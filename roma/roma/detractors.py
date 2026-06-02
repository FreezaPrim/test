"""Detractor analytics for Roma: count, breakdowns, and repeat detractors.

A detractor is a survey row whose NPS-like score is 0-6 (Mohamed's definition).
Every function accepts an optional time window and an optional dimension, so the
same code answers "how many detractors in April", "detractors by agent queue",
"detractors by short code", and "repeated detractors".
"""

from __future__ import annotations

import re
from typing import Any

from . import database, timefilter

NPS_HINT = re.compile(r"tnps|nps|recommend|promoter|q1_answer", re.IGNORECASE)
ID_HINT = re.compile(r"msisdn|mobile|phone|customer|caller", re.IGNORECASE)
# Common dimension columns, mapped from the words people use to ask for them.
DIM_SYNONYMS = {
    "agent queue": ["queue", "agent_queue", "agents_queue", "agent's_queue"],
    "queue": ["queue", "agent_queue"],
    "short code": ["short_code", "shortcode", "short", "code"],
    "short_code": ["short_code", "shortcode"],
    "call type": ["call_type", "calltype"],
    "owner team": ["owner_team", "ownerteam", "team"],
    "root cause": ["root_cause", "rootcause", "cause"],
    "product": ["prod_type", "product", "product_type"],
    "channel": ["channel"],
}


def _detractor_table(conn):
    """Find the survey table + its NPS column + id column + date column."""
    for t in database.list_tables(conn):
        names = [c["name"] for c in t["columns"]]
        nps = next((c for c in names if NPS_HINT.search(c)), None)
        if not nps:
            continue
        idc = next((c for c in names if ID_HINT.search(c)), None)
        datec = timefilter.find_date_column(names)
        return t["table"], nps, idc, datec, names
    return None, None, None, None, []


def resolve_dimension(name_or_word: str, columns: list[str]) -> str | None:
    """Map a user word like 'agent queue' or 'short code' to a real column."""
    low = name_or_word.lower().strip()
    cand = DIM_SYNONYMS.get(low, [low.replace(" ", "_"), low.replace(" ", "")])
    lowers = {c.lower(): c for c in columns}
    for w in cand:
        if w in lowers:
            return lowers[w]
    # contains match
    for c in columns:
        cl = c.lower()
        if any(w in cl for w in cand):
            return c
    return None


def _where(nps: str, time_clause: str | None) -> str:
    cond = f'CAST("{nps}" AS FLOAT) <= 6'
    return f"{cond} AND {time_clause}" if time_clause else cond


def count(conn, time_q: str = "") -> dict[str, Any]:
    table, nps, idc, datec, names = _detractor_table(conn)
    if not table:
        return {"error": "No survey table with an NPS/tNPS column found."}
    tf = timefilter.parse(time_q, datec) if (time_q and datec) else None
    where = _where(nps, tf["where"] if tf else None)
    total = database.run_sql(conn, f'SELECT COUNT(*) AS n FROM "{table}"')["rows"][0]["n"]
    res = database.run_sql(conn, f'SELECT COUNT(*) AS n FROM "{table}" WHERE {where}')
    if "error" in res:
        return res
    det = res["rows"][0]["n"]
    base = database.run_sql(conn,
        f'SELECT COUNT(*) AS n FROM "{table}"' + (f' WHERE {tf["where"]}' if tf else ""))
    base_n = base["rows"][0]["n"] if "error" not in base else total
    pct = (det / base_n * 100) if base_n else 0
    return {"table": table, "nps": nps, "detractors": det, "base": base_n,
            "pct": round(pct, 1), "period": tf["label"] if tf else "all data",
            "date_col": datec}


def breakdown(conn, dimension_word: str, time_q: str = "") -> dict[str, Any]:
    table, nps, idc, datec, names = _detractor_table(conn)
    if not table:
        return {"error": "No survey table with an NPS/tNPS column found."}
    dim = resolve_dimension(dimension_word, names)
    if not dim:
        return {"error": f"Couldn't find a column for '{dimension_word}'. "
                         f"Available: {', '.join(names)}"}
    tf = timefilter.parse(time_q, datec) if (time_q and datec) else None
    where = _where(nps, tf["where"] if tf else None)
    sql = (f'SELECT "{dim}" AS "{dim}", COUNT(*) AS detractors FROM "{table}" '
           f'WHERE {where} GROUP BY "{dim}" ORDER BY detractors DESC LIMIT 25')
    res = database.run_sql(conn, sql)
    if "error" in res:
        return res
    return {"table": table, "dimension": dim, "rows": res["rows"],
            "columns": res["columns"], "period": tf["label"] if tf else "all data"}


def repeated(conn, time_q: str = "") -> dict[str, Any]:
    table, nps, idc, datec, names = _detractor_table(conn)
    if not table:
        return {"error": "No survey table with an NPS/tNPS column found."}
    if not idc:
        return {"error": "No customer id column (MSISDN/phone) found to detect repeats."}
    tf = timefilter.parse(time_q, datec) if (time_q and datec) else None
    where = _where(nps, tf["where"] if tf else None)
    sql = (f'SELECT "{idc}" AS customer, COUNT(*) AS times FROM "{table}" '
           f'WHERE {where} GROUP BY "{idc}" HAVING COUNT(*) > 1 '
           f'ORDER BY times DESC LIMIT 25')
    res = database.run_sql(conn, sql)
    if "error" in res:
        return res
    n_sql = (f'SELECT COUNT(*) AS n FROM (SELECT "{idc}" FROM "{table}" '
             f'WHERE {where} GROUP BY "{idc}" HAVING COUNT(*) > 1)')
    n = database.run_sql(conn, n_sql)["rows"][0]["n"]
    return {"table": table, "id": idc, "repeat_count": n, "rows": res["rows"],
            "columns": res["columns"], "period": tf["label"] if tf else "all data"}


def full_report(conn, time_q: str = "", dimensions: list[str] | None = None
                ) -> dict[str, Any]:
    """Everything at once: count + breakdowns by each dimension + repeats."""
    table, nps, idc, datec, names = _detractor_table(conn)
    if not table:
        return {"error": "No survey table with an NPS/tNPS column found."}
    # default dimensions: try the CX-typical ones that actually exist
    if not dimensions:
        wanted = ["agent queue", "short code", "call type", "owner team", "root cause"]
        dimensions = [w for w in wanted if resolve_dimension(w, names)]
    out: dict[str, Any] = {"count": count(conn, time_q), "breakdowns": [],
                           "repeated": repeated(conn, time_q)}
    for w in dimensions:
        b = breakdown(conn, w, time_q)
        if "error" not in b:
            out["breakdowns"].append(b)
    return out


def compare(conn, period_a: str, period_b: str) -> dict[str, Any]:
    """Compare detractor count/rate between two time windows."""
    a = count(conn, period_a)
    b = count(conn, period_b)
    if "error" in a:
        return a
    if "error" in b:
        return b
    delta = a["detractors"] - b["detractors"]
    pct_delta = a["pct"] - b["pct"]
    direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
    rel = (delta / b["detractors"] * 100) if b["detractors"] else 0.0
    return {
        "a": a, "b": b, "delta": delta, "rel_pct": round(rel, 1),
        "pct_point_delta": round(pct_delta, 1), "direction": direction,
        "nps": a["nps"],
    }
