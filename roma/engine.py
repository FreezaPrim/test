"""The chat engine: turns a natural-language question into an answer.

Strategy (all offline):
1. Match the question to an analyst capability (drivers / segments / trend /
   anomalies / metric-by-dimension / summary) and compute the real answer.
2. If a local Ollama model is available, use it to phrase the answer naturally
   and to handle open-ended questions, grounded in the analyst's findings.
   If not, return the structured result directly.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from . import analyst, database, knowledge, kpis, localchat


def _answer_kpis(conn, q: str) -> str | None:
    if not re.search(r"kpi|tnps|nps|csat|detractor|reach|contact|promoter|score card|scorecard", q):
        return None
    res = kpis.compute(conn)
    if not res:
        return None
    lines = ["CX KPIs from your data:"]
    for k in res:
        lines.append(f"  - {k['name']}: {k['value']}  ({k['detail']}) [{k['table']}]")
    return "\n".join(lines)


def _format_table(cols: list[str], rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    widths = {c: max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    head = "  ".join(str(c).ljust(widths[c]) for c in cols)
    line = "  ".join("-" * widths[c] for c in cols)
    body = "\n".join("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols)
                     for r in rows)
    return f"{head}\n{line}\n{body}"


def _format_bordered_table(cols: list[str], rows: list[list],
                            title: str = "", max_rows: int = 50) -> str:
    """Render a Unicode box-drawing bordered table for terminal display."""
    display = rows[:max_rows]
    if not cols:
        return "(no data)"
    widths = []
    for i, c in enumerate(cols):
        col_max = max((len(str(r[i])) if i < len(r) else 0) for r in display) if display else 0
        widths.append(max(len(str(c)), col_max, 1))

    def _row(cells: list) -> str:
        return "│ " + " │ ".join(
            str(cells[i] if i < len(cells) else "").ljust(widths[i])
            for i in range(len(cols))
        ) + " │"

    top = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    mid = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    bot = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"

    out = []
    if title:
        out.append(f"  {title}")
    out += [top, _row(cols), mid]
    for r in display:
        out.append(_row(r))
    out.append(bot)
    if len(rows) > max_rows:
        out.append(f"  … {len(rows) - max_rows} more rows not shown  (type 'export' to save all)")
    return "\n".join(out)


def _all_columns(conn) -> dict[str, str]:
    """Map lower-case column name -> real name (first occurrence)."""
    out: dict[str, str] = {}
    for t in database.list_tables(conn):
        for c in t["columns"]:
            out.setdefault(c["name"].lower(), c["name"])
    return out


# ----------------------- structured answer builders ------------------------ #

def _answer_drivers(conn, q: str) -> str | None:
    if not re.search(r"driv|influenc|affect|impact|predict|caus|why|factor|"
                     r"\bmake[s]?\b|reason|behind|go up|up or down|main(ly)?\b", q):
        return None
    cols = _all_columns(conn)
    target = next((cols[w] for w in re.findall(r"[a-z_]+", q) if w in cols), None)
    res = analyst.drivers(conn, target)
    if "error" in res:
        return f"Drivers: {res['error']}"
    lines = [f"What most influences '{res['target']}' "
             f"({res['model']}, {res['score_metric']}={res['score']}, "
             f"{res['rows_used']} rows):"]
    for f in res["features"]:
        eff = f" (more = {f['effect']} {res['target']})" if f.get("effect") else ""
        lines.append(f"  - {f['feature']}: importance {f['importance']}{eff}")
    return "\n".join(lines)


def _answer_segments(conn, q: str) -> str | None:
    if not re.search(r"segment|cluster|group|persona|tier", q):
        return None
    res = analyst.segments(conn)
    if "error" in res:
        return f"Segments: {res['error']}"
    lines = [f"Found {res['k']} segments in '{res['table']}':"]
    for s in res["segments"]:
        avgs = ", ".join(f"{k}={v}" for k, v in s["averages"].items())
        lines.append(f"  - Segment {s['segment']} ({s['size']} rows): {avgs}")
    return "\n".join(lines)


def _answer_trend(conn, q: str) -> str | None:
    if not re.search(r"trend|over time|trajector|increas|decreas|rising|falling|month|grow", q):
        return None
    cols = _all_columns(conn)
    metric = next((cols[w] for w in re.findall(r"[a-z_]+", q) if w in cols), None)
    res = analyst.trend(conn, metric)
    if "error" in res:
        return f"Trend: {res['error']}"
    lines = ["Trends over time:"]
    for t in res["trends"]:
        lines.append(f"  - {t['metric']} is {t['direction']} over {t['over']} "
                     f"({t['first']} -> {t['last']})")
    return "\n".join(lines)


def _answer_anomalies(conn, q: str) -> str | None:
    if not re.search(r"anomal|outlier|unusual|weird|strange|spike|odd", q):
        return None
    res = analyst.anomalies(conn)
    if "error" in res:
        return f"Anomalies: {res['error']}"
    lines = ["Unusual rows:"]
    for a in res["anomalies"]:
        vals = ", ".join(f"{k}={v}" for k, v in a["values"].items())
        lines.append(f"  - {a['table']} row {a['row']}: {vals}")
    return "\n".join(lines)


def _answer_identity(conn, q: str) -> str | None:
    if not re.search(r"who are you|what are you|your name|about you|introduce|"
                     r"what can you do|what do you do|help me|capabilities", q):
        return None
    name = knowledge.welcome_name()
    return (f"I'm Roma - {name}'s senior Customer Experience analyst, running "
            f"fully on this machine. I know your e& CX work: tNPS/NPS, CSAT, FCR, "
            f"detractors, Contact & Reach rates, win-back, severity and QA scoring, "
            f"and I apply your own formulas.\n"
            f"Ask me things like:\n"
            f"  --- TNPS DASHBOARD ---\n"
            f"  - tnps dashboard / run tnps / full report  (30-sheet Excel)\n"
            f"  - forecast / predict next 30 days\n"
            f"  - toxic combos / root cause\n"
            f"  - velocity alerts / spikes\n"
            f"  - cohort analysis / recovery rate\n"
            f"  - agent ranking / peer benchmark\n"
            f"  - nps waterfall / top bottom queues\n"
            f"  - channel comparison / fcr impact\n"
            f"  - hour pattern / day of week\n"
            f"  --- CORE ANALYTICS ---\n"
            f"  - what drives tnps?            (key drivers, ranked)\n"
            f"  - my kpis / how many detractors\n"
            f"  - detractors by call_type      (breakdown by any column)\n"
            f"  - print top detractors by shortcode  (bordered table)\n"
            f"  - join mapping                 (pick a mapping file, join it, print preview)\n"
            f"  - repeat callers / repeated customers\n"
            f"  - top owner_team               (most frequent values)\n"
            f"  - show segments / any anomalies\n"
            f"  - average <metric> by <column>\n"
            f"And I learn from you: if I get a question wrong, say \"I meant "
            f"drivers\" or teach me a word with \"churn means detractor\", and "
            f"I'll remember it next time.")


def _answer_repeat(conn, q: str) -> str | None:
    if not re.search(r"repeat|repeated|recurring|multiple times|came back|again", q):
        return None
    for t in database.list_tables(conn):
        key = next((c["name"] for c in t["columns"]
                    if re.search(r"msisdn|phone|mobile|customer|caller|number|account",
                                 c["name"], re.IGNORECASE)), None)
        if not key:
            continue
        sql = (f'SELECT "{key}" AS id, COUNT(*) AS times FROM "{t["table"]}" '
               f'GROUP BY "{key}" HAVING COUNT(*) > 1 ORDER BY times DESC LIMIT 15')
        res = database.run_sql(conn, sql)
        if "error" in res or not res.get("rows"):
            continue
        total_sql = (f'SELECT COUNT(*) AS n FROM (SELECT "{key}" FROM "{t["table"]}" '
                     f'GROUP BY "{key}" HAVING COUNT(*) > 1)')
        nrep = database.run_sql(conn, total_sql)["rows"][0]["n"]
        lines = [f"Repeat customers in '{t['table']}' (by {key}): "
                 f"{nrep} appear more than once. Top:"]
        lines.append(_format_table(res["columns"], res["rows"]))
        return "\n".join(lines)
    return ("I couldn't find a customer/phone id column to detect repeats. "
            "Tell me which column identifies a customer with 'roma onboard'.")


def _answer_top_values(conn, q: str) -> str | None:
    if not re.search(r"\btop\b|most common|most frequent|biggest|highest count|"
                     r"short ?codes?|leading", q):
        return None
    cols = _all_columns(conn)
    words = re.findall(r"[a-z_0-9]+", q)
    col = next((cols[w] for w in words if w in cols), None)
    if not col:
        return None
    for t in database.list_tables(conn):
        names = {c["name"] for c in t["columns"]}
        if col not in names:
            continue
        sql = (f'SELECT "{col}" AS value, COUNT(*) AS count FROM "{t["table"]}" '
               f'GROUP BY "{col}" ORDER BY count DESC LIMIT 10')
        res = database.run_sql(conn, sql)
        if "error" in res or not res.get("rows"):
            continue
        return f"Top '{col}' in {t['table']}:\n" + _format_table(res["columns"], res["rows"])
    return None


def _answer_print_top(conn, q: str) -> str | None:
    """'print top detractors by X' / 'show top X breakdown' → bordered table."""
    if not re.search(r"\bprint\b|\bshow\b|\blist\b|\bdisplay\b|\btable\b", q):
        return None
    if not re.search(r"top|detractor|breakdown|worst|ranked", q):
        return None

    from . import tnps_analytics as ta
    from . import detractors as det

    # build a keyword → actual column name lookup from TNPS col detection
    _, tnps_cols = ta.load_tnps_df(conn)
    dim_map: dict[str, str] = {}
    if isinstance(tnps_cols, dict):
        for key, col in tnps_cols.items():
            if col:
                alias = key.lower().replace("_", " ")
                dim_map[alias] = col
                dim_map[col.lower()] = col
                dim_map[col.lower().replace("_", " ")] = col

    # also include all currently loaded columns
    for col_lower, col_real in _all_columns(conn).items():
        dim_map.setdefault(col_lower, col_real)
        dim_map.setdefault(col_lower.replace("_", " "), col_real)

    # strip noise words and look for a column name in what remains
    noise = r"\b(print|show|list|display|table|top|detractor[s]?|breakdown|worst|ranked|by|the|in|of|for)\b"
    q_clean = re.sub(noise, " ", q).strip()
    dim_col = None
    # try longest match first to prefer "owner team" over "owner"
    candidates = sorted(dim_map.keys(), key=len, reverse=True)
    for kw in candidates:
        if kw and kw in q_clean:
            dim_col = dim_map[kw]
            break
    # fallback: any word in q_clean that matches a column
    if not dim_col:
        for word in re.findall(r"[a-z][a-z_0-9]*", q_clean):
            if word in dim_map:
                dim_col = dim_map[word]
                break

    if not dim_col:
        return None

    # find which loaded table has this column
    table, nps, idc, datec, names = det._detractor_table(conn)
    found_table = table
    for t in database.list_tables(conn):
        if any(c["name"] == dim_col for c in t["columns"]):
            found_table = t["table"]
            break

    if not found_table:
        return None

    if nps:
        sql = (f'SELECT COALESCE(CAST("{dim_col}" AS TEXT), "(blank)") AS "{dim_col}", '
               f'COUNT(*) AS Detractors '
               f'FROM "{found_table}" '
               f'WHERE CAST("{nps}" AS FLOAT) <= 6 '
               f'GROUP BY "{dim_col}" ORDER BY Detractors DESC LIMIT 25')
    else:
        sql = (f'SELECT COALESCE(CAST("{dim_col}" AS TEXT), "(blank)") AS "{dim_col}", '
               f'COUNT(*) AS Count '
               f'FROM "{found_table}" WHERE "{dim_col}" IS NOT NULL '
               f'GROUP BY "{dim_col}" ORDER BY Count DESC LIMIT 25')

    res = database.run_sql(conn, sql)
    if "error" in res or not res.get("rows"):
        return None

    col2 = "Detractors" if nps else "Count"
    rows = [[str(r.get(dim_col, r.get("dimension", ""))), str(r.get(col2, 0))]
            for r in res["rows"]]
    return _format_bordered_table(
        [dim_col, col2], rows,
        title=f"Top by {dim_col}  ·  table: {found_table}")


def _fmt_breakdown(b: dict) -> str:
    head = f"Detractors by {b['dimension']} ({b['period']}):"
    return head + "\n" + _format_table(b["columns"], b["rows"])


def _answer_detractors(conn, q: str) -> str | None:
    """One entry point for all detractor questions: count, breakdowns, repeats."""
    from . import detractors, nlu
    toks = nlu.normalise(q)
    # trigger if the raw text OR the normalised tokens point to detractors
    if not (re.search(r"detractor|unhappy|dissatisf|unhappiest|angry|complaint", q)
            or "detractor" in toks):
        return None
    table, nps, idc, datec, names = detractors._detractor_table(conn)
    if not table:
        return "I couldn't find a survey table with an NPS/tNPS column."

    # repeated detractors?
    if re.search(r"repeat|recurring|again|multiple|مكرر|متكرر", q):
        r = detractors.repeated(conn, q)
        if "error" in r:
            return f"Repeated detractors: {r['error']}"
        head = (f"Repeated detractors ({r['period']}): {r['repeat_count']} customers "
                f"were detractors more than once (by {r['id']}). Top:")
        return head + "\n" + _format_table(r["columns"], r["rows"])

    # find any dimensions the user named (dedupe by the real column they resolve to)
    seen_cols = set()
    dims_found = []
    for word in detractors.DIM_SYNONYMS:
        if word in q:
            col = detractors.resolve_dimension(word, names)
            if col and col not in seen_cols:
                seen_cols.add(col)
                dims_found.append(word)
    # also catch a column named directly
    cols = _all_columns(conn)
    nlu_dim = nlu.resolve_dimension(nlu.normalise(q), cols)

    # explicit "full / complete / report / كل / تقرير" => everything
    wants_all = re.search(r"\bfull\b|complete|report|deep ?dive|everything|all of|"
                          r"تقرير|كل ?حاجه|بالتفصيل|breakdown", q)

    if wants_all or len(dims_found) > 1:
        rep = detractors.full_report(conn, q,
              dimensions=dims_found or None)
        c = rep["count"]
        parts = [f"Detractor report ({c.get('period','all data')}): "
                 f"{c['detractors']} detractors out of {c['base']} ({c['pct']}%)."]
        for b in rep["breakdowns"]:
            parts.append("")
            parts.append(_fmt_breakdown(b))
        rp = rep["repeated"]
        if "error" not in rp and rp["repeat_count"]:
            parts.append("")
            parts.append(f"Repeated detractors: {rp['repeat_count']} customers more "
                         f"than once. Top:")
            parts.append(_format_table(rp["columns"], rp["rows"]))
        return "\n".join(parts)

    # single breakdown by a named dimension
    if dims_found or (nlu_dim and re.search(r"\bby\b|\bper\b|across|breakdown|"
                                            r"which|each|حسب|لكل", q)):
        word = dims_found[0] if dims_found else nlu_dim
        b = detractors.breakdown(conn, word, q)
        if "error" not in b:
            return _fmt_breakdown(b)

    # plain count (optionally time-filtered)
    c = detractors.count(conn, q)
    if "error" in c:
        return f"Detractors: {c['error']}"
    return (f"Detractors ({c['period']}): {c['detractors']} of {c['base']} "
            f"respondents ({c['pct']}%), scored 0-6 on {c['nps']}.")


def _answer_metric_by_dim(conn, q: str) -> str | None:
    """Handle 'average/sum/count of <metric> by <dimension>' style questions."""
    by = re.search(r"\bby\s+([a-z_]+)", q)
    has_agg = re.search(r"avg|average|mean|\bsum\b|total|count|how many|number of|"
                        r"highest|lowest|most|least|\btop\b|worst|best", q)
    if not by and not has_agg:
        return None
    cols = _all_columns(conn)
    words = re.findall(r"[a-z_]+", q)
    mentioned = [cols[w] for w in words if w in cols]
    if not mentioned:
        return None
    agg = ("AVG" if re.search(r"avg|average|mean", q) else
           "SUM" if re.search(r"\bsum|total\b", q) else
           "COUNT" if re.search(r"count|how many|number of", q) else "AVG")
    # metric = a numeric column; dimension = after the word "by"
    by = re.search(r"\bby\s+([a-z_]+)", q)
    dim = cols.get(by.group(1)) if by and by.group(1) in cols else None
    metric = next((m for m in mentioned if m != dim), None)
    if not metric:
        return None
    # find a table holding both
    for t in database.list_tables(conn):
        names = {c["name"] for c in t["columns"]}
        if metric in names and (dim is None or dim in names):
            order = " ORDER BY value ASC" if re.search(r"lowest|worst|least", q) else (
                " ORDER BY value DESC" if re.search(r"highest|best|most|top", q) else "")
            if dim:
                sql = (f'SELECT "{dim}" AS "{dim}", {agg}("{metric}") AS value '
                       f'FROM "{t["table"]}" GROUP BY "{dim}"{order}')
            else:
                sql = f'SELECT {agg}("{metric}") AS value FROM "{t["table"]}"'
            res = database.run_sql(conn, sql)
            if "error" in res or not res.get("rows"):
                continue
            head = f"{agg} of {metric}" + (f" by {dim}" if dim else "") + ":"
            return head + "\n" + _format_table(res["columns"], res["rows"])
    return None


def _answer_summary(conn, q: str) -> str | None:
    if not re.search(r"what do you know|summary|overview|what.*learn|tell me about the data|what.*data", q):
        return None
    return knowledge_text(conn)


def _answer_export(conn, q: str) -> str | None:
    if not re.search(r"\bexport\b|\bsave\b|download|excel|xlsx|workbook|"
                     r"powerpoint|pptx|slides|presentation|deck|"
                     r"\bword\b|docx|document|\bpdf\b|"
                     r"صدّر|صدر|احفظ|اكسل|عرض|بوربوينت|وورد", q):
        return None
    # pick format
    if re.search(r"powerpoint|pptx|slides|presentation|deck|عرض|بوربوينت", q):
        fmt = "pptx"
    elif re.search(r"\bword\b|docx|document|وورد", q):
        fmt = "docx"
    elif re.search(r"\bpdf\b", q):
        fmt = "pdf"
    else:
        fmt = "excel"

    if fmt == "excel":
        from . import export_excel as ex, timefilter
        # TNPS dashboard / full report
        if re.search(r"tnps.dashboard|full.report|full dashboard|dashboard", q):
            try:
                out = ex.export_tnps_dashboard(conn, q)
                return f"TNPS dashboard saved:\n  {out}"
            except Exception:
                pass
        tf = None
        for t in database.list_tables(conn):
            dcol = timefilter.find_date_column([c["name"] for c in t["columns"]])
            if dcol:
                tf = timefilter.parse(q, dcol); break
        if re.search(r"kpi", q):
            out = ex.export_kpis(conn)
        elif re.search(r"driver|drives|influenc", q):
            out = ex.export_drivers(conn)
        elif re.search(r"detractor|منتقد", q):
            out = ex.export_detractor_report(conn, q)
        else:
            out = ex.export_full(conn, q)
        return f"Exported to an e&-branded Excel file:\n  {out}"

    from . import export_docs as ed
    try:
        if fmt == "pptx":
            out = ed.export_pptx(conn, q); label = "PowerPoint deck"
        elif fmt == "docx":
            out = ed.export_docx(conn, q); label = "Word document"
        else:
            out = ed.export_pdf(conn, q); label = "PDF"
    except ImportError as exc:
        lib = {"pptx": "python-pptx", "docx": "python-docx", "pdf": "reportlab"}[fmt]
        return (f"That format needs the '{lib}' library, which isn't installed. "
                f"Re-run setup_windows.bat, or install it, then try again.")
    return f"Exported an e&-branded {label}:\n  {out}"


def _answer_tnps_dashboard(conn, q: str) -> str | None:
    """Catch 'run tnps', 'dashboard', 'full report' etc. and export the dashboard."""
    if not re.search(r"dashboard|full.report|full report|generate.report|"
                     r"run report|run tnps|export.all|all sheets|"
                     r"ريبورت|داشبورد|تقرير.كامل|اعمل.ريبورت", q):
        return None
    # Don't double-fire if already handled by _answer_export
    if re.search(r"\bexport\b|\bsave\b", q):
        return None
    from . import export_excel as ex
    try:
        out = ex.export_tnps_dashboard(conn, q)
        return (f"TNPS dashboard saved (26 sheets — Dashboard, KPIs, Detractors, "
                f"Forecast, Toxic Combos, Agent Ranking, and more):\n  {out}")
    except Exception as exc:  # noqa: BLE001
        return f"Dashboard error: {exc}"


def _answer_forecast(conn, q: str) -> str | None:
    if not re.search(r"forecast|predict|next.*days?|coming.*days?|future|projection|"
                     r"تنبؤ|توقع|المستقبل|الأيام القادمة", q):
        return None
    from . import tnps_analytics as ta
    from . import forecast as fc
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        return "No TNPS data found to forecast."
    daily = ta.build_daily_trend(df, cols)
    if daily.empty:
        return "Not enough daily data to build a forecast."
    m = re.search(r"(\d+)\s*days?", q)
    horizon = int(m.group(1)) if m else 30
    result = fc.build_forecast(daily, horizon=min(horizon, 90))
    if result.empty:
        return "Not enough data to forecast (need at least 4 days)."
    lines = [f"Holt-Winters forecast — next {horizon} days:"]
    for _, r in result.head(7).iterrows():
        d = str(r["Date"])[:10]
        rate = r.get("Forecast_Detractor_Rate_%", 0)
        lo = r.get("Forecast_Detractor_Rate_%_Lower", rate)
        hi = r.get("Forecast_Detractor_Rate_%_Upper", rate)
        lines.append(f"  {d}:  det.rate ≈ {rate:.1f}%  (range {lo:.1f}% – {hi:.1f}%)")
    if horizon > 7:
        lines.append(f"  ... ({horizon} days total in the forecast)")
    breach = result.attrs.get("breach_date")
    if breach and breach != "No breach in horizon":
        lines.append(f"\n  ⚠  Projected 40% target breach: {breach}")
    else:
        lines.append(f"\n  ✓  No breach of 40% detractor target in the {horizon}-day horizon.")
    lines.append(f"\nTip: type 'export tnps dashboard' to save the full forecast sheet.")
    return "\n".join(lines)


def _answer_waterfall(conn, q: str) -> str | None:
    if not re.search(r"waterfall|nps.*month|month.*nps|monthly.nps|gained|lost|"
                     r"promoters.*month|شلال|شهري", q):
        return None
    from . import tnps_analytics as ta
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        return None
    result = ta.build_nps_waterfall(df, cols)
    if result.empty:
        return "No monthly data for waterfall."
    lines = ["NPS Waterfall (monthly):"]
    for _, r in result.iterrows():
        lines.append(f"  {r['Month']}:  NPS={r['NPS_Score']:+.1f}  |  "
                     f"+{int(r['Promoters'])} promoters  -{int(r['Detractors'])} detractors  "
                     f"→ net {int(r['Net_Gain']):+d}  |  det.rate {r['Detractor_Rate_%']:.1f}%")
    return "\n".join(lines)


def _answer_top_bottom(conn, q: str) -> str | None:
    if not re.search(r"top.*bottom|best.*worst|worst.*best|top 10|bottom 10|"
                     r"ranked.queues|best queues|worst queues|league|"
                     r"أفضل.*أسوأ|أسوأ.*أفضل", q):
        return None
    from . import tnps_analytics as ta
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        return None
    result = ta.build_top_bottom(df, cols)
    if result.empty:
        return "Not enough data for top/bottom ranking."
    lines = ["Top & Bottom queues by detractor rate:"]
    for rank_type in ["Worst 10 Queues", "Best 10 Queues",
                      "Worst 10 Short Codes", "Best 10 Short Codes"]:
        sub = result[result["Rank_Type"] == rank_type]
        if sub.empty:
            continue
        lines.append(f"\n  {rank_type}:")
        for _, r in sub.head(5).iterrows():
            lines.append(f"    {str(r['Name']):<40} {r['Detractor_Rate_%']:.1f}%  "
                         f"({int(r['Detractor_Count'])} det.)")
    return "\n".join(lines)


def _answer_agent_ranking(conn, q: str) -> str | None:
    if not re.search(r"agent.rank|rank.*agent|agent.perf|best.agent|worst.agent|"
                     r"peer.bench|benchmark|percentile|band|"
                     r"ترتيب.*وكيل|أفضل.*وكيل|أسوأ.*وكيل", q):
        return None
    from . import tnps_analytics as ta
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        return None
    if re.search(r"peer|bench", q):
        result = ta.build_agent_peer_benchmark(df, cols)
        if result.empty:
            return "No agent peer benchmark data (need Agent_Id column)."
        lines = ["Agent peer benchmark (vs queue average):"]
        for _, r in result.head(8).iterrows():
            delta = r.get("vs_Queue_Avg", 0)
            arrow = "▲" if delta > 0 else "▼" if delta < 0 else "="
            lines.append(f"  {str(r.get('Agent_Id_','?')):<25} "
                         f"{r['Detractor_Rate_%']:.1f}%  {arrow}{abs(delta):.1f}pp vs queue avg  "
                         f"(rank #{int(r.get('Peer_Rank',0))} in {r.get('AGENT_QUEUE','?')})")
        return "\n".join(lines)
    result = ta.build_agent_ranking(df, cols)
    if result.empty:
        return "No agent ranking data (need Agent_Id column)."
    lines = ["Agent ranking by detractor rate:"]
    lines.append("  Worst performers:")
    for _, r in result.head(5).iterrows():
        lines.append(f"    {str(r.get('Agent_Id','?')):<25} {r['Detractor_Rate_%']:.1f}%  "
                     f"Avg tNPS={r['Avg_TNPS']:.1f}  [{r['Band']}]  (n={int(r['Surveys'])})")
    lines.append("  Best performers:")
    for _, r in result[result["Detractor_Rate_%"] == 0].head(5).iterrows():
        lines.append(f"    {str(r.get('Agent_Id','?')):<25} 0.0%  "
                     f"Avg tNPS={r['Avg_TNPS']:.1f}  [{r['Band']}]")
    return "\n".join(lines)


def _answer_pattern(conn, q: str) -> str | None:
    if not re.search(r"hour|time of day|day of week|weekday|weekend|pattern|"
                     r"ساعة|يوم|أيام|نمط|وقت", q):
        return None
    from . import tnps_analytics as ta
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        return None
    if re.search(r"hour|time of day|ساعة|وقت", q):
        result = ta.build_hour_pattern(df, cols)
        if result.empty:
            return None
        worst = result.nlargest(3, "Detractor_Rate_%")
        best = result[result["Surveys"] > 0].nsmallest(3, "Detractor_Rate_%")
        lines = ["Detractor rate by hour of day:"]
        lines.append("  Worst hours:")
        for _, r in worst.iterrows():
            lines.append(f"    {int(r['Hour']):02d}:00  →  {r['Detractor_Rate_%']:.1f}%  "
                         f"({int(r['Detractors'])} det. / {int(r['Surveys'])} surveys)")
        lines.append("  Best hours:")
        for _, r in best.iterrows():
            lines.append(f"    {int(r['Hour']):02d}:00  →  {r['Detractor_Rate_%']:.1f}%")
        return "\n".join(lines)
    result = ta.build_dow_pattern(df, cols)
    if result.empty:
        return None
    lines = ["Detractor rate by day of week:"]
    for _, r in result.iterrows():
        bar = "█" * max(1, int(r["Detractor_Rate_%"] / 5))
        lines.append(f"  {str(r['Day']):<12} {r['Detractor_Rate_%']:5.1f}%  {bar}")
    return "\n".join(lines)


def _answer_channel_fcr(conn, q: str) -> str | None:
    if not re.search(r"channel|fcr|first.call|resolution|قناة|fcr", q):
        return None
    from . import tnps_analytics as ta
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        return None
    if re.search(r"fcr|first.call|resolution", q):
        col = cols.get("fcr_flag")
        if not col:
            return "No FCR column found in your data."
        result = ta.breakdown_by(df, col, cols)
        if result.empty:
            return None
        lines = ["FCR impact on tNPS:"]
        for _, r in result.iterrows():
            lines.append(f"  FCR={r[col]:<5}  Det.Rate={r['Detractor_Rate_%']:.1f}%  "
                         f"Avg tNPS={r['Avg_TNPS']:.2f}  (n={int(r['Completed_Surveys'])})")
        return "\n".join(lines)
    col = cols.get("channel")
    if not col:
        return "No channel column found in your data."
    result = ta.breakdown_by(df, col, cols)
    if result.empty:
        return None
    lines = ["Detractor rate by channel:"]
    for _, r in result.iterrows():
        lines.append(f"  {str(r[col]):<28} Det.Rate={r['Detractor_Rate_%']:.1f}%  "
                     f"Avg tNPS={r['Avg_TNPS']:.2f}")
    return "\n".join(lines)


def _answer_cohort(conn, q: str) -> str | None:
    if not re.search(r"cohort|recovery|came back|returned|retained|استرداد|تعافي", q):
        return None
    from . import tnps_analytics as ta
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        return None
    result = ta.build_cohort_analysis(df, cols)
    if result.empty:
        return "No cohort data (need MSISDN column + multiple months)."
    lines = ["Cohort analysis (detractor recovery by month):"]
    for _, row in result.head(6).iterrows():
        lines.append(f"  - Cohort {row.get('Cohort_Month (First_Detractor)', '?')}: "
                     f"Recovery {row.get('Recovery_Rate_%', 0):.1f}%")
    return "\n".join(lines)


def _answer_velocity(conn, q: str) -> str | None:
    if not re.search(r"velocity|spike|sudden|week.over.week|wow|jumped|تسارع|قفز", q):
        return None
    from . import tnps_analytics as ta
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        return None
    result = ta.build_velocity_alerts(df, cols)
    if result.empty:
        return "No velocity alerts detected."
    lines = ["Velocity alerts (week-over-week spikes):"]
    for _, row in result.head(5).iterrows():
        lines.append(f"  - {row.get('AGENT_QUEUE', '?')} week {row.get('Week', '?')}: "
                     f"{row.get('Alert', '?')}")
    return "\n".join(lines)


def _answer_toxic_combos(conn, q: str) -> str | None:
    if not re.search(r"toxic|combo|root.cause|combination|worst mix|أسوأ|سام", q):
        return None
    from . import tnps_analytics as ta
    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        return None
    result = ta.build_toxic_combos(df, cols)
    if result.empty:
        return "No toxic combos found."
    lines = ["Top toxic dimension combos:"]
    for _, row in result.head(5).iterrows():
        lines.append(f"  - {row.get('Combo', '?')}: {row.get('Detractor_Rate_%', 0):.1f}% detractor rate "
                     f"({int(row.get('Detractors', 0))} detractors / {int(row.get('Surveys', 0))} surveys)")
    return "\n".join(lines)


def _answer_stats(conn, q: str) -> str | None:
    if not re.search(r"\bstats?\b|statistic|describe|distribution|spread|"
                     r"\bmean\b|median|std|percentile|correlat|"
                     r"احصاء|توزيع|متوسط|وسيط", q):
        return None
    from . import stats
    if re.search(r"correlat|relationship|ارتباط", q):
        c = stats.correlations(conn)
        if "error" in c:
            return f"Correlations: {c['error']}"
        lines = [f"Correlations in '{c['table']}':"]
        for p in c["pairs"][:8]:
            lines.append(f"  - {p['a']} & {p['b']}: r={p['r']} ({p['strength']} "
                         f"{p['direction']})")
        if c["small_sample"]:
            lines.append("  Note: small sample - treat as indicative.")
        return "\n".join(lines)
    return stats.summary_text(conn)


def _answer_assumptions(conn, q: str) -> str | None:
    if not re.search(r"assumption|how do you|your rules|methodology|تفترض|قواعد|"
                     r"منهجية|how.*calculate|how.*define", q):
        return None
    from . import knowledge
    txt = knowledge._read("assumptions.md")
    if not txt:
        return None
    # return a trimmed, readable version
    return ("Here are the rules I work by (your own definitions always win):\n\n"
            + txt)


def _answer_compare(conn, q: str) -> str | None:
    if not re.search(r"\bvs\b|versus|compare|compared|against|change.*from|"
                     r"\bvs\.|مقارنة|مقابل", q):
        return None
    from . import detractors, timefilter, nlu
    # find a date column and parse two periods from the question
    datec = None
    for t in database.list_tables(conn):
        datec = timefilter.find_date_column([c["name"] for c in t["columns"]])
        if datec:
            break
    if not datec:
        return "I need a date column to compare periods, but couldn't find one."
    # split the question on vs/against and parse each side for a time window
    parts = re.split(r"\bvs\b|versus|vs\.|against|مقابل", q, maxsplit=1)
    if len(parts) == 2:
        pa = timefilter.parse(parts[0], datec)
        pb = timefilter.parse(parts[1], datec)
        if pa and pb:
            cmp = detractors.compare(conn, parts[0], parts[1])
            if "error" not in cmp:
                a, b = cmp["a"], cmp["b"]
                arrow = ("up" if cmp["direction"] == "up" else
                         "down" if cmp["direction"] == "down" else "flat")
                return (f"Detractors {a['period']} vs {b['period']}:\n"
                        f"  {a['period']}: {a['detractors']} ({a['pct']}%)\n"
                        f"  {b['period']}: {b['detractors']} ({b['pct']}%)\n"
                        f"  Change: {arrow} {abs(cmp['delta'])} "
                        f"({cmp['rel_pct']:+}% relative, "
                        f"{cmp['pct_point_delta']:+} pts).")
    return ("To compare, name two periods, e.g. 'detractors April vs March' "
            "or 'compare Q1 vs Q2'.")


def _answer_alerts(conn, q: str) -> str | None:
    if not re.search(r"alert|watch|what changed|what.s new|anything new|notable|"
                     r"notice|تنبيه|تغير|جديد", q):
        return None
    from . import alerts
    return alerts.alerts_text(conn)


def _answer_join_mapping(conn, q: str) -> str | None:
    """Interactive join: pick a mapping file via dialog, choose the key, merge."""
    if not re.search(r"\bjoin\b|\bmerge\b|\bvlookup\b|join.{0,15}map|map.{0,15}join|"
                     r"link.*file|add.*mapping.*file|mapping.*from.*file|"
                     r"دمج.*ملف|ربط.*ملف|أضف.*مابينج", q):
        return None

    from . import mapping as mp

    # clear any spinner artifacts and signal interactive mode
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()

    tables = database.list_tables(conn)
    if not tables:
        return "No data loaded yet. Add a file first:  roma add <file>"

    # pick base table
    print()
    print("  ┌─ Join: loaded tables ─────────────────────────────────")
    for i, t in enumerate(tables, 1):
        print(f"  │  [{i}] {t['table']}  ({t['row_count']} rows)")
    print("  └───────────────────────────────────────────────────────")

    if len(tables) == 1:
        base_info = tables[0]
        print(f"\n  Using '{base_info['table']}' as the base table.")
    else:
        bsel = input("\n  Which table is the BASE? (number) > ").strip()
        idx = (int(bsel) - 1) if bsel.isdigit() else 0
        base_info = tables[max(0, min(idx, len(tables) - 1))]

    base_cols = [c["name"] for c in base_info["columns"]]

    # open file picker for the mapping/lookup file
    print("\n  Opening file picker for the MAPPING file...")
    lookup_path: str | None = None
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        lookup_path = filedialog.askopenfilename(
            title="Select MAPPING / LOOKUP file",
            filetypes=[("Data files", "*.xlsx *.xls *.xlsm *.csv *.tsv"),
                       ("All files", "*.*")])
        root.destroy()
    except Exception:
        pass

    if not lookup_path:
        lookup_path = input(
            "  (No picker available) Type path to the mapping file > "
        ).strip().strip('"')
    if not lookup_path:
        return "No mapping file selected. Cancelled."

    try:
        look_cols = mp.columns_of(lookup_path)
    except Exception as exc:  # noqa: BLE001
        return f"Couldn't read mapping file: {exc}"

    # show columns side by side
    print(f"\n  BASE '{base_info['table']}' columns:")
    for i, c in enumerate(base_cols, 1):
        print(f"    [{i:2d}] {c}")

    print(f"\n  MAPPING '{Path(lookup_path).name}' columns:")
    for i, c in enumerate(look_cols, 1):
        print(f"    [{i:2d}] {c}")

    # pick the common / join key from the mapping file
    print(f"\n  Which column in the MAPPING file is the JOIN KEY?")
    lk = input("  (number or name) > ").strip()
    lookup_key = mp.resolve_choice(lk, look_cols)
    if not lookup_key:
        return f"Column not recognised: '{lk}'"

    # auto-detect matching base column
    base_key = next((c for c in base_cols if c.lower() == lookup_key.lower()), None)
    if not base_key:
        print(f"\n  Which column in the BASE table matches '{lookup_key}'?")
        bk = input("  (number or name) > ").strip()
        base_key = mp.resolve_choice(bk, base_cols)
        if not base_key:
            return f"Base column not recognised: '{bk}'"
    else:
        print(f"  Auto-matched: '{base_key}' (base) ↔ '{lookup_key}' (mapping)")

    # pick which columns to bring across
    other_look = [c for c in look_cols if c != lookup_key]
    print(f"\n  Columns available to ADD from the mapping file:")
    for i, c in enumerate(other_look, 1):
        print(f"    [{i:2d}] {c}")
    ac = input("\n  Which to add? (numbers, names, or 'all') > ").strip()
    if ac.lower() == "all":
        add_cols = other_look
    else:
        add_cols = mp.resolve_multi(ac, other_look)
    add_cols = [c for c in add_cols if c != lookup_key]
    if not add_cols:
        return "No columns chosen to add. Cancelled."

    # do the left join
    print(f"\n  Joining on '{base_key}' ↔ '{lookup_key}', adding: {', '.join(add_cols)} ...")
    base_df = pd.read_sql_query(f'SELECT * FROM "{base_info["table"]}"', conn)
    lookup_df = mp._read_any(lookup_path)

    right = lookup_df[[lookup_key] + [c for c in add_cols if c != lookup_key]].copy()
    if lookup_key != base_key:
        right = right.rename(columns={lookup_key: base_key})
    merged = base_df.merge(right, on=base_key, how="left", suffixes=("", "_mapped"))

    matched = int(merged[add_cols[0]].notna().sum()) if add_cols else 0

    # load into SQLite and save Excel
    saved = mp.save_and_load(merged, conn, name="mapped")

    # bordered preview of joined result
    preview_cols = [base_key] + add_cols[:5]
    sample_df = merged[preview_cols].head(20)
    preview_rows = [
        [("" if (isinstance(v, float) and pd.isna(v)) else str(v)) for v in r]
        for r in sample_df.values.tolist()
    ]
    preview = _format_bordered_table(
        preview_cols, preview_rows,
        title=f"Join preview — {matched}/{len(base_df)} rows matched  ·  table: mapped")

    return (f"\nJoin done.  {matched} of {len(base_df)} base rows matched.\n"
            f"  Loaded as table 'mapped' in Roma's database.\n"
            f"  Excel saved: {saved['file']}\n\n"
            f"{preview}\n\n"
            f"  Tip: type  'print top detractors by {add_cols[0]}'  to see a ranked table.")


def _answer_mapping(conn, q: str) -> str | None:
    if not re.search(r"how.*map|explain.*map|what.*mapping|^mapping$|"
                     r"roma map\b|merge.*file|مابينج.*ازاي|شرح.*مابينج", q):
        return None
    return ("To join / map two files right here in chat, type:\n"
            "    join mapping\n"
            "Roma will open a file picker for the MAPPING file, list both tables' "
            "columns, ask for the common key, pick which columns to add, merge them, "
            "load the result, and print a preview — all without leaving the chat.\n\n"
            "You can also run it from the terminal:  roma map  (same flow, no chat needed).")


_BUILDERS = [_answer_tnps_dashboard, _answer_export,
             _answer_join_mapping, _answer_mapping,
             _answer_assumptions, _answer_stats, _answer_compare,
             _answer_alerts, _answer_identity,
             _answer_summary, _answer_detractors,
             _answer_drivers, _answer_segments, _answer_trend, _answer_anomalies,
             _answer_repeat, _answer_top_values, _answer_print_top,
             _answer_metric_by_dim, _answer_kpis,
             _answer_forecast, _answer_waterfall, _answer_top_bottom,
             _answer_agent_ranking, _answer_pattern, _answer_channel_fcr,
             _answer_cohort, _answer_velocity, _answer_toxic_combos]


def knowledge_text(conn) -> str:
    """A compact, human-readable summary of what Roma has loaded/learned."""
    tables = database.list_tables(conn)
    docs = database.document_sources(conn)
    links = database.detect_links(conn)
    lines = ["Here's what I have:"]
    for t in tables:
        lines.append(f"  - table '{t['table']}': {t['row_count']} rows, "
                     f"columns: {', '.join(c['name'] for c in t['columns'])}")
    for d in docs:
        lines.append(f"  - document '{d['source']}' ({d['chunks']} chunks)")
    if links:
        lines.append("Files can be linked on: " +
                     ", ".join(f"{l['column']}" for l in links))
    tgt_table, tgt = analyst.infer_target(conn)
    if tgt:
        lines.append(f"Likely key metric to analyse: '{tgt}'. "
                     f"Ask me 'what drives {tgt}?'")
    return "\n".join(lines)


def structured_answer(conn, question: str) -> str | None:
    q = question.lower().strip()
    # 0) Have I been taught what this phrase means? Use it first.
    try:
        from . import learning
        taught = learning.learned_intent(question)
        if taught:
            ans = run_intent(conn, taught, question)
            if ans:
                return ans
    except Exception:  # noqa: BLE001
        pass
    # Period comparison ("April vs March") before the single-period detractor path.
    if re.search(r"\bvs\b|versus|vs\.|compared? to|compared? with|مقابل|مقارنة", q):
        cmp = _answer_compare(conn, q)
        if cmp:
            return cmp
    # Export requests take precedence (they contain words like 'detractor' that
    # would otherwise route to analysis).
    if re.search(r"\bexport\b|\bsave\b|download|excel|xlsx|workbook|powerpoint|"
                 r"pptx|slides|presentation|deck|\bword\b|docx|\bpdf\b|"
                 r"صدّر|صدر|احفظ|اكسل|عرض|بوربوينت|وورد", q):
        try:
            ex = _answer_export(conn, q)
            if ex:
                return ex
        except Exception as exc:  # noqa: BLE001
            return f"Export failed: {exc}"
    # Assumptions/methodology questions, before identity grabs the 'you'.
    if re.search(r"assumption|your rules|methodology|تفترض|قواعد|منهجية|"
                 r"how do you (calculate|define|work)", q):
        a = _answer_assumptions(conn, q)
        if a:
            return a
    # 1) Try the NLU layer: understand free phrasing, route to the right builder.
    nlu_ans = _nlu_route(conn, question)
    if nlu_ans:
        return nlu_ans
    # 2) Fall back to direct keyword builders.
    for builder in _BUILDERS:
        try:
            ans = builder(conn, q)
        except Exception:  # noqa: BLE001
            ans = None
        if ans:
            return ans
    return None


# Map an NLU intent name to the builder that executes it.
_INTENT_TO_BUILDER = {
    "identity": _answer_identity,
    "summary": _answer_summary,
    "drives": _answer_drivers,
    "detractor_by": _answer_detractors,
    "segment": _answer_segments,
    "trend": _answer_trend,
    "anomaly": _answer_anomalies,
    "repeat": _answer_repeat,
    "top": _answer_top_values,
    "metric_by": _answer_metric_by_dim,
    "kpi": _answer_kpis,
}


def _direct_drivers(conn, question):
    from . import nlu
    cols = _all_columns(conn)
    toks = nlu.normalise(question)
    target = nlu.resolve_column([t for t in toks if t != "drives"], cols)
    res = analyst.drivers(conn, target)
    if "error" in res:
        return f"Drivers: {res['error']}"
    lines = [f"What most influences '{res['target']}' "
             f"({res['model']}, {res['score_metric']}={res['score']}, "
             f"{res['rows_used']} rows):"]
    for f in res["features"]:
        eff = f" (more = {f['effect']} {res['target']})" if f.get("effect") else ""
        lines.append(f"  - {f['feature']}: importance {f['importance']}{eff}")
    return "\n".join(lines)


def _direct_detractor_by(conn, question):
    return _answer_detractors(conn, question.lower().strip())


# Intents that should run directly (language-agnostic), bypassing English regex.
_DIRECT = {
    "drives": _direct_drivers,
    "detractor_by": _direct_detractor_by,
    "segment": lambda c, q: _answer_segments(c, "segment"),
    "anomaly": lambda c, q: _answer_anomalies(c, "anomaly"),
    "repeat": lambda c, q: _answer_repeat(c, "repeat"),
    "summary": lambda c, q: _answer_summary(c, "summary"),
    "identity": lambda c, q: _answer_identity(c, "who are you"),
    "kpi": lambda c, q: _answer_kpis(c, "kpi"),
}


def _nlu_route(conn, question: str):
    """Use the NLU layer to pick an intent, then run it (language-agnostic)."""
    from . import nlu
    cols = _all_columns(conn)
    plan = nlu.understand(question, cols)
    if not plan["intent"] or plan["confidence"] <= 0:
        return None
    for intent in plan["candidates"]:
        # builders that need raw-text parsing (by-dimension) run via the builder;
        # the rest run via a direct executor so non-English phrasing still works.
        if intent in ("top", "metric_by"):
            builder = _INTENT_TO_BUILDER.get(intent)
            try:
                ans = builder(conn, question.lower().strip())
            except Exception:  # noqa: BLE001
                ans = None
        elif intent in _DIRECT:
            try:
                ans = _DIRECT[intent](conn, question)
            except Exception:  # noqa: BLE001
                ans = None
        else:
            ans = None
        if ans:
            return ans
    return None


# ------------------------------- main entry -------------------------------- #

def run_intent(conn, intent: str, question: str) -> str | None:
    """Run any intent by name (direct executor or builder)."""
    if intent in _DIRECT:
        try:
            return _DIRECT[intent](conn, question)
        except Exception:  # noqa: BLE001
            return None
    builder = _INTENT_TO_BUILDER.get(intent)
    if builder:
        try:
            return builder(conn, question.lower().strip())
        except Exception:  # noqa: BLE001
            return None
    if intent == "stats":
        return _answer_stats(conn, "statistics")
    if intent == "export":
        return _answer_export(conn, question)
    return None


_LAST_Q = {"text": None}

_INTENT_WORDS = {
    "drives": "drives", "driver": "drives", "drivers": "drives",
    "detractor": "detractor_by", "detractors": "detractor_by",
    "segment": "segment", "segments": "segment",
    "trend": "trend", "anomaly": "anomaly", "anomalies": "anomaly",
    "repeat": "repeat", "kpi": "kpi", "kpis": "kpi", "stats": "stats",
    "statistics": "stats", "summary": "summary", "export": "export", "top": "top",
}


def _handle_teaching(conn, question: str) -> str | None:
    """Detect 'no I meant X' / 'X means Y' and learn from it."""
    from . import learning
    q = question.lower().strip()

    m = re.search(r"['\"]?([a-z\u0600-\u06ff_ ]{2,30})['\"]?\s+"
                  r"(?:means|=|يعني|معناها)\s+"
                  r"['\"]?([a-z\u0600-\u06ff_ ]{2,30})", q)
    if m:
        word, meaning = m.group(1).strip(), m.group(2).strip()
        learning.teach_word(word, meaning)
        return (f"Got it - I'll treat '{word}' as '{meaning}' from now on.")

    if re.search(r"\b(no|not|wrong|i meant|i mean|actually)\b|لا|قصدي|غلط", q):
        for w, intent in _INTENT_WORDS.items():
            if re.search(rf"\b{w}\b", q):
                if _LAST_Q["text"]:
                    learning.teach_intent(_LAST_Q["text"], intent)
                    ans = run_intent(conn, intent, _LAST_Q["text"])
                    note = (f"Sorry about that - I've learned it: I'll read "
                            f"\"{_LAST_Q['text']}\" as {intent} from now on.\n\n")
                    return note + (ans or "")
                return (f"Noted. Ask the question again and I'll treat it as {intent}.")
    return None


SYSTEM = ("You are Roma, a concise customer-experience data analyst. Answer the "
          "user's question using ONLY the findings provided. Do not invent "
          "numbers. If the findings don't cover it, say what data would be needed.")


def answer(conn, question: str, use_llm: bool, model: str | None) -> str:
    # 1) Is this a correction / teaching message? Learn from it.
    teach = _handle_teaching(conn, question)
    if teach is not None:
        return teach

    structured = structured_answer(conn, question)

    if use_llm and model:
        context = structured or knowledge_text(conn)
        prompt = (f"Findings from the data:\n{context}\n\n"
                  f"User question: {question}\n\nAnswer concisely.")
        try:
            out = localchat.chat(model, knowledge.persona_system_prompt(), prompt)
            _LAST_Q["text"] = question
            return out
        except Exception:  # noqa: BLE001
            pass

    _LAST_Q["text"] = question  # remember so a follow-up correction can teach
    if structured:
        return structured

    # 2) Confused: instead of a generic reply, guess the closest intents and ASK.
    from . import nlu
    cols = _all_columns(conn)
    plan = nlu.understand(question, cols)
    guesses = [g for g in plan.get("candidates", []) if g][:3]
    if guesses:
        pretty = {"drives": "what drives a metric", "detractor_by": "detractors",
                  "segment": "segments", "trend": "a trend", "anomaly": "anomalies",
                  "repeat": "repeat customers", "kpi": "KPIs", "top": "top values",
                  "metric_by": "an average by a column", "stats": "statistics",
                  "summary": "a data summary", "export": "an export"}
        opts = ", ".join(pretty.get(g, g) for g in guesses)
        return (f"I'm not fully sure what you mean. Did you want one of these: "
                f"{opts}?\n"
                f"Tell me which (e.g. \"I meant {guesses[0]}\") and I'll remember "
                f"your phrasing next time.")
    return (f"I didn't quite catch that. I can do drivers, KPIs, detractors, "
            f"segments, trends, anomalies, repeat customers, stats, and exports.\n"
            f"If you tell me what you meant (e.g. \"this means detractors\"), "
            f"I'll learn it for next time.")
