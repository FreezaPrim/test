"""Professional statistical analysis for Roma (pure pandas/numpy, offline).

Implements the practice from Anthropic's statistical-analysis skill: pick the
right measure of center, report mean AND median for business metrics, describe
distribution shape, give percentiles, find correlations, and - importantly -
flag when a finding is on thin ice (small samples, skew, low effect).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import analyst, database

# Below this many rows, Roma warns the finding is a hypothesis, not a conclusion.
SMALL_SAMPLE = 30


def _numeric_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns
            if pd.api.types.is_numeric_dtype(df[c]) and not analyst.ID_LIKE.search(c)]


def describe_column(df: pd.DataFrame, col: str) -> dict[str, Any]:
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    n = len(s)
    if n == 0:
        return {"column": col, "error": "no numeric values"}
    mean, median = float(s.mean()), float(s.median())
    std = float(s.std()) if n > 1 else 0.0
    skew = float(s.skew()) if n > 2 else 0.0
    shape = ("right-skewed" if skew > 0.5 else "left-skewed" if skew < -0.5
             else "roughly symmetric")
    # outliers via IQR
    q1, q3 = float(s.quantile(.25)), float(s.quantile(.75))
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = int(((s < lo) | (s > hi)).sum())
    return {
        "column": col, "n": n, "mean": round(mean, 2), "median": round(median, 2),
        "std": round(std, 2), "min": round(float(s.min()), 2),
        "max": round(float(s.max()), 2),
        "p25": round(q1, 2), "p75": round(q3, 2),
        "p90": round(float(s.quantile(.90)), 2),
        "p95": round(float(s.quantile(.95)), 2),
        "shape": shape, "skew": round(skew, 2), "outliers": outliers,
        "mean_median_gap": round(abs(mean - median), 2),
    }


def describe_table(conn, table: str | None = None) -> dict[str, Any]:
    tables = [t["table"] for t in database.list_tables(conn)]
    if not tables:
        return {"error": "No data loaded."}
    table = table or tables[0]
    df = pd.read_sql_query(f'SELECT * FROM "{table}"', conn)
    cols = _numeric_cols(df)
    if not cols:
        return {"error": f"No numeric columns in '{table}'."}
    return {"table": table, "rows": len(df),
            "columns": [describe_column(df, c) for c in cols],
            "small_sample": len(df) < SMALL_SAMPLE}


def correlations(conn, table: str | None = None, top: int = 10) -> dict[str, Any]:
    tables = [t["table"] for t in database.list_tables(conn)]
    if not tables:
        return {"error": "No data loaded."}
    table = table or tables[0]
    df = pd.read_sql_query(f'SELECT * FROM "{table}"', conn)
    num = df[_numeric_cols(df)]
    if num.shape[1] < 2:
        return {"error": f"Need 2+ numeric columns in '{table}' to correlate."}
    corr = num.corr(numeric_only=True)
    pairs = []
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if pd.notna(r):
                pairs.append((cols[i], cols[j], float(r)))
    pairs.sort(key=lambda p: abs(p[2]), reverse=True)
    strength = lambda r: ("strong" if abs(r) >= .6 else "moderate" if abs(r) >= .3
                          else "weak")
    return {"table": table, "rows": len(df),
            "pairs": [{"a": a, "b": b, "r": round(r, 2),
                       "strength": strength(r),
                       "direction": "positive" if r > 0 else "negative"}
                      for a, b, r in pairs[:top]],
            "small_sample": len(df) < SMALL_SAMPLE}


def summary_text(conn, table: str | None = None) -> str:
    d = describe_table(conn, table)
    if "error" in d:
        return f"Stats: {d['error']}"
    lines = [f"Descriptive statistics for '{d['table']}' ({d['rows']} rows):"]
    for c in d["columns"]:
        if "error" in c:
            continue
        line = (f"  - {c['column']}: mean {c['mean']}, median {c['median']}, "
                f"range {c['min']}-{c['max']}, {c['shape']}")
        if c["mean_median_gap"] > c["std"] * 0.5 and c["std"] > 0:
            line += " (mean & median diverge - data is skewed; trust the median)"
        if c["outliers"]:
            line += f", {c['outliers']} outliers"
        lines.append(line)
    if d["small_sample"]:
        lines.append(f"  Note: small sample (<{SMALL_SAMPLE} rows) - treat as "
                     f"indicative, not conclusive.")
    return "\n".join(lines)
