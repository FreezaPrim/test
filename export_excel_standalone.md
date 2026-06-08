# export_excel.py — Roma TNPS Dashboard — Standalone Reference

> **استخدام مستقل**: الملف ده مكتفي بذاته — بيحتوي على الكود الكامل لـ `export_excel.py`  
> اللي بيولّد الـ Excel بالـ 26+ شيت. تقدر تقرأه وتشغّله بشكل مستقل.

---

## Requirements

```
pip install openpyxl pandas numpy
```

---

## File: `roma/export_excel.py`

```python
"""Export Roma's analyses to professional, e&-branded Excel workbooks.

Built on openpyxl (already a Roma dependency), so it works fully offline on a
locked-down laptop. Applies the standards from Anthropic's xlsx skill:
consistent Arial font, styled headers, frozen panes, sensible column widths,
number formats, and zero hardcoded "junk" - each analysis gets its own sheet.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import analyst, config, database, detractors, kpis

# e& brand palette
BRAND_RED = "E00800"
DARK      = "1A1A1A"
LIGHT     = "FCE9E7"   # pale red for banding
GREY      = "F2F2F2"
WHITE     = "FFFFFF"

_THIN   = Side(style="thin", color="DDDDDD")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


# ============================================================
# SHARED HELPERS
# ============================================================

def _title(ws, text: str, span: int) -> int:
    """Write a brand title bar across `span` columns. Return next row."""
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, span))
    c = ws.cell(row=1, column=1, value=text)
    c.font      = Font(name="Arial", size=14, bold=True, color=WHITE)
    c.fill      = PatternFill("solid", fgColor=BRAND_RED)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 26
    sub = ws.cell(row=2, column=1,
                  value=f"Roma - CX analytics  |  generated {datetime.now():%Y-%m-%d %H:%M}")
    sub.font = Font(name="Arial", size=8, italic=True, color="888888")
    return 4   # data starts at row 4


def _header_row(ws, row: int, headers: list[str]) -> None:
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=j, value=str(h))
        c.font      = Font(name="Arial", size=10, bold=True, color=WHITE)
        c.fill      = PatternFill("solid", fgColor=DARK)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border    = _BORDER


def _data_rows(ws, start_row: int, rows: list[list[Any]]) -> int:
    for i, r in enumerate(rows):
        row = start_row + i
        for j, v in enumerate(r, start=1):
            c = ws.cell(row=row, column=j, value=v)
            c.font      = Font(name="Arial", size=10, color=DARK)
            c.fill      = PatternFill("solid", fgColor=(LIGHT if i % 2 else WHITE))
            c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
            c.border    = _BORDER
    return start_row + len(rows)


def _autofit(ws, headers: list[str], rows: list[list[Any]]) -> None:
    for j, h in enumerate(headers, start=1):
        width = len(str(h))
        for r in rows:
            if j - 1 < len(r):
                width = max(width, len(str(r[j - 1])))
        ws.column_dimensions[get_column_letter(j)].width = min(max(width + 4, 12), 50)


def _sheet_from_table(wb, name: str, title: str, headers: list[str],
                      rows: list[list[Any]], chart: bool = False) -> None:
    ws    = wb.create_sheet(title=name[:31])
    start = _title(ws, title, len(headers))
    _header_row(ws, start, headers)
    end   = _data_rows(ws, start + 1, rows)
    _autofit(ws, headers, rows)
    ws.freeze_panes          = ws.cell(row=start + 1, column=1)
    ws.sheet_view.showGridLines = False
    # Optional bar chart for 2-column category/number tables
    if chart and len(headers) == 2 and rows:
        ch           = BarChart()
        ch.type      = "col"
        ch.title     = title
        ch.height    = 7
        ch.width     = 14
        data = Reference(ws, min_col=2, min_row=start, max_row=end - 1)
        cats = Reference(ws, min_col=1, min_row=start + 1, max_row=end - 1)
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(cats)
        ch.legend = None
        if ch.series:
            ch.series[0].graphicalProperties.solidFill = BRAND_RED
        anchor_col = get_column_letter(len(headers) + 2)
        ws.add_chart(ch, f"{anchor_col}{start}")


def _save(wb: Workbook, kind: str, time_q: str) -> Path:
    import re as _re
    config.ensure_dirs()
    raw = time_q.strip().lower()
    m   = _re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|"
                     r"20\d{2}|last month|this month|this year)\w*", raw)
    tag   = "_" + _re.sub(r"\W+", "", m.group(0)) if m else ""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out   = config.REPORTS_DIR / f"{kind}{tag}_{stamp}.xlsx"
    wb.save(out)
    return out


# ============================================================
# SIMPLE EXPORTS  (KPIs, Drivers, Detractors)
# ============================================================

def export_kpis(conn) -> Path:
    rows = [[k["name"], k["value"], k["detail"], k["table"]]
            for k in kpis.compute(conn)]
    wb = Workbook()
    wb.remove(wb.active)
    _sheet_from_table(wb, "KPIs", "CX KPIs",
                      ["KPI", "Value", "Detail", "Source"],
                      rows or [["-", "-", "-", "-"]])
    return _save(wb, "kpis", "")


def export_drivers(conn, target: str | None = None) -> Path:
    d  = analyst.drivers(conn, target)
    wb = Workbook()
    wb.remove(wb.active)
    if "error" in d:
        _sheet_from_table(wb, "Drivers", "Drivers", ["Note"], [[d["error"]]])
    else:
        rows = [[f["feature"], f["importance"], f.get("effect") or ""]
                for f in d["features"]]
        _sheet_from_table(wb, "Drivers",
                          f"Drivers of {d['target']} ({d['score_metric']}={d['score']})",
                          ["Feature", "Importance", "Effect"], rows)
    return _save(wb, "drivers", "")


def export_detractor_report(conn, time_q: str = "") -> Path:
    """A multi-sheet detractor workbook: summary, each breakdown, repeats."""
    rep = detractors.full_report(conn, time_q)
    if "error" in rep:
        raise ValueError(rep["error"])
    wb = Workbook()
    wb.remove(wb.active)

    c      = rep["count"]
    period = c.get("period", "all data")
    _sheet_from_table(
        wb, "Summary", f"Detractor Summary - {period}",
        ["Metric", "Value"],
        [["Period",             period],
         ["Total respondents",  c["base"]],
         ["Detractors (0-6)",   c["detractors"]],
         ["Detractor rate",     f"{c['pct']}%"],
         ["Scored on",          c["nps"]]],
    )
    for b in rep["breakdowns"]:
        rows = [[r.get(b["dimension"]), r.get("detractors")] for r in b["rows"]]
        _sheet_from_table(wb, f"By {b['dimension']}"[:31],
                          f"Detractors by {b['dimension']} - {period}",
                          [b["dimension"], "Detractors"], rows, chart=True)
    rp = rep["repeated"]
    if "error" not in rp and rp["rows"]:
        rows = [[r.get("customer"), r.get("times")] for r in rp["rows"]]
        _sheet_from_table(wb, "Repeated", f"Repeated Detractors - {period}",
                          ["Customer", "Times"], rows)
    return _save(wb, "detractor_report", time_q)


def _add_trend_sheet(wb: Workbook, conn) -> None:
    """Monthly detractor-rate trend sheet with a line chart, if dates exist."""
    import pandas as pd
    from . import detractors, timefilter
    table, nps, idc, datec, names = detractors._detractor_table(conn)
    if not table or not datec:
        return
    df    = pd.read_sql_query(f'SELECT "{datec}", "{nps}" FROM "{table}"', conn)
    dts   = pd.to_datetime(df[datec], errors="coerce")
    score = pd.to_numeric(df[nps], errors="coerce")
    mask  = dts.notna() & score.notna()
    if mask.sum() < 4:
        return
    g   = pd.DataFrame({"m":   dts[mask].dt.to_period("M").astype(str),
                         "det": (score[mask] <= 6).astype(int)})
    grp = g.groupby("m")["det"].mean().mul(100).round(1)
    if len(grp) < 2:
        return
    rows = [[m, float(v)] for m, v in grp.items()]
    ws   = wb.create_sheet(title="Trend"[:31])
    start = _title(ws, "Detractor rate by month (%)", 2)
    _header_row(ws, start, ["Month", "Detractor %"])
    end   = _data_rows(ws, start + 1, rows)
    _autofit(ws, ["Month", "Detractor %"], rows)
    ws.sheet_view.showGridLines = False
    ch         = LineChart()
    ch.title   = "Detractor rate by month (%)"
    ch.height  = 8; ch.width = 16
    data = Reference(ws, min_col=2, min_row=start, max_row=end - 1)
    cats = Reference(ws, min_col=1, min_row=start + 1, max_row=end - 1)
    ch.add_data(data, titles_from_data=True)
    ch.set_categories(cats)
    ch.legend = None
    if ch.series:
        ch.series[0].graphicalProperties.line.solidFill = BRAND_RED
        ch.series[0].graphicalProperties.line.width     = 28000
    ws.add_chart(ch, "D" + str(start))


def export_full(conn, time_q: str = "") -> Path:
    """Everything Roma knows in one workbook: KPIs, drivers, detractors."""
    wb = Workbook()
    wb.remove(wb.active)

    krows = [[k["name"], k["value"], k["detail"]] for k in kpis.compute(conn)]
    if krows:
        _sheet_from_table(wb, "KPIs", "CX KPIs", ["KPI", "Value", "Detail"], krows)

    d = analyst.drivers(conn)
    if "error" not in d:
        rows = [[f["feature"], f["importance"], f.get("effect") or ""]
                for f in d["features"]]
        _sheet_from_table(wb, "Drivers", f"Drivers of {d['target']}",
                          ["Feature", "Importance", "Effect"], rows)

    rep = detractors.full_report(conn, time_q)
    if "error" not in rep:
        c = rep["count"]
        _sheet_from_table(wb, "Detractors",
                          f"Detractor Summary - {c.get('period','all')}",
                          ["Metric", "Value"],
                          [["Detractors", c["detractors"]],
                           ["Base",       c["base"]],
                           ["Rate",       f"{c['pct']}%"]])
        for b in rep["breakdowns"]:
            rows = [[r.get(b["dimension"]), r.get("detractors")] for r in b["rows"]]
            _sheet_from_table(wb, f"By {b['dimension']}"[:31],
                              f"Detractors by {b['dimension']}",
                              [b["dimension"], "Detractors"], rows, chart=True)

    if not wb.sheetnames:
        _sheet_from_table(wb, "Roma", "No data", ["Note"],
                          [["Add data with: roma add"]])
    else:
        _add_trend_sheet(wb, conn)
    return _save(wb, "cx_report", time_q)


# ============================================================
# TNPS DASHBOARD HELPERS
# ============================================================

def _write_df_to_sheet(wb: Workbook, name: str, title: str, df,
                       chart: bool = False) -> None:
    """Convert a DataFrame to rows/headers and write a branded sheet."""
    if df is None or df.empty:
        return
    headers = [str(c) for c in df.columns]
    rows    = []
    for _, row in df.iterrows():
        r = []
        for v in row:
            import numpy as np
            if hasattr(v, "item"):
                v = v.item()
            elif hasattr(v, "__class__") and v.__class__.__name__ == "Timestamp":
                v = str(v)
            r.append(v)
        rows.append(r)
    _sheet_from_table(wb, name[:31], title, headers, rows, chart=chart)


def _write_toc_sheet(wb: Workbook, sheet_names: list) -> None:
    """Table of Contents sheet with hyperlinks to every other sheet."""
    ws = wb.create_sheet("Table_of_Contents", 1)
    ws.sheet_view.showGridLines = False
    ws.merge_cells("B2:E2")
    t           = ws["B2"]
    t.value     = "Table of Contents"
    t.font      = Font(name="Arial", size=16, bold=True, color=WHITE)
    t.fill      = PatternFill("solid", fgColor=BRAND_RED)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height    = 28
    ws.column_dimensions["B"].width = 4
    ws.column_dimensions["C"].width = 35
    ws.column_dimensions["D"].width = 20

    ws.cell(row=3, column=3, value="Sheet Name").font  = Font(bold=True, name="Arial")
    ws.cell(row=3, column=4, value="Navigate To").font = Font(bold=True, name="Arial")

    for idx, name in enumerate(sheet_names, start=4):
        ws.cell(row=idx, column=3, value=name).font = Font(name="Arial", size=10)
        link_cell           = ws.cell(row=idx, column=4, value=f"Go → {name}")
        link_cell.hyperlink = f"#'{name}'!A1"
        link_cell.font      = Font(name="Arial", size=10, color="0563C1", underline="single")


def _apply_trend_colors_sheet(ws, df) -> None:
    """Color-code Trend column in ShortCode pivot (↑ = red, ↓ = green)."""
    if df is None or "Trend" not in df.columns:
        return
    trend_col_idx = list(df.columns).index("Trend") + 1
    for row_idx in range(2, len(df) + 2):
        cell = ws.cell(row=row_idx, column=trend_col_idx)
        val  = str(cell.value or "")
        if val.startswith("↑") or "NEW" in val:
            cell.fill = PatternFill("solid", start_color="FFD7D7")
            cell.font = Font(name="Arial", size=10, color=BRAND_RED, bold=True)
        elif val.startswith("↓") or "STOPPED" in val:
            cell.fill = PatternFill("solid", start_color="D7F0D7")
            cell.font = Font(name="Arial", size=10, color="2E8B57", bold=True)
        else:
            cell.fill = PatternFill("solid", start_color="F5F5F5")


def _write_dashboard_sheet(wb: Workbook, kpi, daily) -> None:
    """Sheet 1 = Dashboard: KPI cards + two line charts."""
    ws        = wb.active
    ws.title  = "Dashboard"
    ws.sheet_view.showGridLines = False

    def kv(metric):
        if kpi is None or kpi.empty:
            return "N/A"
        row = kpi[kpi["Metric"] == metric]
        return row["Value"].iloc[0] if not row.empty else "N/A"

    def dv(metric):
        if kpi is None or kpi.empty or "vs_Prev_Month" not in kpi.columns:
            return ""
        row = kpi[kpi["Metric"] == metric]
        return row["vs_Prev_Month"].iloc[0] if not row.empty else ""

    # ── Title banner ──
    ws.merge_cells("B2:K3")
    t           = ws["B2"]
    t.value     = "TNPS Detractor Analytics & Forecasting Dashboard"
    t.font      = Font(name="Arial", size=20, bold=True, color=WHITE)
    t.fill      = PatternFill("solid", fgColor=DARK)
    t.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells("B4:K4")
    sub           = ws["B4"]
    sub.value     = (
        f"Period: {kv('Date Range Start')}  →  {kv('Date Range End')}"
        f"   |   {kv('Number of Days Covered')} days covered"
    )
    sub.font      = Font(name="Arial", size=11, italic=True, color="666666")
    sub.alignment = Alignment(horizontal="center", vertical="center")

    # ── 6 KPI cards ──
    headline = [
        ("Total Surveys",     "Total Surveys Sent",    DARK),
        ("Detractors",        "Detractors (Q1 0-6)",   BRAND_RED),
        ("Detractor Rate %",  "Detractor Rate %",      BRAND_RED),
        ("NPS Score",         "NPS Score",             "2E8B57"),
        ("Completion Rate %", "Completion Rate %",     BRAND_RED),
        ("Avg Surveys / Day", "Average Surveys / Day", DARK),
    ]
    row_start = 6
    for i, (title, metric_name, color) in enumerate(headline):
        col = 2 + (i % 3) * 3
        r   = row_start + (i // 3) * 4
        val   = kv(metric_name)
        delta = dv(metric_name)

        ws.merge_cells(start_row=r, start_column=col, end_row=r, end_column=col + 2)
        c           = ws.cell(row=r, column=col)
        c.value     = title
        c.font      = Font(name="Arial", size=10, bold=True, color=WHITE)
        c.fill      = PatternFill("solid", fgColor=color)
        c.alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells(start_row=r+1, start_column=col, end_row=r+2, end_column=col+2)
        c           = ws.cell(row=r+1, column=col)
        c.value     = val
        c.font      = Font(name="Arial", size=22, bold=True, color=DARK)
        c.fill      = PatternFill("solid", fgColor="F5F5F5")
        c.alignment = Alignment(horizontal="center", vertical="center")

        if delta not in ("", "N/A"):
            ws.merge_cells(start_row=r+3, start_column=col, end_row=r+3, end_column=col+2)
            dc = ws.cell(row=r+3, column=col)
            try:
                delta_f = float(delta)
                arrow   = "▲" if delta_f > 0 else "▼"
                d_color = BRAND_RED if delta_f > 0 else "2E8B57"
            except (ValueError, TypeError):
                arrow = ""; d_color = "888888"
            dc.value     = f"{arrow} {delta} vs prev month"
            dc.font      = Font(name="Arial", size=9, color=d_color, bold=True)
            dc.alignment = Alignment(horizontal="center", vertical="center")

    for col_l in ["B", "C", "D", "E", "F", "G", "H", "I", "J"]:
        ws.column_dimensions[col_l].width = 14
    for r in range(2, 24):
        ws.row_dimensions[r].height = 22

    # ── Chart data block (hidden columns M-P) ──
    if daily is not None and not daily.empty and "Date" in daily.columns:
        chart_start_col = 13
        ws.cell(row=2, column=chart_start_col,     value="Date")
        ws.cell(row=2, column=chart_start_col + 1, value="Surveys_Sent")
        ws.cell(row=2, column=chart_start_col + 2, value="Detractors")
        ws.cell(row=2, column=chart_start_col + 3, value="Detractor_Rate_%")
        for i, (_, row_d) in enumerate(daily.iterrows(), start=3):
            ws.cell(row=i, column=chart_start_col,     value=str(row_d["Date"]))
            ws.cell(row=i, column=chart_start_col + 1, value=float(row_d.get("Surveys_Sent", 0)))
            ws.cell(row=i, column=chart_start_col + 2, value=float(row_d.get("Detractors",   0)))
            ws.cell(row=i, column=chart_start_col + 3, value=float(row_d.get("Detractor_Rate_%", 0)))
        end_row = 2 + len(daily)
        from openpyxl.utils import get_column_letter as _gcl
        for c in range(chart_start_col, chart_start_col + 4):
            ws.column_dimensions[_gcl(c)].width = 2   # hidden-ish

        # Chart 1 — Volume + Detractors
        ch            = LineChart()
        ch.title      = "Daily Survey Volume & Detractors"
        ch.style      = 2
        ch.height     = 10; ch.width = 24
        data_ref = Reference(ws, min_col=chart_start_col + 1, max_col=chart_start_col + 2,
                             min_row=2, max_row=end_row)
        cats     = Reference(ws, min_col=chart_start_col, min_row=3, max_row=end_row)
        ch.add_data(data_ref, titles_from_data=True)
        ch.set_categories(cats)
        ws.add_chart(ch, "B25")

        # Chart 2 — Detractor Rate %
        ch2           = LineChart()
        ch2.title     = "Daily Detractor Rate %"
        ch2.style     = 12
        ch2.height    = 10; ch2.width = 24
        data_ref2 = Reference(ws, min_col=chart_start_col + 3, max_col=chart_start_col + 3,
                              min_row=2, max_row=end_row)
        ch2.add_data(data_ref2, titles_from_data=True)
        ch2.set_categories(cats)
        if ch2.series:
            ch2.series[0].graphicalProperties.line.solidFill = BRAND_RED
        ws.add_chart(ch2, "B47")


# ============================================================
# TNPS DASHBOARD — MAIN ENTRY POINT
# ============================================================

def export_tnps_dashboard(conn, time_q: str = "") -> Path:
    """Generate the full 30+ sheet TNPS dashboard workbook.

    Sheets written (empty ones are silently skipped):
      Dashboard            — KPI cards + 2 line charts
      Table_of_Contents    — clickable hyperlinks to every sheet
      Executive_Summary    — narrative KPI summary block
      KPI_Summary          — all computed KPIs with prev-month delta
      Daily_Trend          — surveys + detractors per day
      Monthly_Trend        — same, aggregated by month
      ShortCode_Daily_Pivot — short-code × day heatmap (↑/↓ trend colors)
      ShortCode_Catalog    — short-code master list with NPS + rate
      Queue_Catalog        — agent queue master list
      Mapping_Coverage     — how many responses have Lev1/2/3/4 mapped
      Forecast             — 30-day Holt-Winters forecast
      Per_Queue_Forecast   — per-queue 30-day forecast (top 5 queues)
      Top10_Bottom10       — best and worst short-codes by NPS
      NPS_Waterfall        — promoters – passives – detractors waterfall
      SLA_Breach_Heatmap   — hour × day breach heatmap
      By_OwnerTeam         — detractors by owner team
      By_Substatus         — detractors by sub-status
      By_CallType          — detractors by call type
      By_ProdType          — detractors by product type
      Channel_Comparison   — IVR / app / web / walk-in comparison
      FCR_Impact           — first-call-resolution vs NPS impact
      By_Reachability      — reachable / not-reachable breakdown
      By_Lev1 … By_Lev4   — detractors by mapping level 1-4
      By_Site              — detractors by site
      Heatmap_Lev3_x_Lev4 — cross-tab heatmap Lev3 rows × Lev4 cols
      Hour_Pattern         — NPS score by hour of day
      DayOfWeek_Pattern    — NPS score by day of week
      Toxic_Combos         — high-detractor dimension combinations
      Velocity_Alerts      — week-over-week spike alerts
      Cohort_Analysis      — monthly cohort detractor rates
      Repeat_Detractors    — customers who appear as detractors 2+ times
      Duplicate_Surveys    — duplicate responses within 24 h per MSISDN
      Agent_Peer_Benchmark — agent NPS vs team average
      Agent_Ranking        — ranked agent list by NPS / detractor rate
    """
    import pandas as pd
    from . import tnps_analytics as ta
    from . import forecast as fc

    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        raise ValueError("No TNPS data found. Load survey files first: roma add <file>")

    # Wrapper: return empty DataFrame instead of crashing
    def _safe(fn, *args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            return result if result is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    # ── Build all analytics ──────────────────────────────────
    kpi        = _safe(ta.build_kpi_summary,             df, cols)
    daily      = _safe(ta.build_daily_trend,             df, cols)
    monthly    = _safe(ta.build_monthly_trend,           df, cols)
    sc_pivot   = _safe(ta.build_shortcode_daily_pivot,   df, cols)
    sc_catalog = _safe(ta.build_shortcode_catalog,       df, cols)
    q_catalog  = _safe(ta.build_queue_catalog,           df, cols)
    map_cov    = _safe(ta.build_mapping_coverage,        df, cols)
    duplicates = _safe(ta.build_duplicate_surveys,       df, cols)
    top_bottom = _safe(ta.build_top_bottom,              df, cols)
    waterfall  = _safe(ta.build_nps_waterfall,           df, cols)
    sla_breach = _safe(ta.build_sla_breach_heatmap,      df, cols)
    heatmap    = _safe(ta.build_lev3_lev4_heatmap,       df, cols)
    hour_p     = _safe(ta.build_hour_pattern,            df, cols)
    dow_p      = _safe(ta.build_dow_pattern,             df, cols)
    repeats    = _safe(ta.build_repeat_detractors,       df, cols)
    cohort     = _safe(ta.build_cohort_analysis,         df, cols)
    toxic      = _safe(ta.build_toxic_combos,            df, cols)
    velocity   = _safe(ta.build_velocity_alerts,         df, cols)
    peer_bench = _safe(ta.build_agent_peer_benchmark,    df, cols)
    agent_rk   = _safe(ta.build_agent_ranking,           df, cols)
    channel_cmp= _safe(ta.build_channel_comparison,      df, cols)
    fcr_impact = _safe(ta.build_fcr_impact,              df, cols)

    # ── Forecasting ──────────────────────────────────────────
    forecast_df  = _safe(fc.build_forecast,           daily, 30)
    queue_fc     = _safe(fc.build_per_queue_forecast, df, cols, daily, 30, 5)
    exec_summary = _safe(ta.build_executive_summary,  df, cols, kpi, daily, forecast_df)

    # ── Conditional dimension breakdowns ────────────────────
    def _bd(col_key):
        col = cols.get(col_key) or ""
        return _safe(ta.breakdown_by, df, col, cols) if col else pd.DataFrame()

    by_owner_team  = _bd("owner_team")
    by_substatus   = _bd("substatus")
    by_call_type   = _bd("call_type")
    by_prod_type   = _bd("prod_type")
    by_reachability= _bd("reachability")
    by_lev1        = _bd("lev1")
    by_lev2        = _bd("lev2")
    by_lev3        = _bd("lev3")
    by_lev4        = _bd("lev4")
    by_site        = _bd("site")

    # ── Build workbook ───────────────────────────────────────
    wb = Workbook()

    _write_dashboard_sheet(wb, kpi, daily)
    created_sheets = ["Dashboard"]

    _SHEETS = [
        # (sheet_name,          dataframe,      display_title)
        ("Executive_Summary",   exec_summary,   "Executive Summary"),
        ("KPI_Summary",         kpi,            "KPI Summary"),
        ("Daily_Trend",         daily,          "Daily Trend"),
        ("Monthly_Trend",       monthly,        "Monthly Trend"),
        ("ShortCode_Daily_Pivot", sc_pivot,     "Short Code × Day Pivot"),
        ("ShortCode_Catalog",   sc_catalog,     "Short Code Catalog"),
        ("Queue_Catalog",       q_catalog,      "Agent Queue Catalog"),
        ("Mapping_Coverage",    map_cov,        "Mapping Coverage Report"),
        ("Forecast",            forecast_df,    "Forecast (Holt-Winters)"),
        ("Per_Queue_Forecast",  queue_fc,       "Per-Queue Forecast"),
        ("Top10_Bottom10",      top_bottom,     "Top 10 / Bottom 10"),
        ("NPS_Waterfall",       waterfall,      "NPS Waterfall"),
        ("SLA_Breach_Heatmap",  sla_breach,     "SLA Breach Heatmap"),
        ("By_OwnerTeam",        by_owner_team,  "Detractors by Owner Team"),
        ("By_Substatus",        by_substatus,   "Detractors by Sub-Status"),
        ("By_CallType",         by_call_type,   "Detractors by Call Type"),
        ("By_ProdType",         by_prod_type,   "Detractors by Product Type"),
        ("Channel_Comparison",  channel_cmp,    "Channel Comparison"),
        ("FCR_Impact",          fcr_impact,     "FCR Impact"),
        ("By_Reachability",     by_reachability,"Detractors by Reachability"),
        ("By_Lev1",             by_lev1,        "By Mapping Lev 1"),
        ("By_Lev2",             by_lev2,        "By Mapping Lev 2"),
        ("By_Lev3",             by_lev3,        "By Mapping Lev 3"),
        ("By_Lev4",             by_lev4,        "By Mapping Lev 4"),
        ("By_Site",             by_site,        "By Site"),
        ("Heatmap_Lev3_x_Lev4", heatmap,       "Heatmap Lev3 × Lev4"),
        ("Hour_Pattern",        hour_p,         "Hour of Day Pattern"),
        ("DayOfWeek_Pattern",   dow_p,          "Day of Week Pattern"),
        ("Toxic_Combos",        toxic,          "Toxic Dimension Combos"),
        ("Velocity_Alerts",     velocity,       "Velocity Alerts (WoW)"),
        ("Cohort_Analysis",     cohort,         "Cohort Analysis"),
        ("Repeat_Detractors",   repeats,        "Repeat Detractors"),
        ("Duplicate_Surveys",   duplicates,     "Duplicate Surveys (24h)"),
        ("Agent_Peer_Benchmark",peer_bench,     "Agent Peer Benchmarking"),
        ("Agent_Ranking",       agent_rk,       "Agent Ranking"),
    ]

    for sname, sdf, stitle in _SHEETS:
        if sdf is None or (hasattr(sdf, "empty") and sdf.empty):
            continue                    # skip sheets with no data
        _write_df_to_sheet(wb, sname, stitle, sdf)
        if sname == "ShortCode_Daily_Pivot" and sname[:31] in wb.sheetnames:
            _apply_trend_colors_sheet(wb[sname[:31]], sdf)
        created_sheets.append(sname[:31])

    _write_toc_sheet(wb, created_sheets)   # insert TOC as sheet 2
    return _save(wb, "tnps_dashboard", time_q)
```

---

## Sheet Map (35 possible, empty ones skipped)

| # | Sheet Name | What it contains |
|---|-----------|-----------------|
| 1 | **Dashboard** | 6 KPI cards + 2 line charts (always created) |
| 2 | **Table_of_Contents** | Clickable links to every sheet |
| 3 | Executive_Summary | Date range, total surveys, detractor rate, NPS, top queue, forecast snippet |
| 4 | KPI_Summary | All KPIs with prev-month delta |
| 5 | Daily_Trend | Surveys + detractors per calendar day |
| 6 | Monthly_Trend | Same, rolled up by month |
| 7 | ShortCode_Daily_Pivot | Short-code × day heatmap (↑ red, ↓ green trend colors) |
| 8 | ShortCode_Catalog | Short-code master: NPS, rate, count |
| 9 | Queue_Catalog | Agent queue master list |
| 10 | Mapping_Coverage | % responses with Lev1/2/3/4 mapped |
| 11 | Forecast | 30-day Holt-Winters forecast |
| 12 | Per_Queue_Forecast | 30-day forecast per top-5 queue |
| 13 | Top10_Bottom10 | Best and worst short-codes by NPS |
| 14 | NPS_Waterfall | Promoters − Passives − Detractors bar |
| 15 | SLA_Breach_Heatmap | Hour × day SLA breach heatmap |
| 16 | By_OwnerTeam | Detractors by owner team (if column exists) |
| 17 | By_Substatus | Detractors by sub-status |
| 18 | By_CallType | Detractors by call type |
| 19 | By_ProdType | Detractors by product type |
| 20 | Channel_Comparison | IVR vs app vs web vs walk-in |
| 21 | FCR_Impact | First-call-resolution vs NPS |
| 22 | By_Reachability | Reachable / not-reachable |
| 23 | By_Lev1 … By_Lev4 | Detractors by mapping hierarchy level 1-4 |
| 27 | By_Site | Detractors by site |
| 28 | Heatmap_Lev3_x_Lev4 | Cross-tab: Lev3 rows × Lev4 columns |
| 29 | Hour_Pattern | NPS score by hour of day |
| 30 | DayOfWeek_Pattern | NPS score by day of week |
| 31 | Toxic_Combos | High-detractor dimension combinations |
| 32 | Velocity_Alerts | Week-over-week spike alerts |
| 33 | Cohort_Analysis | Monthly cohort detractor retention |
| 34 | Repeat_Detractors | Customers who appear 2+ times as detractor |
| 35 | Duplicate_Surveys | Duplicate responses within 24 h per MSISDN |
| 36 | Agent_Peer_Benchmark | Agent NPS vs team average |
| 37 | Agent_Ranking | Ranked agent list |

---

## Usage

```bash
# Inside roma chat:
export tnps dashboard
export tnps dashboard last month
export tnps dashboard Q1 2025

# From CLI:
roma export dashboard
roma export dashboard --time "last month"

# Directly from Python:
from roma import database, config
from roma.export_excel import export_tnps_dashboard

config.ensure_dirs()
conn = database.connect()
path = export_tnps_dashboard(conn, "last month")
print(f"Saved to: {path}")
```

---

## Design Principles

| Principle | How it's applied |
|-----------|-----------------|
| **Offline-first** | Uses only `openpyxl` + `pandas` — no external APIs |
| **Fail-safe** | Every analytics call wrapped in `_safe()` — bad data never crashes the export |
| **Brand-consistent** | e& red `#E00800`, dark `#1A1A1A`, Arial font throughout |
| **Empty-sheet-free** | Sheets with no data are silently skipped |
| **Auto-width** | `_autofit()` calculates column width from actual content |
| **Frozen panes** | Header row is always frozen for easy scrolling |

---

## Dependencies in Roma

```
export_excel.py
    ├── roma.tnps_analytics    ← all 25+ build_* functions
    ├── roma.forecast          ← build_forecast, build_per_queue_forecast
    ├── roma.kpis              ← compute()
    ├── roma.detractors        ← full_report()
    ├── roma.analyst           ← drivers()
    └── roma.config            ← REPORTS_DIR, ensure_dirs()
```

---

*Generated by Roma — CX Analytics for e& | Fully offline, no API keys required*
