"""Data onboarding: make sense of new sheets, whatever their format.

When Mohamed adds data Roma hasn't seen, it guesses what each column is, asks
about the ones it can't place, and proposes how files link together. Answers are
saved to roma_data/learned/schema_notes.json so Roma remembers next time.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from . import config, database, knowledge

SCHEMA_NOTES = config.LEARNED_DIR / "schema_notes.json"


def _roles() -> dict[str, list[str]]:
    return knowledge.kpi_glossary().get("column_roles", {})


def _kpi_match(col: str) -> str | None:
    name = col.lower()
    for kpi in knowledge.kpi_glossary().get("kpis", []):
        for alias in kpi["aliases"]:
            if alias.lower() in name:
                return kpi["name"]
    return None


def classify_column(col: str, dtype: str, sample_vals: list) -> dict[str, Any]:
    roles = _roles()
    low = col.lower()
    kpi = _kpi_match(col)
    if any(h in low for h in roles.get("id_like", [])):
        role = "id / key"
    elif any(h in low for h in roles.get("date_like", [])):
        role = "date"
    elif any(h in low for h in roles.get("text_like", [])):
        role = "text / VOC"
    elif kpi:
        role = f"metric ({kpi})"
    elif "INT" in dtype.upper() or "REAL" in dtype.upper():
        role = "number"
    else:
        nun = len({str(v) for v in sample_vals if v is not None})
        role = "category" if nun and nun <= max(2, len(sample_vals)) else "unknown"
    return {"column": col, "role": role, "kpi": kpi}


def classify_tables(conn) -> list[dict[str, Any]]:
    out = []
    for t in database.list_tables(conn, sample_rows=8):
        sample = t["sample"]
        cols = []
        for c in t["columns"]:
            vals = [row.get(c["name"]) for row in sample]
            cols.append(classify_column(c["name"], c["type"], vals))
        out.append({"table": t["table"], "rows": t["row_count"], "columns": cols})
    return out


def suggest_text(conn) -> str:
    """Short, non-interactive read-out shown right after `roma add`."""
    info = classify_tables(conn)
    links = database.detect_links(conn)
    lines = ["What I think your data is:"]
    for t in info:
        lines.append(f"  {t['table']} ({t['rows']} rows):")
        for c in t["columns"]:
            tag = f" [{c['role']}]" if c["role"] != "number" else ""
            lines.append(f"     - {c['column']}{tag}")
    if links:
        lines.append("I can link these files on: " +
                     ", ".join(l["column"] for l in links))
    unknown = [f"{t['table']}.{c['column']}"
               for t in info for c in t["columns"] if c["role"] == "unknown"]
    if unknown:
        lines.append("I'm not sure about: " + ", ".join(unknown) +
                     "  ->  run 'roma onboard' to tell me.")
    else:
        lines.append("Run 'roma onboard' to confirm the target metric and links.")
    return "\n".join(lines)


def _ask(prompt: str, default: str = "") -> str:
    try:
        ans = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return ans or default


def interactive_onboard(conn) -> dict[str, Any]:
    """Walk the user through confirming roles, target, and join keys."""
    info = classify_tables(conn)
    links = database.detect_links(conn)
    notes = _load_notes()

    print("\nLet's make sense of your data. Press Enter to accept a suggestion.\n")

    # 1) unknown columns
    for t in info:
        for c in t["columns"]:
            if c["role"] == "unknown":
                ans = _ask(f"  What is '{t['table']}.{c['column']}'? "
                           f"(id / metric / category / date / text / skip) > ", "skip")
                if ans != "skip":
                    notes.setdefault("column_roles", {})[f"{t['table']}.{c['column']}"] = ans

    # 2) target metric
    from .analyst import infer_target
    tgt_table, tgt = infer_target(conn)
    suggestion = tgt or "(none detected)"
    ans = _ask(f"  Your main metric to analyse [{suggestion}] > ", suggestion)
    if ans and ans != "(none detected)":
        notes["target"] = ans

    # 3) join keys
    if links:
        key_list = ", ".join(l["column"] for l in links)
        ans = _ask(f"  Link files on these keys? [{key_list}] (Enter=yes / type a column) > ",
                   "")
        notes["join_keys"] = [ans] if ans else [l["column"] for l in links]
    else:
        ans = _ask("  No shared columns detected. Which column links your files? "
                   "(blank to skip) > ", "")
        if ans:
            notes["join_keys"] = [ans]

    notes["onboarded"] = True
    _save_notes(notes)
    print("\nSaved. I'll remember this. Ask me things, or run 'roma report'.")
    return notes


def _load_notes() -> dict[str, Any]:
    if SCHEMA_NOTES.exists():
        try:
            return json.loads(SCHEMA_NOTES.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_notes(notes: dict) -> None:
    config.ensure_dirs()
    SCHEMA_NOTES.write_text(json.dumps(notes, indent=2), encoding="utf-8")
