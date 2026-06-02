"""Interactive mapping (VLOOKUP-style merge) between two files.

Flow Mohamed asked for:
  1. pick the BASE file  -> Roma lists its columns -> choose the key column
  2. pick the LOOKUP file -> Roma lists its columns -> choose the matching key,
     then choose which columns to ADD onto the base
  3. Roma merges (left join on the keys), loads the result as a new table, and
     also exports an e&-branded Excel file.

Column choices accept either the number Roma shows or the column name.
Runs fully offline (pandas + openpyxl).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from . import config, database


def _read_any(path: str) -> pd.DataFrame:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in {".xlsx", ".xls", ".xlsm"}:
        return pd.read_excel(p)
    if ext == ".csv":
        return pd.read_csv(p)
    if ext == ".tsv":
        return pd.read_csv(p, sep="\t")
    raise ValueError(f"Unsupported file type for mapping: {ext}")


def columns_of(path: str) -> list[str]:
    return list(_read_any(path).columns)


def resolve_choice(choice: str, columns: list[str]) -> str | None:
    """Accept a 1-based number OR a column name (case-insensitive)."""
    c = choice.strip()
    if c.isdigit():
        i = int(c) - 1
        if 0 <= i < len(columns):
            return columns[i]
        return None
    low = {col.lower(): col for col in columns}
    return low.get(c.lower())


def resolve_multi(choices: str, columns: list[str]) -> list[str]:
    """Parse a comma/space list of numbers or names; 'all' = every non-key column."""
    out, seen = [], set()
    for tok in choices.replace(",", " ").split():
        col = resolve_choice(tok, columns)
        if col and col not in seen:
            seen.add(col)
            out.append(col)
    return out


def do_merge(base_file: str, base_key: str, lookup_file: str, lookup_key: str,
             add_cols: list[str], how: str = "left") -> dict[str, Any]:
    """Perform the merge and return the resulting DataFrame + a summary."""
    base = _read_any(base_file)
    lookup = _read_any(lookup_file)
    if base_key not in base.columns:
        return {"error": f"'{base_key}' not in base file."}
    if lookup_key not in lookup.columns:
        return {"error": f"'{lookup_key}' not in lookup file."}
    bad = [c for c in add_cols if c not in lookup.columns]
    if bad:
        return {"error": f"Columns not in lookup file: {', '.join(bad)}"}

    # keep only the key + chosen columns from the lookup side
    right = lookup[[lookup_key] + [c for c in add_cols if c != lookup_key]].copy()
    # rename lookup key to the base key so the join lines up
    if lookup_key != base_key:
        right = right.rename(columns={lookup_key: base_key})
    # avoid clobbering existing base columns
    merged = base.merge(right, on=base_key, how=how, suffixes=("", "_from_lookup"))

    matched = merged[add_cols[0]].notna().sum() if add_cols else 0
    return {"df": merged, "rows": len(merged), "added": add_cols,
            "matched_rows": int(matched), "base_rows": len(base)}


def save_and_load(df: pd.DataFrame, conn, name: str = "mapped") -> dict[str, Any]:
    """Load the merged result into Roma's DB and export an Excel copy."""
    res = database.load_dataframe(conn, name, df, origin_file="mapping")

    from . import export_excel as ex
    from openpyxl import Workbook
    header = list(df.columns)
    disp_rows = [[("" if pd.isna(v) else v) for v in r]
                 for r in df.head(5000).values.tolist()]
    wb = Workbook(); wb.remove(wb.active)
    ex._sheet_from_table(wb, "Mapped", "Mapped result", header, disp_rows)
    out = ex._save(wb, "mapped", "")
    return {"table": res["name"], "file": out, "rows": len(df)}
