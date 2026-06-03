"""TNPS analytics adapted for Roma's SQLite data layer.

All functions accept a cols dict from detect_tnps_columns() so they use the
actual column names found in the DataFrame rather than hardcoded names.
Every function returns an empty DataFrame (never raises) when required data
is missing.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

# ============================================================
# CONFIG (mirrors tnps_analyzer.py defaults)
# ============================================================
_CONFIG = {
    "detractor_max": 6,
    "passive_max": 8,
    "min_agent_sample": 5,
    "anomaly_std_threshold": 2,
    "velocity_alert_pct": 20,
    "sla_detractor_rate_threshold": 50,
    "detractor_rate_target": 40,
}


# ============================================================
# COLUMN DETECTION
# ============================================================
_COL_PATTERNS: dict[str, str] = {
    "nps":          r"q1.answer.*tnps|tnps|nps_score|nps_answer|recommend|q1_answer",
    "ivr_dt":       r"outbound.ivr.dt|ivr.dt|survey.date|call.date|contact.date|date.?time",
    "short_code":   r"short.?code",
    "agent_queue":  r"agent.?queue",
    "msisdn":       r"msisdn|phone|mobile|caller",
    "owner_team":   r"owner.?team",
    "substatus":    r"substatus|sub.?status",
    "call_type":    r"call.?type",
    "fcr_flag":     r"fcr.?flag|fcr",
    "channel":      r"channel.?name|channel",
    "prod_type":    r"prod.?type|product.?type",
    "reachability": r"reachability|reach",
    "agent_id":     r"agent.?id|agent_id_",
    "lev1":         r"q.?mapping.?lev.?1|lev.?1(?!.*2)",
    "lev2":         r"q.?mapping.?lev.?2|lev.?2.?combined|lev.?2",
    "lev3":         r"q.?mapping.?lev.?3|lev.?3.?new|lev.?3",
    "lev4":         r"q.?mapping.?lev.?4|lev.?4.?seg|lev.?4",
    "site":         r"^site$",
}


def detect_tnps_columns(df: pd.DataFrame) -> dict:
    """Return dict mapping canonical names to actual column names found.

    Keys: nps, ivr_dt, short_code, agent_queue, msisdn, owner_team,
          substatus, call_type, fcr_flag, channel, prod_type, reachability,
          agent_id, lev1, lev2, lev3, lev4, site
    Values: actual column name in df, or None if not found.
    """
    cols = list(df.columns)
    result: dict[str, str | None] = {}
    for key, pattern in _COL_PATTERNS.items():
        found = None
        for col in cols:
            if re.search(pattern, col, re.IGNORECASE):
                found = col
                break
        result[key] = found
    return result


# ============================================================
# LOAD FROM SQLITE
# ============================================================
def load_tnps_df(conn) -> tuple[pd.DataFrame, dict]:
    """Load the TNPS survey table from SQLite and detect columns.

    Returns (df, cols_dict) where cols_dict maps canonical -> actual names.
    Adds enriched columns: Month, Week, Hour, DayOfWeek, NPS_Category, Date.
    """
    try:
        import sqlite3
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        all_tables = [r[0] if isinstance(r, (tuple, list)) else r["name"]
                      for r in cursor.fetchall()]
        # filter internal tables
        internal = {"documents", "_roma_sources", "sqlite_sequence"}
        user_tables = [t for t in all_tables if t not in internal]

        # Find which table has an NPS-like column
        tnps_table = None
        for tname in user_tables:
            cols_info = conn.execute(f'PRAGMA table_info("{tname}")').fetchall()
            col_names = [r[1] if isinstance(r, (tuple, list)) else r["name"]
                         for r in cols_info]
            if any(re.search(r"tnps|nps|recommend|promoter|q1.answer", c, re.IGNORECASE)
                   for c in col_names):
                tnps_table = tname
                break

        if not tnps_table:
            return pd.DataFrame(), {}

        df = pd.read_sql_query(f'SELECT * FROM "{tnps_table}"', conn)

        # Check for a mapping table (has queue + lev columns)
        mapping_table = None
        for tname in user_tables:
            if tname == tnps_table:
                continue
            cols_info = conn.execute(f'PRAGMA table_info("{tname}")').fetchall()
            col_names = [r[1] if isinstance(r, (tuple, list)) else r["name"]
                         for r in cols_info]
            has_queue = any(re.search(r"queue", c, re.IGNORECASE) for c in col_names)
            has_lev = any(re.search(r"lev", c, re.IGNORECASE) for c in col_names)
            if has_queue and has_lev:
                mapping_table = tname
                break

        if mapping_table:
            map_df = pd.read_sql_query(f'SELECT * FROM "{mapping_table}"', conn)
            # Try to join on AGENT_QUEUE
            df_queue_col = next(
                (c for c in df.columns if re.search(r"agent.?queue", c, re.IGNORECASE)),
                None
            )
            map_queue_col = next(
                (c for c in map_df.columns if re.search(r"agent.?queue|queue", c, re.IGNORECASE)),
                None
            )
            if df_queue_col and map_queue_col:
                map_df = map_df.rename(columns={map_queue_col: df_queue_col})
                # only bring in columns not already in df
                new_cols = [c for c in map_df.columns
                            if c not in df.columns or c == df_queue_col]
                df = df.merge(
                    map_df[[c for c in new_cols]],
                    on=df_queue_col, how="left"
                )

    except Exception:
        return pd.DataFrame(), {}

    cols = detect_tnps_columns(df)

    # Enrich with date/time columns
    ivr_col = cols.get("ivr_dt")
    nps_col = cols.get("nps")

    if ivr_col and ivr_col in df.columns:
        df[ivr_col] = pd.to_datetime(df[ivr_col], errors="coerce")
        df = df.dropna(subset=[ivr_col])
        df["Date"] = df[ivr_col].dt.date
        df["Month"] = df[ivr_col].dt.to_period("M").astype(str)
        df["Week"] = df[ivr_col].dt.to_period("W").astype(str)
        df["Hour"] = df[ivr_col].dt.hour
        df["DayOfWeek"] = df[ivr_col].dt.day_name()
    else:
        for col in ("Date", "Month", "Week", "Hour", "DayOfWeek"):
            if col not in df.columns:
                df[col] = ""

    if nps_col and nps_col in df.columns:
        df[nps_col] = pd.to_numeric(df[nps_col], errors="coerce")
        df["NPS_Category"] = pd.cut(
            df[nps_col],
            bins=[-1, _CONFIG["detractor_max"], _CONFIG["passive_max"], 10],
            labels=["Detractor", "Passive", "Promoter"],
        ).astype(str)
        df.loc[df[nps_col].isna(), "NPS_Category"] = "N/A"
    else:
        df["NPS_Category"] = "N/A"

    # fill mapping lev columns
    for lev_key in ("lev1", "lev2", "lev3", "lev4", "site"):
        lev_col = cols.get(lev_key)
        if lev_col and lev_col in df.columns:
            df[lev_col] = df[lev_col].fillna("Unmapped")

    # Re-detect after enrichment
    cols = detect_tnps_columns(df)
    return df, cols


# ============================================================
# INTERNAL HELPERS
# ============================================================
def _completed(df: pd.DataFrame, nps_col: str | None) -> pd.DataFrame:
    if not nps_col or nps_col not in df.columns:
        return pd.DataFrame()
    return df[df[nps_col].notna()]


def _safe_return(fn):
    """Decorator: catch any exception and return empty DataFrame."""
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return pd.DataFrame()
    return wrapper


# ============================================================
# KPI SUMMARY
# ============================================================
@_safe_return
def build_kpi_summary(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """KPI summary with delta vs previous month."""
    if df.empty:
        return pd.DataFrame({"Metric": [], "Value": [], "vs_Prev_Month": []})

    nps_col = cols.get("nps")
    sc_col = cols.get("short_code")
    aq_col = cols.get("agent_queue")
    ag_col = cols.get("agent_id")
    lev1_col = cols.get("lev1")

    completed = _completed(df, nps_col)
    if completed.empty and nps_col:
        completed = pd.DataFrame()

    det = completed[completed["NPS_Category"] == "Detractor"] if not completed.empty else pd.DataFrame()
    pas = completed[completed["NPS_Category"] == "Passive"] if not completed.empty else pd.DataFrame()
    pro = completed[completed["NPS_Category"] == "Promoter"] if not completed.empty else pd.DataFrame()

    date_col = "Date" if "Date" in df.columns else None
    n_days = max(df["Date"].nunique(), 1) if date_col and "Date" in df.columns else 1
    n_comp = len(completed)
    nps_score = (len(pro) - len(det)) / n_comp * 100 if n_comp else 0
    avg_tnps = completed[nps_col].mean() if (n_comp and nps_col and nps_col in completed.columns) else 0

    # Delta: compare last month vs previous month
    delta_det_rate = delta_nps = "N/A"
    if "Month" in df.columns and n_comp > 0:
        months = sorted(df["Month"].dropna().unique())
        if len(months) >= 2:
            cur_m  = df[df["Month"] == months[-1]]
            prev_m = df[df["Month"] == months[-2]]

            def _det_rate(d):
                c = d[d[nps_col].notna()] if nps_col and nps_col in d.columns else pd.DataFrame()
                if len(c) == 0:
                    return 0
                return len(c[c["NPS_Category"] == "Detractor"]) / len(c) * 100

            def _nps(d):
                c = d[d[nps_col].notna()] if nps_col and nps_col in d.columns else pd.DataFrame()
                if len(c) == 0:
                    return 0
                p_ = len(c[c["NPS_Category"] == "Promoter"])
                d_ = len(c[c["NPS_Category"] == "Detractor"])
                return (p_ - d_) / len(c) * 100

            delta_det_rate = round(_det_rate(cur_m) - _det_rate(prev_m), 2)
            delta_nps = round(_nps(cur_m) - _nps(prev_m), 2)

    unique_sc = df[sc_col].nunique() if sc_col and sc_col in df.columns else 0
    unique_aq = df[aq_col].nunique() if aq_col and aq_col in df.columns else 0
    unique_ag = df[ag_col].nunique() if ag_col and ag_col in df.columns else 0
    unmapped = int((df[lev1_col] == "Unmapped").sum()) if lev1_col and lev1_col in df.columns else 0

    date_min = str(df["Date"].min()) if "Date" in df.columns and not df["Date"].empty else "N/A"
    date_max = str(df["Date"].max()) if "Date" in df.columns and not df["Date"].empty else "N/A"

    return pd.DataFrame({
        "Metric": [
            "Date Range Start", "Date Range End", "Number of Days Covered",
            "Total Surveys Sent", "Completed Surveys", "Completion Rate %",
            "Detractors (Q1 0-6)", "Passives (Q1 7-8)", "Promoters (Q1 9-10)",
            "Detractor Rate %", "NPS Score", "Average TNPS Score",
            "Average Surveys / Day", "Average Detractors / Day",
            "Unique Short Codes", "Unique Agent Queues", "Unique Agents",
            "Unmapped Queue Surveys",
        ],
        "Value": [
            date_min, date_max, n_days,
            len(df), n_comp,
            round(n_comp / len(df) * 100, 2) if len(df) else 0,
            len(det), len(pas), len(pro),
            round(len(det) / n_comp * 100, 2) if n_comp else 0,
            round(nps_score, 2), round(avg_tnps, 2),
            round(len(df) / n_days, 1), round(len(det) / n_days, 1),
            unique_sc, unique_aq, unique_ag, unmapped,
        ],
        "vs_Prev_Month": [
            "", "", "", "", "", "", "", "", "",
            delta_det_rate, delta_nps, "",
            "", "", "", "", "", "",
        ],
    })


# ============================================================
# DAILY TREND
# ============================================================
@_safe_return
def build_daily_trend(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Daily trend with 7-day rolling avg + anomaly flags."""
    nps_col = cols.get("nps")
    if not nps_col or nps_col not in df.columns or "Date" not in df.columns:
        return pd.DataFrame()

    df2 = df.copy()
    df2[nps_col] = pd.to_numeric(df2[nps_col], errors="coerce")
    df2["Date"] = pd.to_datetime(df2["Date"], errors="coerce")
    df2 = df2.dropna(subset=["Date"])

    g = df2.groupby("Date")
    completed = df2[df2[nps_col].notna()].groupby("Date")

    out = pd.DataFrame({"Date": sorted(df2["Date"].unique())}).set_index("Date")
    out["Surveys_Sent"] = g.size()
    out["Surveys_Completed"] = completed.size()
    out["Completion_Rate_%"] = (out["Surveys_Completed"] / out["Surveys_Sent"].replace(0, np.nan) * 100).round(2)
    out["Detractors"] = df2[df2["NPS_Category"] == "Detractor"].groupby("Date").size()
    out["Passives"] = df2[df2["NPS_Category"] == "Passive"].groupby("Date").size()
    out["Promoters"] = df2[df2["NPS_Category"] == "Promoter"].groupby("Date").size()
    out = out.fillna(0)

    out["Detractor_Rate_%"] = (
        out["Detractors"] / out["Surveys_Completed"].replace(0, np.nan) * 100
    ).round(2)
    out["NPS_Score"] = (
        (out["Promoters"] - out["Detractors"])
        / out["Surveys_Completed"].replace(0, np.nan) * 100
    ).round(2)
    out["Avg_TNPS"] = completed[nps_col].mean().round(2)
    out["Surveys_7d_Avg"] = out["Surveys_Sent"].rolling(7, min_periods=1).mean().round(1)
    out["Detractors_7d_Avg"] = out["Detractors"].rolling(7, min_periods=1).mean().round(1)
    out["Detractor_Rate_7d_Avg"] = out["Detractor_Rate_%"].rolling(7, min_periods=1).mean().round(2)

    thr = _CONFIG["anomaly_std_threshold"]
    mean_v = out["Surveys_Sent"].mean()
    std_v = out["Surveys_Sent"].std()
    out["Volume_vs_Avg"] = out["Surveys_Sent"].apply(
        lambda x: "LOW" if (std_v and x < mean_v - thr * std_v)
        else "HIGH" if (std_v and x > mean_v + thr * std_v)
        else "Normal"
    )
    mean_d = out["Detractor_Rate_%"].mean()
    std_d = out["Detractor_Rate_%"].std()
    out["Detractor_Rate_Status"] = out["Detractor_Rate_%"].apply(
        lambda x: "WORSE" if (pd.notna(x) and std_d and x > mean_d + thr * std_d)
        else "BETTER" if (pd.notna(x) and std_d and x < mean_d - thr * std_d)
        else "Normal"
    )

    return out.reset_index().fillna(0)


# ============================================================
# MONTHLY TREND
# ============================================================
@_safe_return
def build_monthly_trend(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Monthly trend with MoM changes."""
    nps_col = cols.get("nps")
    if not nps_col or nps_col not in df.columns or "Month" not in df.columns:
        return pd.DataFrame()

    df2 = df.copy()
    df2[nps_col] = pd.to_numeric(df2[nps_col], errors="coerce")

    g = df2.groupby("Month")
    completed = df2[df2[nps_col].notna()].groupby("Month")

    out = pd.DataFrame({"Month": sorted(df2["Month"].dropna().unique())}).set_index("Month")
    out["Surveys_Sent"] = g.size()
    out["Surveys_Completed"] = completed.size()
    out["Completion_Rate_%"] = (out["Surveys_Completed"] / out["Surveys_Sent"].replace(0, np.nan) * 100).round(2)
    out["Detractors"] = df2[df2["NPS_Category"] == "Detractor"].groupby("Month").size()
    out["Passives"] = df2[df2["NPS_Category"] == "Passive"].groupby("Month").size()
    out["Promoters"] = df2[df2["NPS_Category"] == "Promoter"].groupby("Month").size()
    out = out.fillna(0)
    out["Detractor_Rate_%"] = (
        out["Detractors"] / out["Surveys_Completed"].replace(0, np.nan) * 100
    ).round(2)
    out["NPS_Score"] = (
        (out["Promoters"] - out["Detractors"])
        / out["Surveys_Completed"].replace(0, np.nan) * 100
    ).round(2)
    out["Avg_TNPS"] = completed[nps_col].mean().round(2)
    out["MoM_Surveys_%"] = (out["Surveys_Sent"].pct_change() * 100).round(2)
    out["MoM_Detractors_%"] = (out["Detractors"].pct_change() * 100).round(2)

    return out.reset_index().fillna(0)


# ============================================================
# SHORT CODE × DAY PIVOT
# ============================================================
@_safe_return
def build_shortcode_daily_pivot(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Short code × date pivot with trend labels."""
    sc_col = cols.get("short_code")
    if not sc_col or sc_col not in df.columns or "Date" not in df.columns:
        return pd.DataFrame()

    det = df[df["NPS_Category"] == "Detractor"]
    if det.empty:
        return pd.DataFrame()

    pivot = det.groupby([sc_col, "Date"]).size().unstack(fill_value=0)
    pivot = pivot.reindex(sorted(pivot.columns), axis=1)

    date_cols = list(pivot.columns)
    pivot["Total_Detractors"] = pivot[date_cols].sum(axis=1)

    if len(date_cols) >= 2:
        mid = len(date_cols) // 2
        first = pivot[date_cols[:mid]].sum(axis=1)
        second = pivot[date_cols[mid:]].sum(axis=1)
    else:
        first = pd.Series(0, index=pivot.index)
        second = pivot[date_cols[0]] if date_cols else pd.Series(0, index=pivot.index)

    pivot["First_Half_Sum"] = first
    pivot["Second_Half_Sum"] = second
    pivot["Change_Abs"] = second - first
    pivot["Change_%"] = ((second - first) / first.replace(0, np.nan) * 100).round(1)

    def label(row):
        f, s, p = row["First_Half_Sum"], row["Second_Half_Sum"], row["Change_%"]
        if f == 0 and s > 0: return "NEW ↑"
        if f > 0 and s == 0: return "STOPPED ↓"
        if pd.isna(p): return "→ flat"
        if p > 10: return f"↑ +{p:.0f}%"
        if p < -10: return f"↓ {p:.0f}%"
        return "→ flat"

    pivot["Trend"] = pivot.apply(label, axis=1)
    return pivot.reset_index().sort_values("Total_Detractors", ascending=False)


# ============================================================
# CATALOGS
# ============================================================
@_safe_return
def build_shortcode_catalog(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Short code catalog."""
    sc_col = cols.get("short_code")
    nps_col = cols.get("nps")
    if not sc_col or sc_col not in df.columns:
        return pd.DataFrame()

    completed = _completed(df, nps_col) if nps_col else df
    by_sc = df.groupby(sc_col)
    comp_by_sc = completed.groupby(sc_col) if not completed.empty else None
    det_by_sc = df[df["NPS_Category"] == "Detractor"].groupby(sc_col)

    out = pd.DataFrame({sc_col: sorted(df[sc_col].dropna().unique())}).set_index(sc_col)
    out["Topic"] = ""
    out["Notes"] = ""
    if "Date" in df.columns:
        out["First_Seen"] = by_sc["Date"].min().astype(str)
        out["Last_Seen"] = by_sc["Date"].max().astype(str)
    out["Total_Surveys"] = by_sc.size()
    out["Completed_Surveys"] = comp_by_sc.size() if comp_by_sc else 0
    out["Total_Detractors"] = det_by_sc.size()
    out = out.fillna(0)
    out["Detractor_Rate_%"] = (
        out["Total_Detractors"] / out["Completed_Surveys"].replace(0, np.nan) * 100
    ).round(2)
    if nps_col and nps_col in df.columns and comp_by_sc:
        out["Avg_TNPS"] = comp_by_sc[nps_col].mean().round(2)

    def mode_or_blank(s):
        m = s.mode()
        return m.iloc[0] if len(m) else ""

    ot_col = cols.get("owner_team")
    ss_col = cols.get("substatus")
    ct_col = cols.get("call_type")
    aq_col = cols.get("agent_queue")

    out["Top_Owner_Team"] = by_sc[ot_col].agg(mode_or_blank) if ot_col and ot_col in df.columns else ""
    out["Top_Substatus"] = by_sc[ss_col].agg(mode_or_blank) if ss_col and ss_col in df.columns else ""
    out["Top_Call_Type"] = by_sc[ct_col].agg(mode_or_blank) if ct_col and ct_col in df.columns else ""
    out["Top_Agent_Queue"] = by_sc[aq_col].agg(mode_or_blank) if aq_col and aq_col in df.columns else ""

    return out.reset_index().sort_values("Total_Detractors", ascending=False)


@_safe_return
def build_queue_catalog(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Agent queue catalog."""
    aq_col = cols.get("agent_queue")
    nps_col = cols.get("nps")
    if not aq_col or aq_col not in df.columns:
        return pd.DataFrame()

    by_q = df.groupby(aq_col)
    completed = _completed(df, nps_col) if nps_col else df
    comp = completed.groupby(aq_col) if not completed.empty else None
    det = df[df["NPS_Category"] == "Detractor"].groupby(aq_col)

    out = pd.DataFrame({aq_col: sorted(df[aq_col].dropna().unique())}).set_index(aq_col)
    out["Total_Surveys"] = by_q.size()
    out["Completed"] = comp.size() if comp else 0
    out["Detractors"] = det.size()
    out = out.fillna(0)
    out["Detractor_Rate_%"] = (out["Detractors"] / out["Completed"].replace(0, np.nan) * 100).round(2)
    if nps_col and nps_col in df.columns and comp:
        out["Avg_TNPS"] = comp[nps_col].mean().round(2)

    def first(s): return s.iloc[0] if len(s) else ""

    for key, label in [("lev1", "Lev_1"), ("lev2", "Lev_2"),
                        ("lev3", "Lev_3"), ("lev4", "Lev_4"), ("site", "Site")]:
        col = cols.get(key)
        if col and col in df.columns:
            out[label] = by_q[col].agg(first)

    out_reset = out.reset_index()
    out_reset.rename(columns={aq_col: "AGENT_QUEUE"}, inplace=True)
    return out_reset.sort_values("Detractors", ascending=False)


# ============================================================
# MAPPING COVERAGE
# ============================================================
@_safe_return
def build_mapping_coverage(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Unmapped queue report."""
    aq_col = cols.get("agent_queue")
    lev1_col = cols.get("lev1")
    if not lev1_col or lev1_col not in df.columns:
        return pd.DataFrame(columns=["AGENT_QUEUE", "Total_Surveys", "Unmapped_Surveys",
                                     "Unmapped_%", "Recommendation"])

    unmapped = df[df[lev1_col] == "Unmapped"]
    if unmapped.empty:
        return pd.DataFrame(columns=["AGENT_QUEUE", "Total_Surveys", "Unmapped_Surveys",
                                     "Unmapped_%", "Recommendation"])

    if not aq_col or aq_col not in df.columns:
        return pd.DataFrame(columns=["AGENT_QUEUE", "Total_Surveys", "Unmapped_Surveys",
                                     "Unmapped_%", "Recommendation"])

    by_q = df.groupby(aq_col)
    u_by_q = unmapped.groupby(aq_col)

    out = pd.DataFrame({
        aq_col: sorted(unmapped[aq_col].dropna().unique()),
    }).set_index(aq_col)
    out["Total_Surveys"] = by_q.size()
    out["Unmapped_Surveys"] = u_by_q.size()
    out = out.fillna(0)
    out["Unmapped_%"] = (out["Unmapped_Surveys"] / out["Total_Surveys"] * 100).round(1)
    out["Recommendation"] = out["Unmapped_%"].apply(
        lambda x: "URGENT – add to mapping file" if x == 100
        else "Review – partial mapping" if x > 0 else "OK"
    )
    out_reset = out.reset_index().sort_values("Unmapped_Surveys", ascending=False)
    out_reset.rename(columns={aq_col: "AGENT_QUEUE"}, inplace=True)
    return out_reset


# ============================================================
# DUPLICATE DETECTION
# ============================================================
@_safe_return
def build_duplicate_surveys(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Same MSISDN within 24h."""
    msisdn_col = cols.get("msisdn")
    ivr_col = cols.get("ivr_dt")
    sc_col = cols.get("short_code")
    aq_col = cols.get("agent_queue")

    if not msisdn_col or msisdn_col not in df.columns:
        return pd.DataFrame()
    if not ivr_col or ivr_col not in df.columns:
        return pd.DataFrame()

    df2 = df.copy()
    df2[ivr_col] = pd.to_datetime(df2[ivr_col], errors="coerce")
    df2 = df2.sort_values(ivr_col)
    df2["prev_dt"] = df2.groupby(msisdn_col)[ivr_col].shift(1)
    df2["hours_since_prev"] = (
        (df2[ivr_col] - df2["prev_dt"]).dt.total_seconds() / 3600
    )
    dups = df2[df2["hours_since_prev"] < 24].copy()
    if dups.empty:
        return pd.DataFrame()

    keep_cols = [msisdn_col, ivr_col, "prev_dt", "hours_since_prev"]
    if sc_col and sc_col in df.columns:
        keep_cols.append(sc_col)
    if aq_col and aq_col in df.columns:
        keep_cols.append(aq_col)
    if "NPS_Category" in dups.columns:
        keep_cols.append("NPS_Category")

    return dups[[c for c in keep_cols if c in dups.columns]].sort_values("hours_since_prev")


# ============================================================
# DIMENSION BREAKDOWNS
# ============================================================
@_safe_return
def breakdown_by(df: pd.DataFrame, col: str, cols: dict | None = None) -> pd.DataFrame:
    """Generic dimension breakdown."""
    if col not in df.columns:
        return pd.DataFrame()

    nps_col = (cols or {}).get("nps") if cols else None
    # Try to auto-detect nps_col if not in cols
    if not nps_col:
        for c in df.columns:
            if re.search(r"q1.answer.*tnps|tnps|nps_score|q1_answer", c, re.IGNORECASE):
                nps_col = c
                break

    if not nps_col or nps_col not in df.columns:
        return pd.DataFrame()

    completed = df[df[nps_col].notna()]
    if completed.empty:
        return pd.DataFrame()

    g = completed.groupby(col)
    out = pd.DataFrame({col: sorted(completed[col].dropna().astype(str).unique())}).set_index(col)
    out["Completed_Surveys"] = completed.groupby(col).size()
    out["Detractors"] = completed[completed["NPS_Category"] == "Detractor"].groupby(col).size()
    out = out.fillna(0)
    out["Detractor_Rate_%"] = (
        out["Detractors"] / out["Completed_Surveys"].replace(0, np.nan) * 100
    ).round(2)
    out["Avg_TNPS"] = g[nps_col].mean().round(2)
    return out.reset_index().sort_values("Detractors", ascending=False)


@_safe_return
def build_lev3_lev4_heatmap(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """lev3 × lev4 heatmap."""
    lev3_col = cols.get("lev3")
    lev4_col = cols.get("lev4")
    nps_col = cols.get("nps")
    if not lev3_col or not lev4_col or not nps_col:
        return pd.DataFrame()
    if lev3_col not in df.columns or lev4_col not in df.columns or nps_col not in df.columns:
        return pd.DataFrame()

    comp = df[df[nps_col].notna()]
    if comp.empty:
        return pd.DataFrame()

    comp_p = pd.pivot_table(comp, index=lev3_col, columns=lev4_col,
                            values=nps_col, aggfunc="count", fill_value=0)
    det_p = pd.pivot_table(comp[comp["NPS_Category"] == "Detractor"],
                           index=lev3_col, columns=lev4_col,
                           values=nps_col, aggfunc="count", fill_value=0)
    det_p = det_p.reindex(index=comp_p.index, columns=comp_p.columns, fill_value=0)
    rate = (det_p / comp_p.replace(0, np.nan) * 100).round(2).fillna(0)
    rate.index.name = "Lev3 \\ Lev4"
    return rate.reset_index()


# ============================================================
# PATTERNS, REPEATS, CROSS-TABS, AGENTS
# ============================================================
@_safe_return
def build_hour_pattern(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Hour of day pattern."""
    nps_col = cols.get("nps")
    if not nps_col or nps_col not in df.columns or "Hour" not in df.columns:
        return pd.DataFrame()

    comp = df[df[nps_col].notna()]
    out = pd.DataFrame({"Hour": range(24)}).set_index("Hour")
    out["Surveys"] = comp.groupby("Hour").size()
    out["Detractors"] = comp[comp["NPS_Category"] == "Detractor"].groupby("Hour").size()
    out = out.fillna(0)
    out["Detractor_Rate_%"] = (out["Detractors"] / out["Surveys"].replace(0, np.nan) * 100).round(2)
    return out.reset_index().fillna(0)


@_safe_return
def build_dow_pattern(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Day of week pattern."""
    nps_col = cols.get("nps")
    if not nps_col or nps_col not in df.columns or "DayOfWeek" not in df.columns:
        return pd.DataFrame()

    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    comp = df[df[nps_col].notna()]
    out = pd.DataFrame({"Day": order}).set_index("Day")
    out["Surveys"] = comp.groupby("DayOfWeek").size()
    out["Detractors"] = comp[comp["NPS_Category"] == "Detractor"].groupby("DayOfWeek").size()
    out = out.fillna(0)
    out["Detractor_Rate_%"] = (out["Detractors"] / out["Surveys"].replace(0, np.nan) * 100).round(2)
    return out.reset_index().fillna(0)


@_safe_return
def build_repeat_detractors(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Repeat detractors (same MSISDN appeared as detractor >= 2 times)."""
    msisdn_col = cols.get("msisdn")
    ivr_col = cols.get("ivr_dt")
    sc_col = cols.get("short_code")
    aq_col = cols.get("agent_queue")

    if not msisdn_col or msisdn_col not in df.columns:
        return pd.DataFrame()

    det = df[df["NPS_Category"] == "Detractor"]
    if det.empty:
        return pd.DataFrame()

    counts = det.groupby(msisdn_col).size().reset_index(name="Detractor_Count")
    counts = counts[counts["Detractor_Count"] >= 2].sort_values("Detractor_Count", ascending=False)
    if counts.empty:
        return pd.DataFrame()

    if ivr_col and ivr_col in det.columns:
        last_cols = [msisdn_col, ivr_col]
        rename_map = {ivr_col: "Last_Detractor_Date"}
        if sc_col and sc_col in det.columns:
            last_cols.append(sc_col)
            rename_map[sc_col] = "Last_Short_Code"
        if aq_col and aq_col in det.columns:
            last_cols.append(aq_col)
            rename_map[aq_col] = "Last_Queue"
        last = (det.sort_values(ivr_col).groupby(msisdn_col).tail(1)
                [last_cols].rename(columns=rename_map))
        return counts.merge(last, on=msisdn_col, how="left")
    return counts


@_safe_return
def build_q2_q1_crosstab(df: pd.DataFrame, q_col: str, label: str) -> pd.DataFrame:
    """Cross-tab between NPS_Category and a Q2/Q3 column."""
    nps_col = None
    for c in df.columns:
        if re.search(r"q1.answer.*tnps|tnps|q1_answer", c, re.IGNORECASE):
            nps_col = c
            break

    if not nps_col or q_col not in df.columns:
        return pd.DataFrame()

    comp = df[(df[nps_col].notna()) & (df[q_col].notna())]
    if comp.empty:
        return pd.DataFrame()

    ct = pd.crosstab(comp["NPS_Category"], comp[q_col], margins=True, margins_name="Total")
    ct.index.name = f"NPS_Category \\ {label}"
    return ct.reset_index()


@_safe_return
def build_agent_ranking(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Agent ranking with percentile bands."""
    ag_col = cols.get("agent_id")
    nps_col = cols.get("nps")

    if not ag_col or ag_col not in df.columns:
        return pd.DataFrame()
    if not nps_col or nps_col not in df.columns:
        return pd.DataFrame()

    comp = df[df[nps_col].notna()]
    g = comp.groupby(ag_col)
    out = pd.DataFrame({"Agent_Id": sorted(comp[ag_col].dropna().unique())}).set_index("Agent_Id")
    out["Surveys"] = g.size()
    out["Detractors"] = comp[comp["NPS_Category"] == "Detractor"].groupby(ag_col).size()
    out = out.fillna(0)
    out["Detractor_Rate_%"] = (out["Detractors"] / out["Surveys"].replace(0, np.nan) * 100).round(2)
    out["Avg_TNPS"] = g[nps_col].mean().round(2)
    out = out[out["Surveys"] >= _CONFIG["min_agent_sample"]]

    if not out.empty:
        out["Percentile"] = out["Detractor_Rate_%"].rank(pct=True).mul(100).round(0).astype(int)
        out["Band"] = pd.cut(out["Percentile"],
                             bins=[0, 10, 25, 75, 90, 100],
                             labels=["Top 10%", "Top 25%", "Mid 50%", "Bottom 25%", "Bottom 10%"],
                             include_lowest=True)
    return out.reset_index().sort_values("Detractor_Rate_%", ascending=False)


# ============================================================
# AGENT PEER BENCHMARKING
# ============================================================
@_safe_return
def build_agent_peer_benchmark(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Peer benchmarking - agent vs queue average."""
    ag_col = cols.get("agent_id")
    aq_col = cols.get("agent_queue")
    nps_col = cols.get("nps")

    if not ag_col or ag_col not in df.columns:
        return pd.DataFrame()
    if not aq_col or aq_col not in df.columns:
        return pd.DataFrame()
    if not nps_col or nps_col not in df.columns:
        return pd.DataFrame()

    comp = df[df[nps_col].notna()]
    agent = comp.groupby([ag_col, aq_col]).agg(
        Surveys=(nps_col, "count"),
        Avg_TNPS=(nps_col, "mean"),
    ).reset_index()
    det = (comp[comp["NPS_Category"] == "Detractor"]
           .groupby([ag_col, aq_col]).size().reset_index(name="Detractors"))
    agent = agent.merge(det, on=[ag_col, aq_col], how="left").fillna(0)
    agent["Detractor_Rate_%"] = (agent["Detractors"] / agent["Surveys"] * 100).round(2)
    agent = agent[agent["Surveys"] >= _CONFIG["min_agent_sample"]]
    if agent.empty:
        return pd.DataFrame()

    qbench = agent.groupby(aq_col)["Detractor_Rate_%"].agg(
        Queue_Avg="mean", Queue_Median="median"
    ).reset_index()
    agent = agent.merge(qbench, on=aq_col, how="left")
    agent["vs_Queue_Avg"] = (agent["Detractor_Rate_%"] - agent["Queue_Avg"]).round(2)
    agent["Peer_Rank"] = agent.groupby(aq_col)["Detractor_Rate_%"].rank(ascending=True).astype(int)
    return agent.sort_values([aq_col, "Peer_Rank"])


# ============================================================
# COHORT ANALYSIS
# ============================================================
@_safe_return
def build_cohort_analysis(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """MSISDN cohort tracking - first detractor month -> subsequent category."""
    msisdn_col = cols.get("msisdn")
    nps_col = cols.get("nps")

    if not msisdn_col or msisdn_col not in df.columns:
        return pd.DataFrame()
    if "Month" not in df.columns:
        return pd.DataFrame()

    comp_cols = [msisdn_col, "Month", "NPS_Category"]
    comp = df[df[nps_col].notna()][comp_cols].copy() if nps_col and nps_col in df.columns else df[comp_cols].copy()

    first_det = (
        comp[comp["NPS_Category"] == "Detractor"]
        .groupby(msisdn_col)["Month"].min().reset_index(name="First_Detractor_Month")
    )
    if first_det.empty:
        return pd.DataFrame()

    cohort = comp.merge(first_det, on=msisdn_col)
    cohort = cohort[cohort["Month"] > cohort["First_Detractor_Month"]]
    if cohort.empty:
        return pd.DataFrame()

    summary = cohort.groupby(["First_Detractor_Month", "NPS_Category"]).size().unstack(fill_value=0)
    summary.index.name = "Cohort_Month (First_Detractor)"
    total = summary.sum(axis=1)
    for col in summary.columns:
        summary[f"{col}_%"] = (summary[col] / total * 100).round(1)

    if "Promoter" in summary.columns:
        summary["Recovery_Rate_%"] = (summary["Promoter"] / total * 100).round(1)

    return summary.reset_index()


# ============================================================
# ROOT-CAUSE TOXIC COMBOS
# ============================================================
@_safe_return
def build_toxic_combos(df: pd.DataFrame, cols: dict, top_n: int = 20) -> pd.DataFrame:
    """Top dimension combinations driving detractors."""
    nps_col = cols.get("nps")
    if not nps_col or nps_col not in df.columns:
        return pd.DataFrame()

    dims = []
    for key in ("owner_team", "substatus", "call_type", "agent_queue"):
        col = cols.get(key)
        if col and col in df.columns:
            dims.append(col)
    if len(dims) < 2:
        return pd.DataFrame()

    comp = df[df[nps_col].notna()].copy()
    comp["_combo"] = comp[dims].fillna("?").astype(str).agg(" | ".join, axis=1)

    g = comp.groupby("_combo")
    out = pd.DataFrame({"Combo": list(g.groups.keys())}).set_index("Combo")
    out["Surveys"] = g.size()
    out["Detractors"] = comp[comp["NPS_Category"] == "Detractor"].groupby("_combo").size()
    out = out.fillna(0)
    out["Detractor_Rate_%"] = (out["Detractors"] / out["Surveys"].replace(0, np.nan) * 100).round(2)
    out["Dimensions"] = " | ".join(dims)
    return (
        out[out["Surveys"] >= 5]
        .reset_index()
        .sort_values("Detractor_Rate_%", ascending=False)
        .head(top_n)
    )


# ============================================================
# VELOCITY ALERTS
# ============================================================
@_safe_return
def build_velocity_alerts(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Week-over-week detractor rate jump per queue."""
    nps_col = cols.get("nps")
    aq_col = cols.get("agent_queue")

    if not nps_col or nps_col not in df.columns:
        return pd.DataFrame()
    if not aq_col or aq_col not in df.columns:
        return pd.DataFrame()
    if "Week" not in df.columns:
        return pd.DataFrame()

    comp = df[df[nps_col].notna()]
    g = comp.groupby([aq_col, "Week"])
    weekly = g.agg(Surveys=(nps_col, "count")).reset_index()
    det_w = (comp[comp["NPS_Category"] == "Detractor"]
             .groupby([aq_col, "Week"]).size().reset_index(name="Detractors"))
    weekly = weekly.merge(det_w, on=[aq_col, "Week"], how="left").fillna(0)
    weekly["Detractor_Rate_%"] = (weekly["Detractors"] / weekly["Surveys"] * 100).round(2)
    weekly = weekly.sort_values([aq_col, "Week"])
    weekly["Prev_Rate"] = weekly.groupby(aq_col)["Detractor_Rate_%"].shift(1)
    weekly["WoW_Change_%"] = (weekly["Detractor_Rate_%"] - weekly["Prev_Rate"]).round(2)
    threshold = _CONFIG["velocity_alert_pct"]
    alerts = weekly[weekly["WoW_Change_%"].abs() >= threshold].copy()
    alerts["Alert"] = alerts["WoW_Change_%"].apply(
        lambda x: f"↑ SPIKE +{x:.1f}%" if x > 0 else f"↓ DROP {x:.1f}%"
    )
    out = alerts.sort_values("WoW_Change_%", ascending=False)
    out.rename(columns={aq_col: "AGENT_QUEUE"}, inplace=True)
    return out


# ============================================================
# TOP 10 / BOTTOM 10
# ============================================================
@_safe_return
def build_top_bottom(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Top/bottom 10 queues and short codes."""
    aq_col = cols.get("agent_queue")
    sc_col = cols.get("short_code")

    q_cat = build_queue_catalog(df, cols)
    sc_cat = build_shortcode_catalog(df, cols)

    parts = []
    if not q_cat.empty and "Detractor_Rate_%" in q_cat.columns:
        q_top = q_cat.nsmallest(10, "Detractor_Rate_%")[
            ["AGENT_QUEUE", "Detractors", "Detractor_Rate_%", "Avg_TNPS"]
        ].copy() if "Avg_TNPS" in q_cat.columns else q_cat.nsmallest(10, "Detractor_Rate_%")[
            ["AGENT_QUEUE", "Detractors", "Detractor_Rate_%"]
        ].copy()
        q_bottom = q_cat.nlargest(10, "Detractor_Rate_%")[
            list(q_top.columns)
        ].copy()
        q_top["Rank_Type"] = "Best 10 Queues"
        q_bottom["Rank_Type"] = "Worst 10 Queues"
        rename_q = {"AGENT_QUEUE": "Name", "Detractors": "Detractor_Count"}
        q_top.rename(columns=rename_q, inplace=True)
        q_bottom.rename(columns=rename_q, inplace=True)
        parts.extend([q_top, q_bottom])

    if not sc_cat.empty and "Detractor_Rate_%" in sc_cat.columns:
        sc_col_name = sc_cat.columns[0]
        sc_top = sc_cat.nsmallest(10, "Detractor_Rate_%")[
            [sc_col_name, "Total_Detractors", "Detractor_Rate_%"]
        ].copy()
        sc_btm = sc_cat.nlargest(10, "Detractor_Rate_%")[
            list(sc_top.columns)
        ].copy()
        sc_top["Rank_Type"] = "Best 10 Short Codes"
        sc_btm["Rank_Type"] = "Worst 10 Short Codes"
        rename_sc = {sc_col_name: "Name", "Total_Detractors": "Detractor_Count"}
        sc_top.rename(columns=rename_sc, inplace=True)
        sc_btm.rename(columns=rename_sc, inplace=True)
        parts.extend([sc_top, sc_btm])

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


# ============================================================
# NPS WATERFALL
# ============================================================
@_safe_return
def build_nps_waterfall(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Monthly NPS waterfall."""
    nps_col = cols.get("nps")
    if not nps_col or nps_col not in df.columns or "Month" not in df.columns:
        return pd.DataFrame()

    months = sorted(df["Month"].dropna().unique())
    rows = []
    for m in months:
        sub = df[(df["Month"] == m) & (df[nps_col].notna())]
        n = len(sub)
        d = (sub["NPS_Category"] == "Detractor").sum()
        p = (sub["NPS_Category"] == "Promoter").sum()
        rows.append({
            "Month": m,
            "Completed": n,
            "Promoters": int(p),
            "Detractors": int(d),
            "Net_Gain": int(p - d),
            "NPS_Score": round((p - d) / n * 100, 2) if n else 0,
            "Promoter_Rate_%": round(p / n * 100, 2) if n else 0,
            "Detractor_Rate_%": round(d / n * 100, 2) if n else 0,
        })
    return pd.DataFrame(rows)


# ============================================================
# SLA BREACH HEATMAP
# ============================================================
@_safe_return
def build_sla_breach_heatmap(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """SLA breach heatmap (queues × days that breached threshold)."""
    nps_col = cols.get("nps")
    aq_col = cols.get("agent_queue")

    if not nps_col or nps_col not in df.columns:
        return pd.DataFrame()
    if not aq_col or aq_col not in df.columns:
        return pd.DataFrame()
    if "Date" not in df.columns:
        return pd.DataFrame()

    comp = df[df[nps_col].notna()]
    if comp.empty:
        return pd.DataFrame()

    threshold = _CONFIG["sla_detractor_rate_threshold"]
    pivot_surveys = pd.pivot_table(comp, index=aq_col, columns="Date",
                                   values=nps_col, aggfunc="count", fill_value=0)
    pivot_det = pd.pivot_table(comp[comp["NPS_Category"] == "Detractor"],
                               index=aq_col, columns="Date",
                               values=nps_col, aggfunc="count", fill_value=0)
    pivot_det = pivot_det.reindex(index=pivot_surveys.index,
                                  columns=pivot_surveys.columns, fill_value=0)
    rate = (pivot_det / pivot_surveys.replace(0, np.nan) * 100).round(1).fillna(0)
    breach = rate.apply(lambda col: col.map(lambda x: x if x >= threshold else 0))
    breach.index.name = "Queue \\ Date"
    breach = breach[breach.sum(axis=1) > 0]
    return breach.reset_index() if not breach.empty else pd.DataFrame()


# ============================================================
# CHANNEL COMPARISON
# ============================================================
@_safe_return
def build_channel_comparison(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Channel comparison."""
    ch_col = cols.get("channel")
    if not ch_col or ch_col not in df.columns:
        return pd.DataFrame()
    return breakdown_by(df, ch_col, cols)


# ============================================================
# FCR IMPACT
# ============================================================
@_safe_return
def build_fcr_impact(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """FCR impact."""
    fcr_col = cols.get("fcr_flag")
    if not fcr_col or fcr_col not in df.columns:
        return pd.DataFrame()
    return breakdown_by(df, fcr_col, cols)


# ============================================================
# EXECUTIVE SUMMARY
# ============================================================
@_safe_return
def build_executive_summary(df: pd.DataFrame, cols: dict, kpi: pd.DataFrame,
                             daily: pd.DataFrame, forecast: pd.DataFrame) -> pd.DataFrame:
    """Narrative executive summary."""
    def kv(metric):
        if kpi is None or kpi.empty:
            return "N/A"
        row = kpi[kpi["Metric"] == metric]
        return row["Value"].iloc[0] if not row.empty else "N/A"

    det_rate = kv("Detractor Rate %")
    nps = kv("NPS Score")
    total = kv("Total Surveys Sent")
    n_det = kv("Detractors (Q1 0-6)")

    trend = "Insufficient data for trend"
    if daily is not None and not daily.empty and len(daily) >= 14:
        mid = len(daily) // 2
        first = daily.iloc[:mid]["Detractor_Rate_%"].mean() if "Detractor_Rate_%" in daily.columns else 0
        second = daily.iloc[mid:]["Detractor_Rate_%"].mean() if "Detractor_Rate_%" in daily.columns else 0
        trend = "↑ WORSENING" if second > first else "↓ IMPROVING"

    nps_col = cols.get("nps")
    aq_col = cols.get("agent_queue")
    top_q = "N/A"
    top_qv = 0
    if nps_col and nps_col in df.columns and aq_col and aq_col in df.columns:
        comp = df[df[nps_col].notna()]
        if not comp.empty:
            qg = comp[comp["NPS_Category"] == "Detractor"].groupby(aq_col).size()
            if not qg.empty:
                top_q = qg.idxmax()
                top_qv = int(qg.max())

    breach_info = ""
    if forecast is not None and not forecast.empty and "Forecast_Detractor_Rate_%" in forecast.columns:
        breach_info = forecast.attrs.get("breach_date", "N/A")

    lines = [
        ("Period", f"{kv('Date Range Start')} → {kv('Date Range End')} ({kv('Number of Days Covered')} days)"),
        ("Total Surveys", f"{total:,}" if isinstance(total, int) else str(total)),
        ("Detractors", str(n_det)),
        ("Detractor Rate", f"{det_rate}%"),
        ("NPS Score", str(nps)),
        ("Period Trend", trend),
        ("Top Driver Queue", f"{top_q}  ({top_qv:,} detractors)"),
        ("Forecast Breach Date", breach_info),
        ("Recommendation", "Prioritise top driver queue and escalate toxic short codes to product team."),
    ]
    return pd.DataFrame(lines, columns=["Summary_Item", "Detail"])


# ============================================================
# SEVERITY SCORING
# ============================================================

@_safe_return
def build_severity_scores(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Classify each detractor row as Critical / High / Medium severity.

    Severity is based on NPS score (lower = worse) then bumped up one level
    if the caller's MSISDN appears multiple times (repeat caller).
    Returns a summary counts DataFrame.
    """
    nps_col = cols.get("nps")
    msisdn_col = cols.get("msisdn")
    if not nps_col or nps_col not in df.columns:
        return pd.DataFrame()

    work = df.copy()
    score = pd.to_numeric(work[nps_col], errors="coerce")

    def _base(v):
        if pd.isna(v) or v > 6:
            return None
        if v <= 2:
            return "Critical"
        if v <= 4:
            return "High"
        return "Medium"

    work["Severity"] = score.map(_base)
    det = work[work["Severity"].notna()].copy()
    if det.empty:
        return pd.DataFrame()

    # repeat-caller bump: MSISDN appears >1 time → promote severity one step
    if msisdn_col and msisdn_col in det.columns:
        step = {"Medium": "High", "High": "Critical", "Critical": "Critical"}
        repeat_ids = set(det[msisdn_col].value_counts()[lambda x: x > 1].index)
        det["Severity"] = det.apply(
            lambda r: step[r["Severity"]] if r[msisdn_col] in repeat_ids else r["Severity"],
            axis=1,
        )

    order = {"Critical": 0, "High": 1, "Medium": 2}
    summary = (det["Severity"].value_counts()
                              .rename_axis("Severity")
                              .reset_index(name="Count"))
    summary["_ord"] = summary["Severity"].map(order)
    summary = summary.sort_values("_ord").drop(columns="_ord").reset_index(drop=True)
    summary["Pct_%"] = (summary["Count"] / len(det) * 100).round(1)
    summary["Meaning"] = summary["Severity"].map({
        "Critical": "Score 0–2  (may have repeat-caller bump)",
        "High":     "Score 3–4  (may have repeat-caller bump)",
        "Medium":   "Score 5–6",
    })
    return summary
