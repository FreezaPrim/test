"""User-defined analytics workflow skills for Roma.

Inspired by OpenJarvis SkillManifest
(src/openjarvis/core/skills/manifest.py).

A skill is a named sequence of Roma chat queries stored in a YAML (or
simple text) file under  roma_data/skills/.  Running a skill executes
each step through the engine, prints the results, and optionally saves
the full output as a Word document.

File format (YAML — needs PyYAML, falls back to plain-text if absent):
  name: morning_review
  description: Daily CX health check
  steps:
    - my kpis
    - forecast next 30 days
    - show severity
    - velocity alerts
    - print top detractors by shortcode

Plain-text fallback (one query per line, blank lines / # comments ignored):
  # morning_review.txt
  my kpis
  forecast next 30 days
  show severity

CLI:
  roma skill list             list all skills in roma_data/skills/
  roma skill run <name>       run a skill and print results
  roma skill run <name> --save   also save output as Word doc
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import config

SKILLS_DIR = config.DATA_DIR / "skills"
_YAML_AVAILABLE: bool | None = None   # lazy probe


# ── helpers ────────────────────────────────────────────────────────────────────

def _yaml_available() -> bool:
    global _YAML_AVAILABLE
    if _YAML_AVAILABLE is None:
        try:
            import yaml  # noqa: F401
            _YAML_AVAILABLE = True
        except ImportError:
            _YAML_AVAILABLE = False
    return _YAML_AVAILABLE


def _parse_yaml(text: str) -> dict[str, Any]:
    import yaml  # guaranteed available after _yaml_available() == True
    return yaml.safe_load(text) or {}


def _parse_text(text: str) -> dict[str, Any]:
    """Parse a plain-text skill file (one step per non-blank, non-comment line).

    Name comes from the filename stem (set by Skill.from_file).
    Description is taken from the first comment line, if any.
    """
    lines = [l.strip() for l in text.splitlines()]
    steps = [l for l in lines if l and not l.startswith("#")]
    comments = [l.lstrip("# ") for l in lines if l.startswith("#")]
    desc = comments[0] if comments else ""
    return {"steps": steps, "description": desc}


# ── Skill dataclass ─────────────────────────────────────────────────────────

class Skill:
    """A named, runnable analytics workflow."""

    def __init__(self, name: str, steps: list[str],
                 description: str = "", source: Path | None = None) -> None:
        self.name = name
        self.steps = [s for s in steps if s and str(s).strip()]
        self.description = description
        self.source = source

    def __repr__(self) -> str:
        return f"Skill(name={self.name!r}, steps={len(self.steps)})"

    @classmethod
    def from_file(cls, path: Path) -> "Skill":
        """Load a skill from a .yaml / .yml / .txt file."""
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".yaml", ".yml") and _yaml_available():
            data = _parse_yaml(text)
        else:
            data = _parse_text(text)

        name  = str(data.get("name", path.stem)).lower().replace(" ", "_")
        steps = [str(s) for s in (data.get("steps") or [])]
        desc  = str(data.get("description", ""))
        return cls(name=name, steps=steps, description=desc, source=path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
            "source": str(self.source) if self.source else None,
        }


# ── Skills directory management ───────────────────────────────────────────────

def ensure_skills_dir() -> Path:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    _write_examples()
    return SKILLS_DIR


def _write_examples() -> None:
    """Write example skill files on first run so users have a starting point."""
    morning = SKILLS_DIR / "morning_review.txt"
    if not morning.exists():
        morning.write_text(
            "# Morning CX health-check\n"
            "my kpis\n"
            "forecast next 30 days\n"
            "show severity\n"
            "velocity alerts\n"
            "print top detractors by shortcode\n",
            encoding="utf-8",
        )
    deep = SKILLS_DIR / "deep_dive.txt"
    if not deep.exists():
        deep.write_text(
            "# Full detractor deep-dive\n"
            "my kpis\n"
            "print top detractors by queue\n"
            "toxic combos\n"
            "agent ranking\n"
            "win-back recovery\n"
            "export tnps presentation\n",
            encoding="utf-8",
        )


def list_skills() -> list[Skill]:
    """Return all skills found in the skills directory."""
    ensure_skills_dir()
    skills = []
    for ext in ("*.yaml", "*.yml", "*.txt"):
        for path in sorted(SKILLS_DIR.glob(ext)):
            try:
                skills.append(Skill.from_file(path))
            except Exception:   # noqa: BLE001
                pass
    return skills


def get_skill(name: str) -> Skill | None:
    """Find a skill by name (case-insensitive, underscores = spaces)."""
    target = name.lower().replace(" ", "_").replace("-", "_")
    for skill in list_skills():
        if skill.name == target:
            return skill
        # also match filename stem
        if skill.source and skill.source.stem.lower() == target:
            return skill
    return None


# ── Runner ────────────────────────────────────────────────────────────────────

def run_skill(conn: Any, skill: Skill, *,
              save: bool = False, verbose: bool = True) -> list[tuple[str, str]]:
    """
    Execute every step of a skill through Roma's engine.

    Args:
        conn:    SQLite connection.
        skill:   Skill to run.
        save:    If True, save full output as a Word doc.
        verbose: If True, print step headers and answers.

    Returns:
        List of (question, answer) pairs.
    """
    from . import engine as eng

    log: list[tuple[str, str]] = []

    if verbose:
        print(f"\n  ┌─ Skill: {skill.name} "
              f"{'─ ' * max(0, 40 - len(skill.name))}┐")
        if skill.description:
            print(f"  │  {skill.description}")
        print(f"  │  {len(skill.steps)} steps")
        print(f"  └{'─' * 50}┘\n")

    # We need a minimal conn context; use engine.answer() with use_llm=False
    for i, step in enumerate(skill.steps, 1):
        if verbose:
            print(f"  ── Step {i}/{len(skill.steps)}: {step}")
            print()

        answer = eng.answer(conn, step, use_llm=False, model=None)
        log.append((step, answer))

        if verbose:
            print(answer)
            print()

    if save and log:
        out = _save_skill_output(skill, log)
        if verbose:
            print(f"\n  Skill output saved: {out}")

    return log


def _save_skill_output(skill: Skill, log: list[tuple[str, str]]) -> Path:
    """Save skill run output as a Word document."""
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
        from datetime import datetime

        config.ensure_dirs()
        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = "Arial"
        style.font.size = Pt(11)

        title = doc.add_heading(f"Roma Skill: {skill.name}", level=0)
        for run in title.runs:
            run.font.color.rgb = RGBColor.from_string("E00800")

        doc.add_paragraph(
            f"e& Consumer  |  {datetime.now():%Y-%m-%d %H:%M}  |  "
            f"{len(log)} steps"
        ).italic = True
        if skill.description:
            doc.add_paragraph(skill.description)
        doc.add_paragraph()

        for i, (question, answer) in enumerate(log, 1):
            qp = doc.add_paragraph()
            qr = qp.add_run(f"Step {i}: {question}")
            qr.bold = True
            qr.font.color.rgb = RGBColor.from_string("1A1A1A")
            doc.add_paragraph(answer)
            doc.add_paragraph()

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r"\W+", "_", skill.name)
        out = config.REPORTS_DIR / f"skill_{safe_name}_{stamp}.docx"
        doc.save(out)
        return out
    except Exception as exc:   # noqa: BLE001
        raise RuntimeError(f"Could not save skill output: {exc}") from exc


# ── Skill creation helper ─────────────────────────────────────────────────────

def create_skill(name: str, steps: list[str], description: str = "") -> Skill:
    """Persist a new skill to the skills directory and return it."""
    ensure_skills_dir()
    safe = re.sub(r"\W+", "_", name.lower().strip()).strip("_") or "skill"
    path = SKILLS_DIR / f"{safe}.txt"

    lines = []
    if description:
        lines.append(f"# {description}")
    lines.extend(steps)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return Skill(name=safe, steps=steps, description=description, source=path)
