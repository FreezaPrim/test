"""Roma's memory loader.

Reads the bundled memory (persona, Mohamed's work summary, the CX playbook, and
the KPI glossary) plus any user-added memory files dropped into
roma_data/memory/. This is what makes Roma feel like it already knows Mohamed's
world the moment it starts.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import config

PKG_MEMORY = Path(__file__).resolve().parent / "memory"
USER_MEMORY = config.DATA_DIR / "memory"


def _read(name: str) -> str:
    for base in (USER_MEMORY, PKG_MEMORY):
        p = base / name
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    return ""


@lru_cache(maxsize=1)
def profile_text() -> str:
    return _read("profile.md")


@lru_cache(maxsize=1)
def work_summary_text() -> str:
    return _read("work_summary.md")


@lru_cache(maxsize=1)
def playbook_text() -> str:
    return _read("cx_playbook.md")


@lru_cache(maxsize=1)
def kpi_glossary() -> dict:
    raw = _read("kpi_glossary.json")
    if not raw:
        return {"kpis": [], "column_roles": {}}
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"kpis": [], "column_roles": {}}


def welcome_name() -> str:
    """Pull the user's name out of the profile, default 'Mohamed'."""
    for line in profile_text().splitlines():
        if "**Name:**" in line:
            return line.split("**Name:**", 1)[1].strip()
    return "Mohamed"


def extra_memory_files() -> list[str]:
    """Names of any user-added memory files (so Roma can mention them)."""
    if not USER_MEMORY.exists():
        return []
    return sorted(p.name for p in USER_MEMORY.glob("*")
                  if p.is_file() and p.name not in {
                      "profile.md", "work_summary.md", "cx_playbook.md",
                      "kpi_glossary.json"})


def add_memory_file(path: str) -> str:
    """Copy a file into Roma's user memory; return the stored name."""
    src = Path(path)
    USER_MEMORY.mkdir(parents=True, exist_ok=True)
    dest = USER_MEMORY / src.name
    dest.write_text(Path(src).read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8")
    return dest.name


def persona_system_prompt() -> str:
    """Compact persona + knowledge for the optional local chat model."""
    pieces = [profile_text().strip()]
    pb = playbook_text().strip()
    if pb:
        pieces.append("Reference benchmarks and practice:\n" + pb)
    ws = work_summary_text().strip()
    if ws:
        # Keep it bounded so small local models aren't overwhelmed.
        pieces.append("Mohamed's own methods (source of truth, condensed):\n"
                      + ws[:6000])
    pieces.append("Answer ONLY from the findings you are given plus this "
                  "knowledge. Never invent numbers. Be concise and practical.")
    return "\n\n".join(pieces)
