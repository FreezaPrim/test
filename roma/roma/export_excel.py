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
