"""Win-back and churn-recovery analytics for Roma.

Tracks NPS category transitions month-over-month per customer (MSISDN).
A "recovery" = detractor in month N → non-detractor in month N+1.
A "churn"    = non-detractor in month N → detractor in month N+1.
"""

from __future__ import annotations

import pandas as pd


def _add_category(df: pd.DataFrame, nps_col: str) -> pd.DataFrame:
    s = pd.to_numeric(df[nps_col], errors="coerce")
    df = df.copy()
    df["_nps_cat"] = s.map(
        lambda v: "Promoter" if v >= 9 else ("Passive" if v >= 7 else "Detractor")
        if pd.notna(v) else None
    )
    return df


def _cohort(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Return a DataFrame of MSISDN-level month-to-month NPS transitions."""
    msisdn_col = cols.get("msisdn")
    nps_col = cols.get("nps")
    date_col = cols.get("date")
    if not (msisdn_col and nps_col and date_col):
        return pd.DataFrame()
    needed = [msisdn_col, nps_col, date_col]
    work = df[needed].copy()
    work[nps_col] = pd.to_numeric(work[nps_col], errors="coerce")
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work = work.dropna()
    work = _add_category(work, nps_col)
    work["_month"] = work[date_col].dt.to_period("M").astype(str)
    # one row per MSISDN per month (use latest score in that month)
    grp = (work.sort_values(date_col)
               .groupby([msisdn_col, "_month"])["_nps_cat"]
               .last()
               .reset_index())
    months = sorted(grp["_month"].unique())
    if len(months) < 2:
        return pd.DataFrame()
    records = []
    for i in range(len(months) - 1):
        m0, m1 = months[i], months[i + 1]
        left = grp[grp["_month"] == m0][[msisdn_col, "_month", "_nps_cat"]].rename(
            columns={"_month": "Month_From", "_nps_cat": "Cat_From"})
        right = grp[grp["_month"] == m1][[msisdn_col, "_month", "_nps_cat"]].rename(
            columns={"_month": "Month_To", "_nps_cat": "Cat_To"})
        merged = left.merge(right, on=msisdn_col)
        merged["Recovered"] = (
            (merged["Cat_From"] == "Detractor") & (merged["Cat_To"] != "Detractor")
        ).astype(int)
        merged["Churned"] = (
            (merged["Cat_From"] != "Detractor") & (merged["Cat_To"] == "Detractor")
        ).astype(int)
        records.append(merged)
    return pd.concat(records, ignore_index=True) if records else pd.DataFrame()


def build_winback_summary(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Recovery and churn rate by month-pair."""
    cohort = _cohort(df, cols)
    if cohort.empty:
        return pd.DataFrame()
    agg = cohort.groupby("Month_From").agg(
        Detractors_Start=("Cat_From", lambda x: (x == "Detractor").sum()),
        Recovered=("Recovered", "sum"),
        New_Churned=("Churned", "sum"),
        Total_Tracked=("Cat_From", "count"),
    ).reset_index().rename(columns={"Month_From": "Month"})
    agg["Recovery_Rate_%"] = (
        agg["Recovered"] / agg["Detractors_Start"].clip(lower=1) * 100
    ).round(1)
    agg["Churn_Rate_%"] = (
        agg["New_Churned"] / agg["Total_Tracked"].clip(lower=1) * 100
    ).round(1)
    return agg


def build_winback_by_dimension(df: pd.DataFrame, cols: dict,
                                dim_col: str) -> pd.DataFrame:
    """Recovery rate broken down by a dimension (queue, shortcode, team)."""
    msisdn_col = cols.get("msisdn")
    if not msisdn_col or dim_col not in df.columns:
        return pd.DataFrame()
    cohort = _cohort(df, cols)
    if cohort.empty:
        return pd.DataFrame()
    # attach dimension from original df (last seen value per MSISDN)
    dim_map = (df.dropna(subset=[msisdn_col, dim_col])
                 .sort_values(cols.get("date", msisdn_col))
                 .groupby(msisdn_col)[dim_col]
                 .last()
                 .reset_index())
    cohort_dim = cohort.merge(dim_map, on=msisdn_col, how="left")
    agg = cohort_dim.groupby(dim_col).agg(
        Detractors=("Cat_From", lambda x: (x == "Detractor").sum()),
        Recovered=("Recovered", "sum"),
    ).reset_index()
    agg["Recovery_Rate_%"] = (
        agg["Recovered"] / agg["Detractors"].clip(lower=1) * 100
    ).round(1)
    return agg.sort_values("Recovery_Rate_%", ascending=False).reset_index(drop=True)


def build_winback_transitions(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Transition matrix: count of each From→To category pair across all months."""
    cohort = _cohort(df, cols)
    if cohort.empty:
        return pd.DataFrame()
    matrix = (cohort.groupby(["Cat_From", "Cat_To"])
                    .size()
                    .reset_index(name="Count"))
    matrix["Pct_%"] = (
        matrix["Count"] / matrix.groupby("Cat_From")["Count"].transform("sum") * 100
    ).round(1)
    return matrix.sort_values(["Cat_From", "Count"], ascending=[True, False])
