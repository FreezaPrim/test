#!/usr/bin/env python3
"""
tnps_dashboard_standalone.py
============================
Standalone TNPS Excel dashboard generator — zero Roma dependencies.

Usage:
    python tnps_dashboard_standalone.py data.csv
    python tnps_dashboard_standalone.py data.xlsx
    python tnps_dashboard_standalone.py data.csv --time "2025-01"
    python tnps_dashboard_standalone.py data.csv --out my_report.xlsx

Requirements:
    pip install pandas openpyxl numpy scipy
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ──────────────────────────────────────────────────────────────────────────────
# e& brand palette
# ──────────────────────────────────────────────────────────────────────────────
BRAND_RED = "E00800"
DARK      = "1A1A1A"
LIGHT     = "FCE9E7"
WHITE     = "FFFFFF"

_THIN   = Side(style="thin", color="DDDDDD")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════

def load_data(path: str | Path) -> pd.DataFrame:
    """Load CSV or Excel into a DataFrame. Tries common encodings."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls", ".xlsm"):
        return pd.read_excel(p, dtype=str)
    for enc in ("utf-8", "utf-8-sig", "cp1256", "latin-1"):
        try:
            return pd.read_csv(p, dtype=str, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot read file: {p}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — AUTO-DETECT COLUMNS
# ══════════════════════════════════════════════════════════════════════════════

def _find_col(df: pd.DataFrame, patterns: list[str]) -> str | None:
    """Return the first column name matching any pattern (case-insensitive)."""
    cols_lower = {c.lower(): c for c in df.columns}
    for pat in patterns:
        for lc, orig in cols_lower.items():
            if re.search(pat, lc):
                return orig
    return None


def detect_columns(df: pd.DataFrame) -> dict:
    """Auto-detect key columns. Returns a dict of role → column_name."""
    cols: dict[str, str | None] = {}

    # NPS / TNPS score
    cols["score"] = _find_col(df, [
        r"tnps", r"nps.*score", r"score.*nps", r"q1.*answer", r"answer.*q1",
        r"rating", r"score", r"note", r"grade",
    ])

    # Date
    cols["date"] = _find_col(df, [
        r"date", r"survey.*date", r"response.*date", r"created", r"timestamp",
        r"submit", r"tarikh", r"تاريخ",
    ])

    # MSISDN / phone
    cols["msisdn"] = _find_col(df, [
        r"msisdn", r"mobile", r"phone", r"telephone", r"mob.*no", r"customer.*id",
        r"cust.*id", r"account",
    ])

    # Short code / service
    cols["shortcode"] = _find_col(df, [
        r"short.*code", r"shortcode", r"service.*code", r"ivr.*code",
        r"queue.*code", r"code",
    ])

    # Queue / agent group
    cols["queue"] = _find_col(df, [
        r"queue", r"skill.*group", r"agent.*group", r"team", r"group",
    ])

    # Agent
    cols["agent"] = _find_col(df, [
        r"agent.*name", r"agent.*id", r"csr", r"representative", r"staff",
    ])

    # Lev1-4 mapping
    cols["lev1"] = _find_col(df, [r"lev.*1", r"level.*1", r"category.*1", r"l1"])
    cols["lev2"] = _find_col(df, [r"lev.*2", r"level.*2", r"category.*2", r"l2"])
    cols["lev3"] = _find_col(df, [r"lev.*3", r"level.*3", r"category.*3", r"l3"])
    cols["lev4"] = _find_col(df, [r"lev.*4", r"level.*4", r"category.*4", r"l4"])

    # Other dimensions
    cols["call_type"] = _find_col(df, [r"call.*type", r"contact.*type", r"channel"])
    cols["site"]      = _find_col(df, [r"\bsite\b", r"location", r"branch"])
    cols["region"]    = _find_col(df, [r"region", r"area", r"zone", r"governorate"])

    return cols


def prepare_df(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Add numeric score column and parsed date column in-place."""
    df = df.copy()
    sc = cols.get("score")
    if sc:
        df["_score"] = pd.to_numeric(df[sc], errors="coerce")
    else:
        df["_score"] = np.nan

    dt = cols.get("date")
    if dt:
        df["_date"] = pd.to_datetime(df[dt], errors="coerce", dayfirst=True)
    else:
        df["_date"] = pd.NaT

    df["_is_det"]  = df["_score"].between(0, 6, inclusive="both")
    df["_is_pass"] = df["_score"].between(7, 8, inclusive="both")
    df["_is_prom"] = df["_score"].between(9, 10, inclusive="both")
    return df


def apply_time_filter(df: pd.DataFrame, time_q: str) -> pd.DataFrame:
    """Filter df by a plain-language time phrase."""
    if not time_q or "_date" not in df.columns:
        return df
    q = time_q.lower().strip()
    now = pd.Timestamp.now()

    if re.search(r"last\s*month", q):
        m = (now - pd.DateOffset(months=1))
        mask = (df["_date"].dt.year == m.year) & (df["_date"].dt.month == m.month)
    elif re.search(r"this\s*month", q):
        mask = (df["_date"].dt.year == now.year) & (df["_date"].dt.month == now.month)
    elif re.search(r"this\s*year", q):
        mask = df["_date"].dt.year == now.year
    elif m := re.search(r"(20\d{2})-(\d{2})", q):
        mask = (df["_date"].dt.year == int(m.group(1))) & \
               (df["_date"].dt.month == int(m.group(2)))
    elif m := re.search(r"(20\d{2})", q):
        mask = df["_date"].dt.year == int(m.group(1))
    elif m := re.search(r"q([1-4])\s*(20\d{2})?", q):
        qn = int(m.group(1))
        yr = int(m.group(2)) if m.group(2) else now.year
        months = {1: [1,2,3], 2: [4,5,6], 3: [7,8,9], 4: [10,11,12]}[qn]
        mask = (df["_date"].dt.year == yr) & (df["_date"].dt.month.isin(months))
    else:
        return df

    filtered = df[mask]
    return filtered if len(filtered) > 0 else df


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — ANALYTICS FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def _nps(df: pd.DataFrame) -> float:
    n = df["_score"].notna().sum()
    if n == 0:
        return 0.0
    prom = df["_is_prom"].sum()
    det  = df["_is_det"].sum()
    return round((prom - det) / n * 100, 1)


def build_kpi_summary(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    scored = df["_score"].notna()
    total   = len(df)
    n_score = scored.sum()
    n_det   = int(df["_is_det"].sum())
    n_prom  = int(df["_is_prom"].sum())
    det_pct = round(n_det / n_score * 100, 1) if n_score else 0
    nps     = _nps(df)
    comp    = round(n_score / total * 100, 1) if total else 0
    avg_day = ""
    if df["_date"].notna().any():
        days = (df["_date"].max() - df["_date"].min()).days + 1
        avg_day = round(total / max(days, 1), 1)
        date_start = str(df["_date"].min().date())
        date_end   = str(df["_date"].max().date())
        days_cov   = days
    else:
        date_start = date_end = "N/A"
        days_cov   = "N/A"

    rows = [
        ("Total Surveys Sent",    total,    ""),
        ("Scored Responses",      n_score,  ""),
        ("Detractors (Q1 0-6)",   n_det,    ""),
        ("Promoters (Q1 9-10)",   n_prom,   ""),
        ("Detractor Rate %",      det_pct,  "lower is better"),
        ("NPS Score",             nps,      "Promoters% − Detractors%"),
        ("Completion Rate %",     comp,     "scored / total"),
        ("Average Surveys / Day", avg_day,  ""),
        ("Date Range Start",      date_start, ""),
        ("Date Range End",        date_end,   ""),
        ("Number of Days Covered",days_cov,   ""),
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value", "Note"])


def build_daily_trend(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    if df["_date"].isna().all():
        return pd.DataFrame()
    g = df.groupby(df["_date"].dt.date)
    out = g.agg(
        Surveys_Sent=("_score", "count"),
        Detractors=("_is_det", "sum"),
    ).reset_index()
    out.columns = ["Date", "Surveys_Sent", "Detractors"]
    out["Detractor_Rate_%"] = (out["Detractors"] / out["Surveys_Sent"] * 100).round(1)
    return out.sort_values("Date")


def build_monthly_trend(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    if df["_date"].isna().all():
        return pd.DataFrame()
    df2 = df.copy()
    df2["_month"] = df2["_date"].dt.to_period("M").astype(str)
    g = df2.groupby("_month")
    out = g.agg(
        Surveys_Sent=("_score", "count"),
        Detractors=("_is_det", "sum"),
        NPS=("_score", lambda x: _nps(df2.loc[x.index])),
    ).reset_index()
    out.columns = ["Month", "Surveys_Sent", "Detractors", "NPS"]
    out["Detractor_Rate_%"] = (out["Detractors"] / out["Surveys_Sent"] * 100).round(1)
    return out


def _breakdown(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if not col or col not in df.columns:
        return pd.DataFrame()
    g = df.groupby(col)
    out = g.agg(
        Total=("_score", "count"),
        Detractors=("_is_det", "sum"),
        Avg_Score=("_score", "mean"),
    ).reset_index()
    out["Detractor_Rate_%"] = (out["Detractors"] / out["Total"] * 100).round(1)
    out["Avg_Score"] = out["Avg_Score"].round(2)
    return out.sort_values("Detractors", ascending=False)


def build_top_bottom(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    sc = cols.get("shortcode")
    if not sc:
        return pd.DataFrame()
    g = df.groupby(sc).agg(
        Total=("_score", "count"),
        NPS_Score=("_score", lambda x: _nps(df.loc[x.index])),
    ).reset_index()
    g = g[g["Total"] >= 5].sort_values("NPS_Score")
    top = g.tail(10).copy(); top["Rank"] = "Top 10"
    bot = g.head(10).copy(); bot["Rank"] = "Bottom 10"
    out = pd.concat([bot, top], ignore_index=True)
    out.columns = [sc, "Total", "NPS_Score", "Rank"]
    return out


def build_repeat_detractors(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    msisdn = cols.get("msisdn")
    if not msisdn:
        return pd.DataFrame()
    det = df[df["_is_det"]]
    cnt = det.groupby(msisdn).size().reset_index(name="Times_as_Detractor")
    return cnt[cnt["Times_as_Detractor"] >= 2].sort_values(
        "Times_as_Detractor", ascending=False
    )


def build_hour_pattern(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    if df["_date"].isna().all():
        return pd.DataFrame()
    df2 = df.copy()
    df2["_hour"] = df2["_date"].dt.hour
    g = df2.groupby("_hour").agg(
        Surveys=("_score", "count"),
        Avg_Score=("_score", "mean"),
        Detractors=("_is_det", "sum"),
    ).reset_index()
    g.columns = ["Hour", "Surveys", "Avg_Score", "Detractors"]
    g["Avg_Score"] = g["Avg_Score"].round(2)
    return g


def build_dow_pattern(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    if df["_date"].isna().all():
        return pd.DataFrame()
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    df2 = df.copy()
    df2["_dow"] = df2["_date"].dt.dayofweek
    g = df2.groupby("_dow").agg(
        Surveys=("_score", "count"),
        Avg_Score=("_score", "mean"),
        Detractors=("_is_det", "sum"),
    ).reset_index()
    g["Day"] = g["_dow"].map(lambda x: days[x])
    g["Avg_Score"] = g["Avg_Score"].round(2)
    return g[["Day","Surveys","Avg_Score","Detractors"]]


def build_score_dist(df: pd.DataFrame) -> pd.DataFrame:
    if df["_score"].isna().all():
        return pd.DataFrame()
    cnt = df["_score"].value_counts().sort_index().reset_index()
    cnt.columns = ["Score", "Count"]
    cnt["Pct_%"] = (cnt["Count"] / cnt["Count"].sum() * 100).round(1)
    return cnt


def build_forecast(daily: pd.DataFrame, periods: int = 30) -> pd.DataFrame:
    """Simple linear-extrapolation forecast (no scipy needed for basic version)."""
    if daily is None or daily.empty or "Surveys_Sent" not in daily.columns:
        return pd.DataFrame()
    try:
        from scipy.optimize import curve_fit  # noqa
        _scipy = True
    except ImportError:
        _scipy = False

    y = daily["Surveys_Sent"].values.astype(float)
    n = len(y)
    if n < 3:
        return pd.DataFrame()

    # Use Holt's double exponential smoothing if scipy available, else linear
    if _scipy and n >= 7:
        alpha, beta = 0.3, 0.1
        level, trend = y[0], y[1] - y[0]
        smoothed = []
        for val in y:
            prev_level = level
            level = alpha * val + (1 - alpha) * (level + trend)
            trend = beta * (level - prev_level) + (1 - beta) * trend
            smoothed.append(level)
        forecasts = [level + (i + 1) * trend for i in range(periods)]
    else:
        # Linear regression fallback
        x = np.arange(n)
        m, b = np.polyfit(x, y, 1)
        forecasts = [max(0, m * (n + i) + b) for i in range(periods)]

    last_date = pd.to_datetime(daily["Date"].iloc[-1]) if "Date" in daily.columns else pd.Timestamp.now()
    future_dates = pd.date_range(last_date + pd.Timedelta(days=1), periods=periods)
    out = pd.DataFrame({
        "Date": future_dates.strftime("%Y-%m-%d"),
        "Forecast_Surveys": [round(max(0, f), 1) for f in forecasts],
        "Type": "Forecast",
    })
    return out


def build_toxic_combos(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    dims = [c for k, c in cols.items()
            if k in ("shortcode","queue","lev1","lev2","call_type","site") and c]
    if len(dims) < 2:
        return pd.DataFrame()
    d1, d2 = dims[0], dims[1]
    g = df.groupby([d1, d2]).agg(
        Total=("_score", "count"),
        Detractors=("_is_det", "sum"),
    ).reset_index()
    g["Detractor_Rate_%"] = (g["Detractors"] / g["Total"] * 100).round(1)
    g = g[g["Total"] >= 5]
    return g.sort_values("Detractor_Rate_%", ascending=False).head(30)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — EXCEL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _title_row(ws, text: str, span: int) -> int:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, span))
    c = ws.cell(row=1, column=1, value=text)
    c.font      = Font(name="Arial", size=14, bold=True, color=WHITE)
    c.fill      = PatternFill("solid", fgColor=BRAND_RED)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 26
    sub = ws.cell(row=2, column=1,
                  value=f"Standalone TNPS Dashboard  |  {datetime.now():%Y-%m-%d %H:%M}")
    sub.font = Font(name="Arial", size=8, italic=True, color="888888")
    return 4


def _header_row(ws, row: int, headers: list) -> None:
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=j, value=str(h))
        c.font      = Font(name="Arial", size=10, bold=True, color=WHITE)
        c.fill      = PatternFill("solid", fgColor=DARK)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border    = _BORDER


def _data_rows(ws, start: int, rows: list) -> int:
    LIGHT = "FCE9E7"
    for i, r in enumerate(rows):
        for j, v in enumerate(r, 1):
            if hasattr(v, "item"):
                v = v.item()
            c = ws.cell(row=start + i, column=j, value=v)
            c.font      = Font(name="Arial", size=10, color=DARK)
            c.fill      = PatternFill("solid", fgColor=(LIGHT if i % 2 else WHITE))
            c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
            c.border    = _BORDER
    return start + len(rows)


def _autofit(ws, headers: list, rows: list) -> None:
    for j, h in enumerate(headers, 1):
        w = len(str(h))
        for r in rows:
            if j - 1 < len(r):
                w = max(w, len(str(r[j - 1])))
        ws.column_dimensions[get_column_letter(j)].width = min(max(w + 4, 12), 50)


def _write_sheet(wb: Workbook, name: str, title: str, df: pd.DataFrame,
                 chart: bool = False) -> None:
    if df is None or df.empty:
        return
    headers = [str(c) for c in df.columns]
    rows    = []
    for _, row in df.iterrows():
        r = []
        for v in row:
            if hasattr(v, "item"):
                v = v.item()
            elif pd.isna(v) if not isinstance(v, str) else False:
                v = ""
            r.append(v)
        rows.append(r)
    ws    = wb.create_sheet(title=name[:31])
    start = _title_row(ws, title, len(headers))
    _header_row(ws, start, headers)
    end   = _data_rows(ws, start + 1, rows)
    _autofit(ws, headers, rows)
    ws.freeze_panes             = ws.cell(row=start + 1, column=1)
    ws.sheet_view.showGridLines = False

    if chart and len(headers) == 2 and rows:
        ch       = BarChart()
        ch.type  = "col"; ch.title = title; ch.height = 7; ch.width = 14
        data = Reference(ws, min_col=2, min_row=start, max_row=end - 1)
        cats = Reference(ws, min_col=1, min_row=start + 1, max_row=end - 1)
        ch.add_data(data, titles_from_data=True)
        ch.set_categories(cats)
        ch.legend = None
        if ch.series:
            ch.series[0].graphicalProperties.solidFill = BRAND_RED
        ws.add_chart(ch, f"{get_column_letter(len(headers)+2)}{start}")


def _write_toc(wb: Workbook, sheet_names: list) -> None:
    ws = wb.create_sheet("Table_of_Contents", 1)
    ws.sheet_view.showGridLines = False
    ws.merge_cells("B2:E2")
    t = ws["B2"]; t.value = "Table of Contents"
    t.font = Font(name="Arial", size=16, bold=True, color=WHITE)
    t.fill = PatternFill("solid", fgColor=BRAND_RED)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 28
    ws.column_dimensions["C"].width = 35
    ws.column_dimensions["D"].width = 20
    ws.cell(row=3, column=3, value="Sheet").font  = Font(bold=True, name="Arial")
    ws.cell(row=3, column=4, value="Link").font   = Font(bold=True, name="Arial")
    for idx, name in enumerate(sheet_names, 4):
        ws.cell(row=idx, column=3, value=name).font = Font(name="Arial", size=10)
        lc = ws.cell(row=idx, column=4, value=f"Go → {name}")
        lc.hyperlink = f"#'{name}'!A1"
        lc.font = Font(name="Arial", size=10, color="0563C1", underline="single")


def _write_dashboard_sheet(wb: Workbook, kpi: pd.DataFrame,
                            daily: pd.DataFrame) -> None:
    ws = wb.active; ws.title = "Dashboard"
    ws.sheet_view.showGridLines = False

    def kv(metric):
        if kpi is None or kpi.empty:
            return "N/A"
        r = kpi[kpi["Metric"] == metric]
        return r["Value"].iloc[0] if not r.empty else "N/A"

    ws.merge_cells("B2:K3")
    t = ws["B2"]; t.value = "TNPS Detractor Analytics Dashboard"
    t.font = Font(name="Arial", size=20, bold=True, color=WHITE)
    t.fill = PatternFill("solid", fgColor=DARK)
    t.alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells("B4:K4")
    sub = ws["B4"]
    sub.value = (f"Period: {kv('Date Range Start')}  →  {kv('Date Range End')}"
                 f"   |   {kv('Number of Days Covered')} days")
    sub.font = Font(name="Arial", size=11, italic=True, color="666666")
    sub.alignment = Alignment(horizontal="center", vertical="center")

    cards = [
        ("Total Surveys",    "Total Surveys Sent",    DARK),
        ("Detractors",       "Detractors (Q1 0-6)",   BRAND_RED),
        ("Detractor Rate %", "Detractor Rate %",      BRAND_RED),
        ("NPS Score",        "NPS Score",             "2E8B57"),
        ("Completion %",     "Completion Rate %",     BRAND_RED),
        ("Avg / Day",        "Average Surveys / Day", DARK),
    ]
    for i, (label, metric, color) in enumerate(cards):
        col = 2 + (i % 3) * 3
        r   = 6 + (i // 3) * 4
        ws.merge_cells(start_row=r, start_column=col, end_row=r, end_column=col+2)
        c = ws.cell(row=r, column=col); c.value = label
        c.font = Font(name="Arial", size=10, bold=True, color=WHITE)
        c.fill = PatternFill("solid", fgColor=color)
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.merge_cells(start_row=r+1, start_column=col, end_row=r+2, end_column=col+2)
        c = ws.cell(row=r+1, column=col); c.value = kv(metric)
        c.font = Font(name="Arial", size=22, bold=True, color=DARK)
        c.fill = PatternFill("solid", fgColor="F5F5F5")
        c.alignment = Alignment(horizontal="center", vertical="center")

    for cl in ["B","C","D","E","F","G","H","I","J"]:
        ws.column_dimensions[cl].width = 14
    for r in range(2, 24):
        ws.row_dimensions[r].height = 22

    # Chart data (hidden columns M-P)
    if daily is not None and not daily.empty and "Date" in daily.columns:
        cc = 13
        ws.cell(row=2, column=cc,   value="Date")
        ws.cell(row=2, column=cc+1, value="Surveys_Sent")
        ws.cell(row=2, column=cc+2, value="Detractors")
        ws.cell(row=2, column=cc+3, value="Detractor_Rate_%")
        for i, (_, rd) in enumerate(daily.iterrows(), 3):
            ws.cell(row=i, column=cc,   value=str(rd["Date"]))
            ws.cell(row=i, column=cc+1, value=float(rd.get("Surveys_Sent", 0)))
            ws.cell(row=i, column=cc+2, value=float(rd.get("Detractors", 0)))
            ws.cell(row=i, column=cc+3, value=float(rd.get("Detractor_Rate_%", 0)))
        er = 2 + len(daily)
        for c in range(cc, cc+4):
            ws.column_dimensions[get_column_letter(c)].width = 2

        ch = LineChart(); ch.title = "Daily Volume & Detractors"
        ch.style = 2; ch.height = 10; ch.width = 24
        ch.add_data(Reference(ws, min_col=cc+1, max_col=cc+2, min_row=2, max_row=er),
                    titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=cc, min_row=3, max_row=er))
        ws.add_chart(ch, "B25")

        ch2 = LineChart(); ch2.title = "Daily Detractor Rate %"
        ch2.style = 12; ch2.height = 10; ch2.width = 24
        ch2.add_data(Reference(ws, min_col=cc+3, max_col=cc+3, min_row=2, max_row=er),
                     titles_from_data=True)
        ch2.set_categories(Reference(ws, min_col=cc, min_row=3, max_row=er))
        if ch2.series:
            ch2.series[0].graphicalProperties.line.solidFill = BRAND_RED
        ws.add_chart(ch2, "B47")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — ASSEMBLE THE WORKBOOK
# ══════════════════════════════════════════════════════════════════════════════

def build_dashboard(df: pd.DataFrame, cols: dict, out_path: Path) -> Path:
    def _safe(fn, *a, **kw):
        try:
            r = fn(*a, **kw)
            return r if r is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    kpi     = _safe(build_kpi_summary, df, cols)
    daily   = _safe(build_daily_trend, df, cols)
    monthly = _safe(build_monthly_trend, df, cols)
    top_bot = _safe(build_top_bottom, df, cols)
    repeats = _safe(build_repeat_detractors, df, cols)
    hour_p  = _safe(build_hour_pattern, df, cols)
    dow_p   = _safe(build_dow_pattern, df, cols)
    score_d = _safe(build_score_dist, df)
    forecast= _safe(build_forecast, daily, 30)
    toxic   = _safe(build_toxic_combos, df, cols)

    wb = Workbook()
    _write_dashboard_sheet(wb, kpi, daily)
    created = ["Dashboard"]

    static = [
        ("KPI_Summary",      kpi,     "KPI Summary"),
        ("Score_Distribution",score_d,"Score Distribution (0-10)"),
        ("Daily_Trend",      daily,   "Daily Trend"),
        ("Monthly_Trend",    monthly, "Monthly Trend"),
        ("Top10_Bottom10",   top_bot, "Top 10 / Bottom 10 Short Codes"),
        ("Hour_Pattern",     hour_p,  "Hour of Day Pattern"),
        ("DayOfWeek_Pattern",dow_p,   "Day of Week Pattern"),
        ("Repeat_Detractors",repeats, "Repeat Detractors (2+ times)"),
        ("Toxic_Combos",     toxic,   "Toxic Dimension Combos"),
        ("Forecast_30d",     forecast,"30-Day Forecast"),
    ]

    dim_sheets = [
        ("By_ShortCode",  cols.get("shortcode"),  "Detractors by Short Code"),
        ("By_Queue",      cols.get("queue"),       "Detractors by Queue"),
        ("By_Agent",      cols.get("agent"),       "Detractors by Agent"),
        ("By_Lev1",       cols.get("lev1"),        "By Mapping Lev 1"),
        ("By_Lev2",       cols.get("lev2"),        "By Mapping Lev 2"),
        ("By_Lev3",       cols.get("lev3"),        "By Mapping Lev 3"),
        ("By_Lev4",       cols.get("lev4"),        "By Mapping Lev 4"),
        ("By_CallType",   cols.get("call_type"),   "Detractors by Call Type"),
        ("By_Site",       cols.get("site"),        "Detractors by Site"),
        ("By_Region",     cols.get("region"),      "Detractors by Region"),
    ]

    for sname, sdf, stitle in static:
        if sdf is None or (hasattr(sdf, "empty") and sdf.empty):
            continue
        _write_sheet(wb, sname, stitle, sdf)
        created.append(sname[:31])

    for sname, col, stitle in dim_sheets:
        if not col:
            continue
        bdf = _safe(_breakdown, df, col)
        if bdf.empty:
            continue
        _write_sheet(wb, sname, stitle, bdf, chart=False)
        created.append(sname[:31])

    # Raw data sample (first 2000 rows)
    sample = df.drop(columns=["_score","_date","_is_det","_is_pass","_is_prom"],
                     errors="ignore").head(2000)
    _write_sheet(wb, "Raw_Data_Sample", "Raw Data Sample (first 2000 rows)", sample)
    created.append("Raw_Data_Sample")

    _write_toc(wb, created)
    wb.save(out_path)
    return out_path


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Standalone TNPS Excel Dashboard — no Roma needed",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tnps_dashboard_standalone.py survey.csv
  python tnps_dashboard_standalone.py survey.xlsx --time "2025-01"
  python tnps_dashboard_standalone.py survey.csv --time "Q1 2025" --out q1_report.xlsx
        """,
    )
    parser.add_argument("file", help="Input CSV or Excel file")
    parser.add_argument("--time", default="", help='Filter: "2025-01", "Q1 2025", "last month"')
    parser.add_argument("--out",  default="", help="Output .xlsx path (default: auto-named)")
    args = parser.parse_args()

    input_path = Path(args.file)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {input_path} ...")
    df_raw = load_data(input_path)
    print(f"  {len(df_raw):,} rows × {len(df_raw.columns)} columns")

    cols = detect_columns(df_raw)
    print("  Detected columns:")
    for role, col in cols.items():
        if col:
            print(f"    {role:<14} → {col}")

    df = prepare_df(df_raw, cols)

    if args.time:
        before = len(df)
        df = apply_time_filter(df, args.time)
        print(f"  Time filter '{args.time}': {len(df):,} / {before:,} rows")

    if df["_score"].isna().all():
        print("WARNING: Could not detect a numeric NPS/score column.")
        print("  Columns in your file:", list(df_raw.columns))
        print("  Hint: rename your score column to contain 'tnps', 'nps', 'score', or 'rating'")

    # Output path
    if args.out:
        out = Path(args.out)
    else:
        tag   = "_" + re.sub(r"\W+", "", args.time.lower()) if args.time else ""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out   = Path(f"tnps_dashboard{tag}_{stamp}.xlsx")

    print(f"Building dashboard → {out}")
    build_dashboard(df, cols, out)
    print(f"Done. Saved to: {out.resolve()}")


if __name__ == "__main__":
    main()
