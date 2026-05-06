"""
Receipt image forensics module.
Techniques: Error Level Analysis (ELA), metadata inspection, noise analysis, clone detection.
"""

import io
import math
import struct
import zlib
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
from PIL import Image, ImageChops, ImageEnhance, ImageFilter


@dataclass
class Finding:
    label: str
    severity: str  # "high", "medium", "low", "ok"
    detail: str


@dataclass
class FraudReport:
    risk_score: int  # 0-100
    verdict: str     # "LIKELY FAKE", "SUSPICIOUS", "LIKELY GENUINE"
    findings: list[Finding] = field(default_factory=list)
    ela_image: Optional[Image.Image] = None
    noise_image: Optional[Image.Image] = None


# ── ELA ──────────────────────────────────────────────────────────────────────

def _ela(img: Image.Image, quality: int = 90) -> tuple[Image.Image, float]:
    """Re-save as JPEG and compute pixel-level differences."""
    orig = img.convert("RGB")
    buf = io.BytesIO()
    orig.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    resaved = Image.open(buf).convert("RGB")

    diff = ImageChops.difference(orig, resaved)
    arr = np.array(diff, dtype=np.float32)
    scale = 15.0
    amplified = Image.fromarray(np.clip(arr * scale, 0, 255).astype(np.uint8))

    mean_err = float(arr.mean())
    return amplified, mean_err


def _ela_region_variance(ela_arr: np.ndarray) -> float:
    """
    Split the ELA image into a grid and measure variance of mean errors
    across cells. High variance = parts of the image have very different
    error levels, a common sign of splicing.
    """
    h, w = ela_arr.shape[:2]
    rows, cols = 6, 4
    cell_means = []
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r * h // rows, (r + 1) * h // rows
            x0, x1 = c * w // cols, (c + 1) * w // cols
            cell = ela_arr[y0:y1, x0:x1]
            cell_means.append(float(cell.mean()))
    return float(np.std(cell_means))


# ── Noise analysis ────────────────────────────────────────────────────────────

def _noise_map(img: Image.Image) -> tuple[Image.Image, float]:
    """High-pass filter to expose sensor noise. Inconsistencies reveal edits."""
    gray = img.convert("L")
    arr = np.array(gray, dtype=np.float32)
    from scipy.ndimage import uniform_filter
    smooth = uniform_filter(arr, size=3)
    noise = np.abs(arr - smooth)
    noise_img = Image.fromarray(np.clip(noise * 8, 0, 255).astype(np.uint8))
    return noise_img, float(noise.std())


def _noise_region_variance(img: Image.Image) -> float:
    """Variance of local noise levels across the image grid."""
    from scipy.ndimage import uniform_filter
    gray = np.array(img.convert("L"), dtype=np.float32)
    smooth = uniform_filter(gray, size=3)
    noise = np.abs(gray - smooth)

    h, w = noise.shape
    rows, cols = 6, 4
    stds = []
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r * h // rows, (r + 1) * h // rows
            x0, x1 = c * w // cols, (c + 1) * w // cols
            stds.append(float(noise[y0:y1, x0:x1].std()))
    return float(np.std(stds))


# ── Clone / copy-paste detection ──────────────────────────────────────────────

def _clone_score(img: Image.Image, block: int = 16, top_k: int = 200) -> float:
    """
    DCT-based block matching. Duplicate blocks imply copy-paste editing.
    Returns a score 0-1 where higher = more suspicious.
    """
    from scipy.fft import dctn
    gray = np.array(img.convert("L").resize(
        (img.width // 2, img.height // 2), Image.LANCZOS
    ), dtype=np.float32)

    h, w = gray.shape
    blocks = []
    for y in range(0, h - block, block):
        for x in range(0, w - block, block):
            patch = gray[y:y + block, x:x + block]
            coeff = dctn(patch, norm="ortho").flatten()[:32]
            blocks.append(coeff)

    if len(blocks) < 2:
        return 0.0

    blocks = np.array(blocks)
    norms = np.linalg.norm(blocks, axis=1, keepdims=True) + 1e-8
    normed = blocks / norms

    # Sample top_k blocks to keep runtime reasonable
    idx = np.random.choice(len(normed), min(top_k, len(normed)), replace=False)
    sample = normed[idx]
    sims = sample @ normed.T
    np.fill_diagonal(sims[:, idx], 0)

    max_sims = sims.max(axis=1)
    match_ratio = float((max_sims > 0.995).mean())
    return match_ratio


# ── JPEG metadata ─────────────────────────────────────────────────────────────

def _parse_metadata(img: Image.Image, raw_bytes: bytes) -> dict:
    info = {}

    # EXIF
    exif = img._getexif() if hasattr(img, "_getexif") else None
    if exif:
        from PIL.ExifTags import TAGS
        info["exif"] = {TAGS.get(k, k): v for k, v in exif.items()
                        if isinstance(v, (str, int, float, bytes))}
    else:
        info["exif"] = {}

    # Count JPEG APP markers (extra markers can signal re-editing)
    marker_counts: dict[str, int] = {}
    i = 0
    while i < len(raw_bytes) - 1:
        if raw_bytes[i] == 0xFF:
            m = raw_bytes[i + 1]
            key = f"0xFF{m:02X}"
            marker_counts[key] = marker_counts.get(key, 0) + 1
            if m in (0xD8, 0xD9):
                i += 2
                continue
            if i + 3 < len(raw_bytes):
                length = struct.unpack(">H", raw_bytes[i + 2:i + 4])[0]
                i += 2 + length
                continue
        i += 1
    info["markers"] = marker_counts

    # Software tag in EXIF
    sw = info["exif"].get("Software", "")
    info["software"] = str(sw)

    return info


def _double_save_score(raw_bytes: bytes) -> float:
    """
    Count JPEG quantisation tables. Two full sets of tables strongly suggests
    the image was opened and re-saved in an editor (e.g. Photoshop, GIMP).
    Returns 0.0 (ok) or 1.0 (suspicious).
    """
    dqt_count = 0
    i = 0
    while i < len(raw_bytes) - 1:
        if raw_bytes[i] == 0xFF and raw_bytes[i + 1] == 0xDB:
            dqt_count += 1
            if i + 3 < len(raw_bytes):
                length = struct.unpack(">H", raw_bytes[i + 2:i + 4])[0]
                i += 2 + length
                continue
        i += 1
    return 1.0 if dqt_count >= 3 else 0.0


# ── Ghost analysis ────────────────────────────────────────────────────────────

def _jpeg_ghost(img: Image.Image) -> float:
    """
    JPEG ghost: re-save at multiple qualities; regions that were previously
    saved at a different quality will show as anomalies.
    Returns a score 0-1.
    """
    orig = img.convert("RGB")
    orig_arr = np.array(orig, dtype=np.float32)

    min_ghost = np.inf
    for q in (60, 70, 80, 85, 90, 95):
        buf = io.BytesIO()
        orig.save(buf, "JPEG", quality=q)
        buf.seek(0)
        recon = np.array(Image.open(buf).convert("RGB"), dtype=np.float32)
        diff = ((orig_arr - recon) ** 2).mean(axis=2)
        ghost_val = diff.std()
        if ghost_val < min_ghost:
            min_ghost = ghost_val

    return float(min(min_ghost / 30.0, 1.0))


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze(image_bytes: bytes) -> FraudReport:
    np.random.seed(42)
    findings: list[Finding] = []
    risk = 0

    img = Image.open(io.BytesIO(image_bytes))
    is_jpeg = img.format in ("JPEG", "JPG") or image_bytes[:2] == b"\xff\xd8"

    # ── 1. ELA ────────────────────────────────────────────────────────────────
    ela_img, ela_mean = _ela(img)
    ela_arr = np.array(ela_img, dtype=np.float32)
    ela_var = _ela_region_variance(ela_arr)

    ela_finding = None
    if ela_mean > 12 and ela_var > 18:
        ela_finding = Finding(
            "Error Level Analysis",
            "high",
            f"High average ELA error ({ela_mean:.1f}) with large regional variance "
            f"({ela_var:.1f}). Different parts of the image appear to have been saved "
            "at different compression levels — a strong indicator of editing."
        )
        risk += 40
    elif ela_mean > 8 or ela_var > 12:
        ela_finding = Finding(
            "Error Level Analysis",
            "medium",
            f"Moderate ELA error (mean={ela_mean:.1f}, variance={ela_var:.1f}). "
            "Some regions show higher-than-expected compression inconsistency."
        )
        risk += 20
    else:
        ela_finding = Finding(
            "Error Level Analysis",
            "ok",
            f"ELA error levels are uniform across the image (mean={ela_mean:.1f}, "
            f"variance={ela_var:.1f}). No obvious splicing detected."
        )
    findings.append(ela_finding)

    # ── 2. Noise analysis ─────────────────────────────────────────────────────
    noise_img, noise_std = _noise_map(img)
    noise_reg_var = _noise_region_variance(img)

    if noise_reg_var > 4.0:
        findings.append(Finding(
            "Noise Pattern",
            "high",
            f"Noise variance across regions is very high ({noise_reg_var:.2f}). "
            "Genuine photos from one device have consistent noise; this image does not."
        ))
        risk += 30
    elif noise_reg_var > 2.0:
        findings.append(Finding(
            "Noise Pattern",
            "medium",
            f"Noise variance is moderately elevated ({noise_reg_var:.2f}). "
            "Could indicate blending of content from multiple sources."
        ))
        risk += 15
    else:
        findings.append(Finding(
            "Noise Pattern",
            "ok",
            f"Noise pattern is consistent across the image ({noise_reg_var:.2f})."
        ))

    # ── 3. Clone / copy-paste ─────────────────────────────────────────────────
    clone = _clone_score(img)
    if clone > 0.08:
        findings.append(Finding(
            "Clone Detection",
            "high",
            f"Many duplicate image blocks detected ({clone*100:.1f}% match rate). "
            "This pattern is typical of copy-paste manipulation."
        ))
        risk += 25
    elif clone > 0.03:
        findings.append(Finding(
            "Clone Detection",
            "medium",
            f"Some duplicate blocks found ({clone*100:.1f}% match rate). "
            "Possible but not conclusive evidence of copy-paste."
        ))
        risk += 10
    else:
        findings.append(Finding(
            "Clone Detection",
            "ok",
            f"No significant block duplication detected ({clone*100:.1f}%)."
        ))

    # ── 4. JPEG double-save / quantisation tables ─────────────────────────────
    if is_jpeg:
        ds = _double_save_score(image_bytes)
        if ds > 0:
            findings.append(Finding(
                "JPEG Re-Save",
                "medium",
                "Multiple JPEG quantisation table sets found. This is a strong sign "
                "the image was opened and re-saved in a photo editor (Photoshop, GIMP, etc.)."
            ))
            risk += 15
        else:
            findings.append(Finding(
                "JPEG Re-Save",
                "ok",
                "Normal number of JPEG quantisation tables. No re-save artifact detected."
            ))

        # ── 5. JPEG ghost ──────────────────────────────────────────────────────
        ghost = _jpeg_ghost(img)
        if ghost > 0.65:
            findings.append(Finding(
                "JPEG Ghost",
                "high",
                f"Strong JPEG ghost signal ({ghost*100:.0f}/100). Parts of the image "
                "were previously compressed at a different quality — a hallmark of pasting "
                "content from another JPEG."
            ))
            risk += 20
        elif ghost > 0.40:
            findings.append(Finding(
                "JPEG Ghost",
                "medium",
                f"Moderate JPEG ghost ({ghost*100:.0f}/100). Some regions may have "
                "a different compression history."
            ))
            risk += 10
        else:
            findings.append(Finding(
                "JPEG Ghost",
                "ok",
                f"No significant ghost artifact ({ghost*100:.0f}/100)."
            ))

    # ── 6. Metadata ───────────────────────────────────────────────────────────
    meta = _parse_metadata(img, image_bytes)
    sw = meta.get("software", "")
    suspicious_sw = ["photoshop", "gimp", "paint", "snapseed", "lightroom",
                     "affinity", "pixelmator", "canva", "picsart", "facetune"]
    if any(s in sw.lower() for s in suspicious_sw):
        findings.append(Finding(
            "Metadata – Software",
            "high",
            f"Image was processed by editing software: '{sw}'. "
            "Genuine bank screenshots are never post-processed by photo editors."
        ))
        risk += 30
    elif sw:
        findings.append(Finding(
            "Metadata – Software",
            "low",
            f"Software tag present: '{sw}'."
        ))
        risk += 5
    else:
        findings.append(Finding(
            "Metadata – Software",
            "ok",
            "No photo-editing software detected in metadata."
        ))

    exif = meta.get("exif", {})
    if exif.get("Make") or exif.get("Model"):
        device = f"{exif.get('Make','')} {exif.get('Model','')}".strip()
        findings.append(Finding(
            "Metadata – Device",
            "ok",
            f"Captured by: {device}. Camera EXIF present (expected for a real screenshot)."
        ))
    else:
        findings.append(Finding(
            "Metadata – Device",
            "low",
            "No camera/device EXIF. Could be a screenshot (normal) or stripped metadata (suspicious)."
        ))

    # ── Clamp and verdict ─────────────────────────────────────────────────────
    risk = min(risk, 100)

    if risk >= 55:
        verdict = "LIKELY FAKE"
    elif risk >= 30:
        verdict = "SUSPICIOUS"
    else:
        verdict = "LIKELY GENUINE"

    return FraudReport(
        risk_score=risk,
        verdict=verdict,
        findings=findings,
        ela_image=ela_img,
        noise_image=noise_img,
    )
