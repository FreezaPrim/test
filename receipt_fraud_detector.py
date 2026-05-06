"""
Receipt Fraud Detector
======================
Standalone Streamlit app — run with:
    streamlit run receipt_fraud_detector.py

Dependencies:
    pip install streamlit Pillow numpy scipy matplotlib
"""

import io
import struct
import streamlit as st
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from PIL import Image, ImageChops


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Finding:
    label: str
    severity: str   # "high" | "medium" | "low" | "ok"
    detail: str


@dataclass
class FraudReport:
    risk_score: int
    verdict: str    # "LIKELY FAKE" | "SUSPICIOUS" | "LIKELY GENUINE"
    findings: list[Finding] = field(default_factory=list)
    ela_image: Optional[Image.Image] = None
    noise_image: Optional[Image.Image] = None


# ── ELA ───────────────────────────────────────────────────────────────────────

def _ela(img: Image.Image, quality: int = 90):
    orig = img.convert("RGB")
    buf = io.BytesIO()
    orig.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    resaved = Image.open(buf).convert("RGB")
    diff = ImageChops.difference(orig, resaved)
    arr = np.array(diff, dtype=np.float32)
    amplified = Image.fromarray(np.clip(arr * 15, 0, 255).astype(np.uint8))
    return amplified, float(arr.mean())


def _ela_region_variance(ela_arr: np.ndarray) -> float:
    h, w = ela_arr.shape[:2]
    cell_means = []
    for r in range(6):
        for c in range(4):
            y0, y1 = r * h // 6, (r + 1) * h // 6
            x0, x1 = c * w // 4, (c + 1) * w // 4
            cell_means.append(float(ela_arr[y0:y1, x0:x1].mean()))
    return float(np.std(cell_means))


# ── Noise ─────────────────────────────────────────────────────────────────────

def _noise_map(img: Image.Image):
    from scipy.ndimage import uniform_filter
    arr = np.array(img.convert("L"), dtype=np.float32)
    noise = np.abs(arr - uniform_filter(arr, size=3))
    noise_img = Image.fromarray(np.clip(noise * 8, 0, 255).astype(np.uint8))
    return noise_img, float(noise.std())


def _noise_region_variance(img: Image.Image) -> float:
    from scipy.ndimage import uniform_filter
    arr = np.array(img.convert("L"), dtype=np.float32)
    noise = np.abs(arr - uniform_filter(arr, size=3))
    h, w = noise.shape
    stds = []
    for r in range(6):
        for c in range(4):
            y0, y1 = r * h // 6, (r + 1) * h // 6
            x0, x1 = c * w // 4, (c + 1) * w // 4
            stds.append(float(noise[y0:y1, x0:x1].std()))
    return float(np.std(stds))


# ── Clone detection ───────────────────────────────────────────────────────────

def _clone_score(img: Image.Image, block: int = 16, top_k: int = 200) -> float:
    from scipy.fft import dctn
    gray = np.array(
        img.convert("L").resize((img.width // 2, img.height // 2), Image.LANCZOS),
        dtype=np.float32,
    )
    h, w = gray.shape
    blocks = []
    for y in range(0, h - block, block):
        for x in range(0, w - block, block):
            coeff = dctn(gray[y:y+block, x:x+block], norm="ortho").flatten()[:32]
            blocks.append(coeff)
    if len(blocks) < 2:
        return 0.0
    blocks = np.array(blocks)
    normed = blocks / (np.linalg.norm(blocks, axis=1, keepdims=True) + 1e-8)
    idx = np.random.choice(len(normed), min(top_k, len(normed)), replace=False)
    sims = normed[idx] @ normed.T
    np.fill_diagonal(sims[:, idx], 0)
    return float((sims.max(axis=1) > 0.995).mean())


# ── JPEG helpers ──────────────────────────────────────────────────────────────

def _double_save_score(raw: bytes) -> float:
    dqt = 0
    i = 0
    while i < len(raw) - 1:
        if raw[i] == 0xFF and raw[i+1] == 0xDB:
            dqt += 1
            if i + 3 < len(raw):
                i += 2 + struct.unpack(">H", raw[i+2:i+4])[0]
                continue
        i += 1
    return 1.0 if dqt >= 3 else 0.0


def _jpeg_ghost(img: Image.Image) -> float:
    orig = img.convert("RGB")
    orig_arr = np.array(orig, dtype=np.float32)
    min_ghost = float("inf")
    for q in (60, 70, 80, 85, 90, 95):
        buf = io.BytesIO()
        orig.save(buf, "JPEG", quality=q)
        buf.seek(0)
        recon = np.array(Image.open(buf).convert("RGB"), dtype=np.float32)
        val = ((orig_arr - recon) ** 2).mean(axis=2).std()
        if val < min_ghost:
            min_ghost = val
    return float(min(min_ghost / 30.0, 1.0))


def _parse_metadata(img: Image.Image) -> dict:
    exif_raw = img._getexif() if hasattr(img, "_getexif") else None
    if exif_raw:
        from PIL.ExifTags import TAGS
        exif = {TAGS.get(k, k): v for k, v in exif_raw.items()
                if isinstance(v, (str, int, float, bytes))}
    else:
        exif = {}
    sw = str(exif.get("Software", ""))
    device = f"{exif.get('Make', '')} {exif.get('Model', '')}".strip()
    return {"software": sw, "device": device}


# ── Main analysis ─────────────────────────────────────────────────────────────

def analyze(image_bytes: bytes) -> FraudReport:
    np.random.seed(42)
    findings: list[Finding] = []
    risk = 0

    img = Image.open(io.BytesIO(image_bytes))
    is_jpeg = img.format in ("JPEG", "JPG") or image_bytes[:2] == b"\xff\xd8"

    # 1. ELA
    ela_img, ela_mean = _ela(img)
    ela_var = _ela_region_variance(np.array(ela_img, dtype=np.float32))
    if ela_mean > 12 and ela_var > 18:
        findings.append(Finding("Error Level Analysis", "high",
            f"High ELA error (mean={ela_mean:.1f}, variance={ela_var:.1f}). "
            "Different regions have different compression levels — strong sign of editing."))
        risk += 40
    elif ela_mean > 8 or ela_var > 12:
        findings.append(Finding("Error Level Analysis", "medium",
            f"Moderate ELA anomaly (mean={ela_mean:.1f}, variance={ela_var:.1f})."))
        risk += 20
    else:
        findings.append(Finding("Error Level Analysis", "ok",
            f"ELA is uniform (mean={ela_mean:.1f}, variance={ela_var:.1f}). No splicing detected."))

    # 2. Noise
    noise_img, _ = _noise_map(img)
    noise_var = _noise_region_variance(img)
    if noise_var > 4.0:
        findings.append(Finding("Noise Pattern", "high",
            f"Noise variance very high ({noise_var:.2f}). Inconsistent noise across regions."))
        risk += 30
    elif noise_var > 2.0:
        findings.append(Finding("Noise Pattern", "medium",
            f"Noise variance elevated ({noise_var:.2f}). Possible multi-source blending."))
        risk += 15
    else:
        findings.append(Finding("Noise Pattern", "ok",
            f"Noise is consistent ({noise_var:.2f})."))

    # 3. Clone detection
    clone = _clone_score(img)
    if clone > 0.08:
        findings.append(Finding("Clone Detection", "high",
            f"High block duplication rate ({clone*100:.1f}%). Typical of copy-paste manipulation."))
        risk += 25
    elif clone > 0.03:
        findings.append(Finding("Clone Detection", "medium",
            f"Some duplicate blocks ({clone*100:.1f}%). Possible copy-paste."))
        risk += 10
    else:
        findings.append(Finding("Clone Detection", "ok",
            f"No significant duplication ({clone*100:.1f}%)."))

    # 4. JPEG re-save
    if is_jpeg:
        if _double_save_score(image_bytes):
            findings.append(Finding("JPEG Re-Save", "medium",
                "Multiple JPEG quantisation tables found — image was re-saved in a photo editor."))
            risk += 15
        else:
            findings.append(Finding("JPEG Re-Save", "ok",
                "Normal quantisation tables. No re-save artifact."))

        ghost = _jpeg_ghost(img)
        if ghost > 0.65:
            findings.append(Finding("JPEG Ghost", "high",
                f"Strong ghost signal ({ghost*100:.0f}/100). Content pasted from another JPEG."))
            risk += 20
        elif ghost > 0.40:
            findings.append(Finding("JPEG Ghost", "medium",
                f"Moderate ghost ({ghost*100:.0f}/100)."))
            risk += 10
        else:
            findings.append(Finding("JPEG Ghost", "ok",
                f"No ghost artifact ({ghost*100:.0f}/100)."))

    # 5. Metadata
    meta = _parse_metadata(img)
    sw = meta["software"]
    bad_sw = ["photoshop","gimp","paint","snapseed","lightroom",
              "affinity","pixelmator","canva","picsart","facetune"]
    if any(s in sw.lower() for s in bad_sw):
        findings.append(Finding("Metadata – Software", "high",
            f"Editing software detected: '{sw}'. Bank screenshots are never post-processed."))
        risk += 30
    elif sw:
        findings.append(Finding("Metadata – Software", "low", f"Software tag: '{sw}'."))
        risk += 5
    else:
        findings.append(Finding("Metadata – Software", "ok", "No editing software in metadata."))

    if meta["device"]:
        findings.append(Finding("Metadata – Device", "ok", f"Captured by: {meta['device']}."))
    else:
        findings.append(Finding("Metadata – Device", "low",
            "No device EXIF. Could be a screenshot (normal) or stripped metadata."))

    risk = min(risk, 100)
    verdict = "LIKELY FAKE" if risk >= 55 else "SUSPICIOUS" if risk >= 30 else "LIKELY GENUINE"
    return FraudReport(risk_score=risk, verdict=verdict,
                       findings=findings, ela_image=ela_img, noise_image=noise_img)


# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Receipt Fraud Detector", page_icon="🔍", layout="wide")

st.title("🔍 Receipt Fraud Detector")
st.markdown(
    "Upload a bank transfer or InstaPay receipt image. "
    "The tool will scan it for signs of digital manipulation using 6 forensic techniques."
)

uploaded = st.file_uploader("Upload receipt image (JPEG or PNG)", type=["jpg","jpeg","png"])

if uploaded:
    raw = uploaded.read()
    img = Image.open(io.BytesIO(raw))

    col_orig, col_ela = st.columns(2)
    with col_orig:
        st.markdown("**Original Image**")
        st.image(img, use_column_width=True)

    with st.spinner("Analyzing for tampering…"):
        report = analyze(raw)

    with col_ela:
        st.markdown("**Error Level Analysis (ELA)**")
        st.image(report.ela_image, use_column_width=True)
        st.caption("Brighter = higher compression error. Uniform = untouched.")

    st.markdown("---")

    color = {"LIKELY GENUINE": "#27ae60", "SUSPICIOUS": "#e67e22", "LIKELY FAKE": "#e74c3c"}[report.verdict]
    st.markdown(
        f"""<div style="background:{color};padding:18px 24px;border-radius:10px;text-align:center;">
            <span style="font-size:1.6rem;font-weight:700;color:white;">{report.verdict}</span><br>
            <span style="color:white;font-size:1rem;">Risk Score: {report.risk_score} / 100</span>
        </div>""",
        unsafe_allow_html=True,
    )

    st.markdown("### Detailed Findings")
    sev_icon  = {"high":"🔴","medium":"🟡","low":"🟠","ok":"🟢"}
    sev_order = {"high":0,"medium":1,"low":2,"ok":3}
    for f in sorted(report.findings, key=lambda x: sev_order.get(x.severity, 9)):
        with st.expander(f"{sev_icon.get(f.severity,'⚪')} {f.label}  —  {f.severity.upper()}"):
            st.write(f.detail)

    st.markdown("---")
    st.markdown("**Noise Pattern Map**")
    st.image(report.noise_image, use_column_width=True)
    st.caption("Inconsistent noise across regions can indicate blended or pasted content.")

    st.markdown(
        "> **Disclaimer:** This tool uses image-forensics heuristics and is not definitive legal proof. "
        "Consult your bank or a certified forensic expert for official verification."
    )
else:
    st.info("Upload a receipt image above to begin analysis.")
