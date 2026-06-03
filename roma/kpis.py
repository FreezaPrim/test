"""Compute Mohamed's CX KPIs from whatever columns are present.

Detection is by column name + value range, so it adapts to new sheets. Every
KPI is wrapped in its own try/except: if the needed columns aren't there, that
KPI is simply skipped rather than failing the whole run.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from . import database

NPS_HINT = re.compile(r"tnps|nps|recommend|promoter|loyalty", re.IGNORECASE)
CSAT_HINT = re.compile(r"csat|satisfaction", re.IGNORECASE)
REACH_HINT = re.compile(r"call[\W_]*0[\W_]*1|call01|reach", re.IGNORECASE)
SCORE_HINT = re.compile(r"score|rating", re.IGNORECASE)
KEY_HINT = re.compile(r"msisdn|customer|phone|mobile|number|id", re.IGNORECASE)


def _tables(conn) -> dict[str, pd.DataFrame]:
    return {t["table"]: pd.read_sql_query(f'SELECT * FROM "{t["table"]}"', conn)
            for t in database.list_tables(conn)}


def _nps(df: pd.DataFrame, table: str) -> list[dict]:
    out = []
    for col in df.columns:
        if not (NPS_HINT.search(col) or SCORE_HINT.search(col)):
            continue
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(s) < 5 or s.min() < 0 or s.max() > 10:
            continue
        promoters_pct = (s >= 9).mean() * 100
        passives_pct  = ((s >= 7) & (s <= 8)).mean() * 100
        detractors_pct = (s <= 6).mean() * 100
        nps = promoters_pct - detractors_pct
        n = len(s)
        out.append({
            "name": f"tNPS ({col})", "table": table,
            "value": f"{nps:+.0f}",
            "detail": (f"▲ {promoters_pct:.0f}% promoters  "
                       f"○ {passives_pct:.0f}% passives  "
                       f"▼ {detractors_pct:.0f}% detractors  "
                       f"n={n}")})
        out.append({
            "name": f"Promoters ({col})", "table": table,
            "value": f"{promoters_pct:.0f}%",
            "detail": f"{int((s >= 9).sum())} respondents scored 9–10"})
        out.append({
            "name": f"Passives ({col})", "table": table,
            "value": f"{passives_pct:.0f}%",
            "detail": f"{int(((s >= 7) & (s <= 8)).sum())} respondents scored 7–8"})
        out.append({
            "name": f"Detractors ({col})", "table": table,
            "value": f"{detractors_pct:.0f}%  ({int((s <= 6).sum())})",
            "detail": f"scored 0–6  |  n={n}"})
        break  # one NPS-like column per table is enough
    return out


def _csat(df: pd.DataFrame, table: str) -> list[dict]:
    out = []
    for col in df.columns:
        if not CSAT_HINT.search(col):
            continue
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(s) < 5:
            continue
        top = 4 if s.max() <= 5 else 9
        topbox = (s >= top).mean() * 100
        out.append({"name": f"CSAT ({col})", "table": table,
                    "value": f"{s.mean():.2f} avg",
                    "detail": f"top-box {topbox:.0f}% (>= {top}), scale ~1-{int(s.max())}, "
                              f"n={len(s)}"})
        break
    return out


def _reach(df: pd.DataFrame, table: str) -> list[dict]:
    out = []
    for col in df.columns:
        if not REACH_HINT.search(col):
            continue
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(s) < 3 or not set(s.unique()).issubset({0, 1}):
            continue
        out.append({"name": f"Reach Rate ({col})", "table": table,
                    "value": f"{s.mean() * 100:.0f}%",
                    "detail": f"{int(s.sum())} reached of {len(s)} (call happened == 1)"})
        break
    return out


def _contact_rate(conn, tables: dict[str, pd.DataFrame]) -> list[dict]:
    """If a detractor set and another keyed table exist, estimate Contact Rate."""
    links = database.detect_links(conn)
    out = []
    for link in links:
        key = link["column"]
        if not KEY_HINT.search(key):
            continue
        shared = link["shared_by"]
        # find a table with an NPS-like column to define detractors
        for src in shared:
            df = tables[src]
            real_key = next((c for c in df.columns if c.lower() == key), None)
            nps_col = next((c for c in df.columns
                            if NPS_HINT.search(c) or SCORE_HINT.search(c)), None)
            if not real_key or not nps_col:
                continue
            scores = pd.to_numeric(df[nps_col], errors="coerce")
            if scores.dropna().empty or scores.min() < 0 or scores.max() > 10:
                continue
            detractor_keys = set(df.loc[scores <= 6, real_key].dropna())
            if not detractor_keys:
                continue
            for other in shared:
                if other == src:
                    continue
                odf = tables[other]
                okey = next((c for c in odf.columns if c.lower() == key), None)
                if not okey:
                    continue
                contacted = detractor_keys & set(odf[okey].dropna())
                rate = len(contacted) / len(detractor_keys) * 100
                out.append({
                    "name": "Contact Rate",
                    "table": f"{src} -> {other}",
                    "value": f"{rate:.0f}%",
                    "detail": f"{len(contacted)} of {len(detractor_keys)} detractors "
                              f"appear in '{other}' (on {key})"})
            return out  # one detractor source is enough
    return out


def compute(conn) -> list[dict[str, Any]]:
    tables = _tables(conn)
    results: list[dict] = []
    for table, df in tables.items():
        for fn in (_nps, _csat, _reach):
            try:
                results.extend(fn(df, table))
            except Exception:  # noqa: BLE001
                pass
    try:
        results.extend(_contact_rate(conn, tables))
    except Exception:  # noqa: BLE001
        pass
    return results
