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
DARK = "1A1A1A"
LIGHT = "FCE9E7"      # pale red for banding
GREY = "F2F2F2"
WHITE = "FFFFFF"

_THIN = Side(style="thin", color="DDDDDD")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _title(ws, text: str, span: int) -> int:
    """Write a brand title bar across `span` columns. Return next row."""
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, span))
    c = ws.cell(row=1, column=1, value=text)
    c.font = Font(name="Arial", size=14, bold=True, color=WHITE)
    c.fill = PatternFill("solid", fgColor=BRAND_RED)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 26
    sub = ws.cell(row=2, column=1,
                  value=f"Roma - CX analytics  |  generated {datetime.now():%Y-%m-%d %H:%M}")
    sub.font = Font(name="Arial", size=8, italic=True, color="888888")
    return 4  # data starts at row 4


def _header_row(ws, row: int, headers: list[str]) -> None:
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=row, column=j, value=str(h))
        c.font = Font(name="Arial", size=10, bold=True, color=WHITE)
        c.fill = PatternFill("solid", fgColor=DARK)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = _BORDER


def _data_rows(ws, start_row: int, rows: list[list[Any]]) -> int:
    for i, r in enumerate(rows):
        row = start_row + i
        for j, v in enumerate(r, start=1):
            c = ws.cell(row=row, column=j, value=v)
            c.font = Font(name="Arial", size=10, color=DARK)
            c.fill = PatternFill("solid", fgColor=(LIGHT if i % 2 else WHITE))
            c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
            c.border = _BORDER
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
    ws = wb.create_sheet(title=name[:31])
    start = _title(ws, title, len(headers))
    _header_row(ws, start, headers)
    end = _data_rows(ws, start + 1, rows)
    _autofit(ws, headers, rows)
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    ws.sheet_view.showGridLines = False
    # Optional bar chart for 2-column category/number tables.
    if chart and len(headers) == 2 and rows:
        ch = BarChart()
        ch.type = "col"
        ch.title = title
        ch.height = 7
        ch.width = 14
        data = Reference(ws, min_col=2, min_row=start, max_row=end - 1)
        cats = Reference(ws, min_col=1, min_row=start + 1, max_row=end - 1)
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(cats)
        ch.legend = None
        if ch.series:
            ch.series[0].graphicalProperties.solidFill = BRAND_RED
        anchor_col = get_column_letter(len(headers) + 2)
        ws.add_chart(ch, f"{anchor_col}{start}")


def export_detractor_report(conn, time_q: str = "") -> Path:
    """A multi-sheet detractor workbook: summary, each breakdown, repeats."""
    rep = detractors.full_report(conn, time_q)
    if "error" in rep:
        raise ValueError(rep["error"])
    wb = Workbook()
    wb.remove(wb.active)

    c = rep["count"]
    period = c.get("period", "all data")
    # Summary sheet
    _sheet_from_table(
        wb, "Summary", f"Detractor Summary - {period}",
        ["Metric", "Value"],
        [["Period", period],
         ["Total respondents", c["base"]],
         ["Detractors (score 0-6)", c["detractors"]],
         ["Detractor rate", f"{c['pct']}%"],
         ["Scored on", c["nps"]]],
    )
    # Breakdown sheets
    for b in rep["breakdowns"]:
        rows = [[r.get(b["dimension"]), r.get("detractors")] for r in b["rows"]]
        _sheet_from_table(wb, f"By {b['dimension']}"[:31],
                          f"Detractors by {b['dimension']} - {period}",
                          [b["dimension"], "Detractors"], rows, chart=True)
    # Repeated
    rp = rep["repeated"]
    if "error" not in rp and rp["rows"]:
        rows = [[r.get("customer"), r.get("times")] for r in rp["rows"]]
        _sheet_from_table(wb, "Repeated", f"Repeated Detractors - {period}",
                          ["Customer", "Times"], rows)

    return _save(wb, "detractor_report", time_q)


def export_kpis(conn) -> Path:
    rows = [[k["name"], k["value"], k["detail"], k["table"]]
            for k in kpis.compute(conn)]
    wb = Workbook()
    wb.remove(wb.active)
    _sheet_from_table(wb, "KPIs", "CX KPIs",
                      ["KPI", "Value", "Detail", "Source"], rows or [["-", "-", "-", "-"]])
    return _save(wb, "kpis", "")


def export_drivers(conn, target: str | None = None) -> Path:
    d = analyst.drivers(conn, target)
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


def _add_trend_sheet(wb, conn) -> None:
    """Monthly detractor-rate trend sheet with a line chart, if dates exist."""
    import pandas as pd
    from . import detractors, timefilter
    table, nps, idc, datec, names = detractors._detractor_table(conn)
    if not table or not datec:
        return
    df = pd.read_sql_query(f'SELECT "{datec}", "{nps}" FROM "{table}"', conn)
    dts = pd.to_datetime(df[datec], errors="coerce")
    score = pd.to_numeric(df[nps], errors="coerce")
    mask = dts.notna() & score.notna()
    if mask.sum() < 4:
        return
    g = pd.DataFrame({"m": dts[mask].dt.to_period("M").astype(str),
                      "det": (score[mask] <= 6).astype(int)})
    grp = g.groupby("m")["det"].mean().mul(100).round(1)
    if len(grp) < 2:
        return
    rows = [[m, float(v)] for m, v in grp.items()]
    ws = wb.create_sheet(title="Trend"[:31])
    start = _title(ws, "Detractor rate by month (%)", 2)
    _header_row(ws, start, ["Month", "Detractor %"])
    end = _data_rows(ws, start + 1, rows)
    _autofit(ws, ["Month", "Detractor %"], rows)
    ws.sheet_view.showGridLines = False
    ch = LineChart()
    ch.title = "Detractor rate by month (%)"
    ch.height = 8; ch.width = 16
    data = Reference(ws, min_col=2, min_row=start, max_row=end - 1)
    cats = Reference(ws, min_col=1, min_row=start + 1, max_row=end - 1)
    ch.add_data(data, titles_from_data=True)
    ch.set_categories(cats)
    ch.legend = None
    if ch.series:
        ch.series[0].graphicalProperties.line.solidFill = BRAND_RED
        ch.series[0].graphicalProperties.line.width = 28000
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
                          [["Detractors", c["detractors"]], ["Base", c["base"]],
                           ["Rate", f"{c['pct']}%"]])
        for b in rep["breakdowns"]:
            rows = [[r.get(b["dimension"]), r.get("detractors")] for r in b["rows"]]
            _sheet_from_table(wb, f"By {b['dimension']}"[:31],
                              f"Detractors by {b['dimension']}",
                              [b["dimension"], "Detractors"], rows, chart=True)

    if not wb.sheetnames:
        _sheet_from_table(wb, "Roma", "No data", ["Note"], [["Add data with: roma add"]])
    else:
        _add_trend_sheet(wb, conn)
    return _save(wb, "cx_report", time_q)


def _save(wb: Workbook, kind: str, time_q: str) -> Path:
    import re as _re
    config.ensure_dirs()
    # Keep only a short, clean tag (month/quarter/year words), drop the rest.
    raw = time_q.strip().lower()
    m = _re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|"
                   r"20\d{2}|last month|this month|this year)\w*", raw)
    tag = "_" + _re.sub(r"\W+", "", m.group(0)) if m else ""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = config.REPORTS_DIR / f"{kind}{tag}_{stamp}.xlsx"
    wb.save(out)
    return out


# ============================================================
# TNPS DASHBOARD (30+ sheets)
# ============================================================

def _write_df_to_sheet(wb: Workbook, name: str, title: str, df: "pd.DataFrame",
                       chart: bool = False) -> None:
    """Convert a DataFrame to rows/headers and write a branded sheet."""
    if df is None or df.empty:
        return
    headers = [str(c) for c in df.columns]
    rows = []
    for _, row in df.iterrows():
        r = []
        for v in row:
            import numpy as np
            if hasattr(v, "item"):  # numpy scalar
                v = v.item()
            elif hasattr(v, "__class__") and v.__class__.__name__ in ("Timestamp",):
                v = str(v)
            r.append(v)
        rows.append(r)
    _sheet_from_table(wb, name[:31], title, headers, rows, chart=chart)


def _write_toc_sheet(wb: Workbook, sheet_names: list) -> None:
    """Table of Contents sheet with hyperlinks."""
    ws = wb.create_sheet("Table_of_Contents", 1)
    ws.sheet_view.showGridLines = False
    ws.merge_cells("B2:E2")
    t = ws["B2"]
    t.value = "Table of Contents"
    t.font = Font(name="Arial", size=16, bold=True, color=WHITE)
    t.fill = PatternFill("solid", fgColor=BRAND_RED)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 28
    ws.column_dimensions["B"].width = 4
    ws.column_dimensions["C"].width = 35
    ws.column_dimensions["D"].width = 20

    ws.cell(row=3, column=3, value="Sheet Name").font = Font(bold=True, name="Arial")
    ws.cell(row=3, column=4, value="Navigate To").font = Font(bold=True, name="Arial")

    for idx, name in enumerate(sheet_names, start=4):
        ws.cell(row=idx, column=3, value=name).font = Font(name="Arial", size=10)
        link_cell = ws.cell(row=idx, column=4, value=f"Go → {name}")
        link_cell.hyperlink = f"#'{name}'!A1"
        link_cell.font = Font(name="Arial", size=10, color="0563C1", underline="single")


def _apply_trend_colors_sheet(ws, df: "pd.DataFrame") -> None:
    """Color-code the Trend column in ShortCode pivot."""
    if df is None or "Trend" not in df.columns:
        return
    trend_col_idx = list(df.columns).index("Trend") + 1
    for row_idx in range(2, len(df) + 2):
        cell = ws.cell(row=row_idx, column=trend_col_idx)
        val = str(cell.value or "")
        if val.startswith("↑") or "NEW" in val:
            cell.fill = PatternFill("solid", start_color="FFD7D7")
            cell.font = Font(name="Arial", size=10, color=BRAND_RED, bold=True)
        elif val.startswith("↓") or "STOPPED" in val:
            cell.fill = PatternFill("solid", start_color="D7F0D7")
            cell.font = Font(name="Arial", size=10, color="2E8B57", bold=True)
        else:
            cell.fill = PatternFill("solid", start_color="F5F5F5")


def _write_dashboard_sheet(wb: Workbook, kpi: "pd.DataFrame",
                            daily: "pd.DataFrame") -> None:
    """KPI cards + charts on the Dashboard sheet."""
    ws = wb.active
    ws.title = "Dashboard"
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

    # Title banner
    ws.merge_cells("B2:K3")
    t = ws["B2"]
    t.value = "TNPS Detractor Analytics & Forecasting Dashboard"
    t.font = Font(name="Arial", size=20, bold=True, color=WHITE)
    t.fill = PatternFill("solid", fgColor=DARK)
    t.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells("B4:K4")
    sub = ws["B4"]
    sub.value = (
        f"Period: {kv('Date Range Start')}  →  {kv('Date Range End')}"
        f"   |   {kv('Number of Days Covered')} days covered"
    )
    sub.font = Font(name="Arial", size=11, italic=True, color="666666")
    sub.alignment = Alignment(horizontal="center", vertical="center")

    # KPI cards
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
        r = row_start + (i // 3) * 4
        val = kv(metric_name)
        delta = dv(metric_name)

        ws.merge_cells(start_row=r, start_column=col, end_row=r, end_column=col + 2)
        c = ws.cell(row=r, column=col)
        c.value = title
        c.font = Font(name="Arial", size=10, bold=True, color=WHITE)
        c.fill = PatternFill("solid", fgColor=color)
        c.alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells(start_row=r+1, start_column=col, end_row=r+2, end_column=col+2)
        c = ws.cell(row=r+1, column=col)
        c.value = val
        c.font = Font(name="Arial", size=22, bold=True, color=DARK)
        c.fill = PatternFill("solid", fgColor="F5F5F5")
        c.alignment = Alignment(horizontal="center", vertical="center")

        if delta not in ("", "N/A"):
            ws.merge_cells(start_row=r+3, start_column=col, end_row=r+3, end_column=col+2)
            dc = ws.cell(row=r+3, column=col)
            try:
                delta_f = float(delta)
                arrow = "▲" if delta_f > 0 else "▼"
                d_color = BRAND_RED if delta_f > 0 else "2E8B57"
            except (ValueError, TypeError):
                arrow = ""; d_color = "888888"
            dc.value = f"{arrow} {delta} vs prev month"
            dc.font = Font(name="Arial", size=9, color=d_color, bold=True)
            dc.alignment = Alignment(horizontal="center", vertical="center")

    for col_l in ["B", "C", "D", "E", "F", "G", "H", "I", "J"]:
        ws.column_dimensions[col_l].width = 14
    for r in range(2, 24):
        ws.row_dimensions[r].height = 22

    # Chart data block
    if daily is not None and not daily.empty and "Date" in daily.columns:
        chart_start_col = 13
        ws.cell(row=2, column=chart_start_col, value="Date")
        ws.cell(row=2, column=chart_start_col+1, value="Surveys_Sent")
        ws.cell(row=2, column=chart_start_col+2, value="Detractors")
        ws.cell(row=2, column=chart_start_col+3, value="Detractor_Rate_%")
        for i, (_, row_d) in enumerate(daily.iterrows(), start=3):
            ws.cell(row=i, column=chart_start_col, value=str(row_d["Date"]))
            ws.cell(row=i, column=chart_start_col+1,
                    value=float(row_d.get("Surveys_Sent", 0)))
            ws.cell(row=i, column=chart_start_col+2,
                    value=float(row_d.get("Detractors", 0)))
            ws.cell(row=i, column=chart_start_col+3,
                    value=float(row_d.get("Detractor_Rate_%", 0)))
        end_row = 2 + len(daily)
        for c in range(chart_start_col, chart_start_col + 4):
            from openpyxl.utils import get_column_letter as _gcl
            ws.column_dimensions[_gcl(c)].width = 2

        ch = LineChart()
        ch.title = "Daily Survey Volume & Detractors"
        ch.style = 2
        ch.height = 10; ch.width = 24
        data_ref = Reference(ws, min_col=chart_start_col+1, max_col=chart_start_col+2,
                             min_row=2, max_row=end_row)
        cats = Reference(ws, min_col=chart_start_col, min_row=3, max_row=end_row)
        ch.add_data(data_ref, titles_from_data=True)
        ch.set_categories(cats)
        ws.add_chart(ch, "B25")

        ch2 = LineChart()
        ch2.title = "Daily Detractor Rate %"
        ch2.style = 12
        ch2.height = 10; ch2.width = 24
        data_ref2 = Reference(ws, min_col=chart_start_col+3, max_col=chart_start_col+3,
                              min_row=2, max_row=end_row)
        ch2.add_data(data_ref2, titles_from_data=True)
        ch2.set_categories(cats)
        if ch2.series:
            ch2.series[0].graphicalProperties.line.solidFill = BRAND_RED
        ws.add_chart(ch2, "B47")


def export_tnps_dashboard(conn, time_q: str = "") -> Path:
    """Generate the full 30+ sheet TNPS dashboard.

    Sheets (empty ones are skipped):
    Dashboard, Executive_Summary, KPI_Summary, Daily_Trend, Monthly_Trend,
    ShortCode_Daily_Pivot, ShortCode_Catalog, Queue_Catalog, Mapping_Coverage,
    Forecast, Per_Queue_Forecast, Top10_Bottom10, NPS_Waterfall,
    SLA_Breach_Heatmap, By_OwnerTeam, By_Substatus, By_CallType, By_ProdType,
    Channel_Comparison, FCR_Impact, By_Reachability, By_Region,
    Heatmap_Lev3_x_Lev4, Hour_Pattern, DayOfWeek_Pattern, Toxic_Combos,
    Velocity_Alerts, Cohort_Analysis, Repeat_Detractors, Duplicate_Surveys,
    Agent_Peer_Benchmark, Agent_Ranking, Q2_Attitude_vs_TNPS,
    Table_of_Contents
    """
    import pandas as pd
    from . import tnps_analytics as ta
    from . import forecast as fc

    df, cols = ta.load_tnps_df(conn)
    if df.empty:
        raise ValueError("No TNPS data found in the database. Load survey files first.")

    def _safe(fn, *args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            return result if result is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    # Build all analytics
    kpi = _safe(ta.build_kpi_summary, df, cols)
    daily = _safe(ta.build_daily_trend, df, cols)
    monthly = _safe(ta.build_monthly_trend, df, cols)
    sc_pivot = _safe(ta.build_shortcode_daily_pivot, df, cols)
    sc_catalog = _safe(ta.build_shortcode_catalog, df, cols)
    q_catalog = _safe(ta.build_queue_catalog, df, cols)
    map_cov = _safe(ta.build_mapping_coverage, df, cols)
    duplicates = _safe(ta.build_duplicate_surveys, df, cols)
    top_bottom = _safe(ta.build_top_bottom, df, cols)
    waterfall = _safe(ta.build_nps_waterfall, df, cols)
    sla_breach = _safe(ta.build_sla_breach_heatmap, df, cols)
    heatmap = _safe(ta.build_lev3_lev4_heatmap, df, cols)
    hour_p = _safe(ta.build_hour_pattern, df, cols)
    dow_p = _safe(ta.build_dow_pattern, df, cols)
    repeats = _safe(ta.build_repeat_detractors, df, cols)
    cohort = _safe(ta.build_cohort_analysis, df, cols)
    toxic = _safe(ta.build_toxic_combos, df, cols)
    velocity = _safe(ta.build_velocity_alerts, df, cols)
    peer_bench = _safe(ta.build_agent_peer_benchmark, df, cols)
    agent_rk = _safe(ta.build_agent_ranking, df, cols)
    channel_cmp = _safe(ta.build_channel_comparison, df, cols)
    fcr_impact = _safe(ta.build_fcr_impact, df, cols)

    # Forecasting
    forecast_df = _safe(fc.build_forecast, daily, 30)
    queue_fc = _safe(fc.build_per_queue_forecast, df, cols, daily, 30, 5)
    exec_summary = _safe(ta.build_executive_summary, df, cols, kpi, daily, forecast_df)

    # Dimension breakdowns
    by_owner_team = _safe(ta.breakdown_by, df, cols.get("owner_team") or "", cols) if cols.get("owner_team") else pd.DataFrame()
    by_substatus = _safe(ta.breakdown_by, df, cols.get("substatus") or "", cols) if cols.get("substatus") else pd.DataFrame()
    by_call_type = _safe(ta.breakdown_by, df, cols.get("call_type") or "", cols) if cols.get("call_type") else pd.DataFrame()
    by_prod_type = _safe(ta.breakdown_by, df, cols.get("prod_type") or "", cols) if cols.get("prod_type") else pd.DataFrame()
    by_reachability = _safe(ta.breakdown_by, df, cols.get("reachability") or "", cols) if cols.get("reachability") else pd.DataFrame()
    by_lev1 = _safe(ta.breakdown_by, df, cols.get("lev1") or "", cols) if cols.get("lev1") else pd.DataFrame()
    by_lev2 = _safe(ta.breakdown_by, df, cols.get("lev2") or "", cols) if cols.get("lev2") else pd.DataFrame()
    by_lev3 = _safe(ta.breakdown_by, df, cols.get("lev3") or "", cols) if cols.get("lev3") else pd.DataFrame()
    by_lev4 = _safe(ta.breakdown_by, df, cols.get("lev4") or "", cols) if cols.get("lev4") else pd.DataFrame()
    by_site = _safe(ta.breakdown_by, df, cols.get("site") or "", cols) if cols.get("site") else pd.DataFrame()

    # Build workbook
    wb = Workbook()

    # Dashboard sheet (active sheet)
    _write_dashboard_sheet(wb, kpi, daily)
    created_sheets = ["Dashboard"]

    _SHEETS = [
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
        ("By_Reachability",     by_reachability, "Detractors by Reachability"),
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
        ("Agent_Peer_Benchmark", peer_bench,    "Agent Peer Benchmarking"),
        ("Agent_Ranking",       agent_rk,       "Agent Ranking"),
    ]

    for sname, sdf, stitle in _SHEETS:
        if sdf is None or (hasattr(sdf, "empty") and sdf.empty):
            continue
        _write_df_to_sheet(wb, sname, stitle, sdf)
        # Special: apply trend colors to ShortCode pivot
        if sname == "ShortCode_Daily_Pivot" and sname[:31] in wb.sheetnames:
            _apply_trend_colors_sheet(wb[sname[:31]], sdf)
        created_sheets.append(sname[:31])

    # Table of Contents (second sheet after Dashboard)
    _write_toc_sheet(wb, created_sheets)

    return _save(wb, "tnps_dashboard", time_q)
