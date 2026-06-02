"""Automatic insight alerts for Roma.

After you add data (or run `roma watch`), Roma compares the most recent month
in the data against the month before and flags what a manager would want to
know without asking: detractor spikes, tNPS shifts, and which breakdown segment
moved most. Pure pandas, offline.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from . import database, detractors, timefilter

# A change is "notable" past these thresholds.
REL_THRESHOLD = 10.0   # percent change in detractor count
PP_THRESHOLD = 3.0     # percentage-point change in detractor rate


def _recent_two_months(conn) -> tuple[str, str] | None:
    """Return (latest_month_label, previous_month_label) found in the data."""
    table, nps, idc, datec, names = detractors._detractor_table(conn)
    if not table or not datec:
        return None
    df = pd.read_sql_query(f'SELECT "{datec}" FROM "{table}"', conn)
    dts = pd.to_datetime(df[datec], errors="coerce").dropna()
    if dts.empty:
        return None
    periods = sorted(dts.dt.to_period("M").unique())
    if len(periods) < 2:
        return None
    latest, prev = periods[-1], periods[-2]
    # produce month-name labels timefilter understands, e.g. "April 2026"
    return (latest.strftime("%B %Y"), prev.strftime("%B %Y"))


def generate_alerts(conn) -> dict[str, Any]:
    table, nps, idc, datec, names = detractors._detractor_table(conn)
    if not table:
        return {"error": "No survey table with an NPS/tNPS column found."}
    months = _recent_two_months(conn)
    alerts: list[str] = []

    if months:
        latest, prev = months
        cmp = detractors.compare(conn, latest, prev)
        if "error" not in cmp:
            a, b = cmp["a"], cmp["b"]
            if abs(cmp["rel_pct"]) >= REL_THRESHOLD or abs(cmp["pct_point_delta"]) >= PP_THRESHOLD:
                arrow = "increased" if cmp["direction"] == "up" else "decreased"
                alerts.append(
                    f"Detractors {arrow} {abs(cmp['rel_pct'])}% in {latest} "
                    f"({b['detractors']} -> {a['detractors']}); rate "
                    f"{b['pct']}% -> {a['pct']}%.")
            # which dimension moved most between the two months
            for dim_word in ["agent queue", "short code", "call type", "owner team"]:
                col = detractors.resolve_dimension(dim_word, names)
                if not col:
                    continue
                moved = _dimension_shift(conn, table, nps, col, datec, latest, prev)
                if moved:
                    alerts.append(moved)
                    break  # one dimension call-out is enough for an alert digest
        period_label = f"{latest} vs {prev}"
    else:
        # No time dimension: just flag the overall detractor rate as context.
        c = detractors.count(conn)
        if "error" not in c:
            alerts.append(f"Detractor rate is {c['pct']}% "
                          f"({c['detractors']} of {c['base']}). Add dated data to "
                          f"track month-over-month changes.")
        period_label = "all data"

    if not alerts:
        alerts.append("No notable month-over-month changes detected.")
    return {"period": period_label, "alerts": alerts}


def _dimension_shift(conn, table, nps, col, datec, latest, prev) -> str | None:
    """Find the category whose detractor count moved most between two months."""
    tf_a = timefilter.parse(latest, datec)
    tf_b = timefilter.parse(prev, datec)
    if not tf_a or not tf_b:
        return None

    def counts(where):
        sql = (f'SELECT "{col}" AS k, COUNT(*) AS n FROM "{table}" '
               f'WHERE CAST("{nps}" AS FLOAT) <= 6 AND {where} GROUP BY "{col}"')
        res = database.run_sql(conn, sql)
        return {r["k"]: r["n"] for r in res.get("rows", [])} if "error" not in res else {}

    ca, cb = counts(tf_a["where"]), counts(tf_b["where"])
    best_key, best_delta = None, 0
    for k in set(ca) | set(cb):
        d = ca.get(k, 0) - cb.get(k, 0)
        if abs(d) > abs(best_delta):
            best_key, best_delta = k, d
    if best_key is not None and abs(best_delta) >= 3:
        arrow = "up" if best_delta > 0 else "down"
        return (f"Biggest mover by {col}: '{best_key}' detractors {arrow} "
                f"{abs(best_delta)} ({cb.get(best_key,0)} -> {ca.get(best_key,0)}).")
    return None


def alerts_text(conn) -> str:
    res = generate_alerts(conn)
    if "error" in res:
        return f"Alerts: {res['error']}"
    lines = [f"Roma noticed ({res['period']}):"]
    for a in res["alerts"]:
        lines.append(f"  - {a}")
    return "\n".join(lines)
