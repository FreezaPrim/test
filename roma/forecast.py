"""Holt-Winters forecasting for TNPS analytics.

Uses statsmodels ExponentialSmoothing when available; falls back to a
simple mean-based forecast if not installed.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    _HW_AVAILABLE = True
except ImportError:
    _HW_AVAILABLE = False


# ============================================================
# CORE FORECAST
# ============================================================

def forecast_series(series: pd.Series, horizon: int, label: str
                    ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (forecast, lower_CI, upper_CI) using Holt-Winters.

    Falls back to mean-based forecast if statsmodels is unavailable or
    there is insufficient data.
    """
    s = series.dropna()
    mean_val = float(s.mean()) if len(s) > 0 else 0.0

    if len(s) < 4:
        base = pd.Series([mean_val] * horizon)
        base_lo = pd.Series([max(0.0, mean_val) for _ in range(horizon)])
        return base.clip(lower=0), base_lo, base

    if not _HW_AVAILABLE:
        # Simple linear extrapolation fallback
        base = pd.Series([mean_val] * horizon)
        std = float(s.std()) if len(s) > 1 else 0.0
        lo = pd.Series([max(0.0, mean_val - 1.96 * std)] * horizon)
        hi = pd.Series([mean_val + 1.96 * std] * horizon)
        return base, lo, hi

    try:
        if len(s) >= 14:
            model = ExponentialSmoothing(
                s,
                seasonal_periods=7,
                seasonal="add",
                trend="add",
                initialization_method="estimated",
            ).fit()
        else:
            model = ExponentialSmoothing(
                s,
                trend="add",
                initialization_method="estimated",
            ).fit()

        f = model.forecast(horizon).clip(lower=0)
        # Simple CI: ±1.96 * residual std
        resid_std = float(model.resid.std()) if hasattr(model, "resid") else 0.0
        ci_width = 1.96 * resid_std
        lower = (f - ci_width).clip(lower=0)
        upper = f + ci_width
        return f.reset_index(drop=True), lower.reset_index(drop=True), upper.reset_index(drop=True)

    except Exception:
        base = pd.Series([mean_val] * horizon)
        lo = pd.Series([max(0.0, mean_val)] * horizon)
        return base, lo, base


def build_forecast(daily_df: pd.DataFrame, horizon: int = 30) -> pd.DataFrame:
    """Overall forecast for surveys, detractors, detractor rate, NPS.

    Parameters
    ----------
    daily_df:
        Result of build_daily_trend() - must have a 'Date' column.
    horizon:
        Number of days to forecast.

    Returns
    -------
    DataFrame with Date, Forecast_* columns.  Attaches 'breach_date' to
    result.attrs if detractor rate target is projected to be breached.
    """
    if daily_df is None or daily_df.empty:
        return pd.DataFrame()

    d = daily_df.copy()
    d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
    d = d.dropna(subset=["Date"]).set_index("Date").sort_index()

    if d.empty:
        return pd.DataFrame()

    last_date = d.index.max()
    future_dates = [last_date + timedelta(days=i + 1) for i in range(horizon)]
    out = pd.DataFrame({"Date": future_dates})

    _SERIES_MAP = [
        ("Surveys_Sent",       "Survey_Volume"),
        ("Detractors",         "Detractor_Count"),
        ("Detractor_Rate_%",   "Detractor_Rate_%"),
        ("NPS_Score",          "NPS_Score"),
        ("Avg_TNPS",           "Average_TNPS"),
    ]
    for col, label in _SERIES_MAP:
        if col not in d.columns:
            continue
        f, lo, hi = forecast_series(d[col], horizon, label)
        out[f"Forecast_{col}"] = f.values.round(2)
        out[f"Forecast_{col}_Lower"] = lo.values.round(2)
        out[f"Forecast_{col}_Upper"] = hi.values.round(2)

    # Breach date: when does forecast detractor rate exceed target?
    _TARGET = 40  # default detractor rate target %
    if "Forecast_Detractor_Rate_%" in out.columns:
        breach_rows = out[out["Forecast_Detractor_Rate_%"] >= _TARGET]
        breach_date = (
            str(breach_rows["Date"].iloc[0].date())
            if not breach_rows.empty else "No breach in horizon"
        )
        out.attrs["breach_date"] = breach_date

    return out


def build_per_queue_forecast(df: pd.DataFrame, cols: dict,
                              daily_df: pd.DataFrame, horizon: int = 30,
                              top_n: int = 5) -> pd.DataFrame:
    """Per-queue Holt-Winters forecast for top N queues.

    Parameters
    ----------
    df:
        Full enriched TNPS DataFrame (from load_tnps_df).
    cols:
        Column detection dict from detect_tnps_columns().
    daily_df:
        Result of build_daily_trend().
    horizon:
        Days to forecast.
    top_n:
        Number of top queues by detractor count to forecast.

    Returns
    -------
    DataFrame with Date plus per-queue rate/lower/upper columns.
    """
    nps_col = cols.get("nps")
    aq_col = cols.get("agent_queue")

    if daily_df is None or daily_df.empty:
        return pd.DataFrame()
    if not nps_col or not aq_col:
        return pd.DataFrame()
    if nps_col not in df.columns or aq_col not in df.columns:
        return pd.DataFrame()

    top_queues = (
        df[df["NPS_Category"] == "Detractor"]
        .groupby(aq_col).size()
        .nlargest(top_n).index.tolist()
    )
    if not top_queues:
        return pd.DataFrame()

    last_date = pd.to_datetime(daily_df["Date"]).max()
    future_dates = [last_date + timedelta(days=i + 1) for i in range(horizon)]
    out = pd.DataFrame({"Date": future_dates})

    for q in top_queues:
        sub = df[(df[aq_col] == q) & (df[nps_col].notna())].copy()
        if sub.empty or "Date" not in sub.columns:
            continue
        sub["Date"] = pd.to_datetime(sub["Date"], errors="coerce")
        sub = sub.dropna(subset=["Date"])

        daily_q = (
            sub.groupby("Date").apply(
                lambda x: pd.Series({
                    "Det_Rate": (
                        (x["NPS_Category"] == "Detractor").sum() / len(x) * 100
                    )
                })
            )
            .reset_index()
        )
        daily_q = daily_q.set_index("Date").sort_index()

        f, lo, hi = forecast_series(daily_q["Det_Rate"], horizon, q)
        safe_name = str(q)[:20].replace(" ", "_").replace("/", "-")
        out[f"{safe_name}_Rate"] = f.values.round(2)
        out[f"{safe_name}_Lower"] = lo.values.round(2)
        out[f"{safe_name}_Upper"] = hi.values.round(2)

    return out
