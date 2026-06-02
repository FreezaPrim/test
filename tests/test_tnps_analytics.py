"""Tests for Roma's TNPS analytics integration."""
import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta
import sqlite3
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from roma.tnps_analytics import (
    detect_tnps_columns, load_tnps_df,
    build_kpi_summary, build_daily_trend, build_monthly_trend,
    build_shortcode_catalog, build_queue_catalog,
    build_repeat_detractors, build_toxic_combos, build_velocity_alerts,
    build_cohort_analysis, build_nps_waterfall, build_top_bottom,
    build_duplicate_surveys, build_mapping_coverage,
    build_hour_pattern, build_dow_pattern, build_agent_ranking,
    build_agent_peer_benchmark, breakdown_by,
)
from roma.forecast import forecast_series, build_forecast


def make_test_df(n=200, seed=42):
    """Create a synthetic TNPS DataFrame for testing."""
    rng = np.random.default_rng(seed)
    base = date(2026, 1, 1)
    dates = [base + timedelta(days=int(d)) for d in rng.integers(0, 120, n)]
    return pd.DataFrame({
        "OUTBOUND_IVR_DT": pd.to_datetime(dates),
        "Q1_ANSWER__TNPS": rng.integers(0, 11, n),  # sanitized col name
        "SHORT_CODE": rng.choice(["SC001", "SC002", "SC003", "SC004"], n),
        "AGENT_QUEUE": rng.choice(["QueueA", "QueueB", "QueueC"], n),
        "MSISDN": rng.integers(100000, 999999, n).astype(str),
        "OWNER_TEAM": rng.choice(["Team1", "Team2"], n),
        "CALL_TYPE": rng.choice(["Inbound", "Outbound"], n),
        "Agent_Id_": rng.choice(["A1", "A2", "A3", "A4", "A5"], n),
        "Q_Mapping_Lev_1": rng.choice(["Seg1", "Seg2", "Unmapped"], n),
        "Q_Mapping_Lev_2_Combined": rng.choice(["L2A", "L2B"], n),
        "Q_Mapping_Lev_3_New_PF_Seg": rng.choice(["L3A", "L3B"], n),
        "Q_Mapping_Lev_4_Seg": rng.choice(["L4A", "L4B"], n),
        "Site": rng.choice(["Cairo", "Alex"], n),
        "Month": "",  # will be enriched
        "Week": "",
        "Hour": 0,
        "DayOfWeek": "",
        "NPS_Category": "",
        "Date": [d for d in dates],
    })


def make_enriched_df(n=200):
    """Make a fully enriched test DataFrame (with Month, NPS_Category etc)."""
    df = make_test_df(n)
    df["Date"] = df["OUTBOUND_IVR_DT"].dt.date
    df["Month"] = df["OUTBOUND_IVR_DT"].dt.to_period("M").astype(str)
    df["Week"] = df["OUTBOUND_IVR_DT"].dt.to_period("W").astype(str)
    df["Hour"] = df["OUTBOUND_IVR_DT"].dt.hour
    df["DayOfWeek"] = df["OUTBOUND_IVR_DT"].dt.day_name()
    df["NPS_Category"] = pd.cut(
        df["Q1_ANSWER__TNPS"], bins=[-1, 6, 8, 10],
        labels=["Detractor", "Passive", "Promoter"]
    ).astype(str)
    return df


def make_cols():
    return {
        "nps": "Q1_ANSWER__TNPS",
        "ivr_dt": "OUTBOUND_IVR_DT",
        "short_code": "SHORT_CODE",
        "agent_queue": "AGENT_QUEUE",
        "msisdn": "MSISDN",
        "owner_team": "OWNER_TEAM",
        "substatus": None,
        "call_type": "CALL_TYPE",
        "fcr_flag": None,
        "channel": None,
        "prod_type": None,
        "reachability": None,
        "agent_id": "Agent_Id_",
        "lev1": "Q_Mapping_Lev_1",
        "lev2": "Q_Mapping_Lev_2_Combined",
        "lev3": "Q_Mapping_Lev_3_New_PF_Seg",
        "lev4": "Q_Mapping_Lev_4_Seg",
        "site": "Site",
    }


class TestColumnDetection:
    def test_detects_nps_column(self):
        df = pd.DataFrame({"Q1_ANSWER__TNPS": [1, 2, 3], "SHORT_CODE": ["a", "b", "c"]})
        cols = detect_tnps_columns(df)
        assert cols["nps"] == "Q1_ANSWER__TNPS"

    def test_handles_missing_columns(self):
        df = pd.DataFrame({"other_col": [1, 2, 3]})
        cols = detect_tnps_columns(df)
        assert cols["nps"] is None
        assert cols["msisdn"] is None

    def test_detects_msisdn(self):
        df = pd.DataFrame({"MSISDN": ["123", "456"], "Q1_ANSWER__TNPS": [5, 8]})
        cols = detect_tnps_columns(df)
        assert cols["msisdn"] == "MSISDN"

    def test_detects_agent_queue(self):
        df = pd.DataFrame({"AGENT_QUEUE": ["Q1", "Q2"], "Q1_ANSWER__TNPS": [5, 8]})
        cols = detect_tnps_columns(df)
        assert cols["agent_queue"] == "AGENT_QUEUE"

    def test_detects_short_code(self):
        df = pd.DataFrame({"SHORT_CODE": ["SC1", "SC2"], "Q1_ANSWER__TNPS": [5, 8]})
        cols = detect_tnps_columns(df)
        assert cols["short_code"] == "SHORT_CODE"


class TestKPISummary:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_kpi_summary(df, cols)
        assert isinstance(result, pd.DataFrame)
        assert "Metric" in result.columns
        assert "Value" in result.columns

    def test_contains_key_metrics(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_kpi_summary(df, cols)
        metrics = result["Metric"].tolist()
        assert any("Detractor" in str(m) for m in metrics)
        assert any("NPS" in str(m) for m in metrics)

    def test_handles_empty_df(self):
        df = pd.DataFrame()
        cols = {k: None for k in make_cols()}
        result = build_kpi_summary(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_has_vs_prev_month(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_kpi_summary(df, cols)
        assert "vs_Prev_Month" in result.columns


class TestDailyTrend:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_daily_trend(df, cols)
        assert isinstance(result, pd.DataFrame)
        assert "Date" in result.columns

    def test_has_detractor_rate(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_daily_trend(df, cols)
        assert "Detractor_Rate_%" in result.columns

    def test_has_rolling_avg(self):
        df = make_enriched_df(400)  # enough data for rolling
        cols = make_cols()
        result = build_daily_trend(df, cols)
        assert "Detractor_Rate_7d_Avg" in result.columns

    def test_missing_nps_returns_empty(self):
        df = make_enriched_df()
        cols = make_cols()
        cols["nps"] = None
        result = build_daily_trend(df, cols)
        assert result.empty


class TestMonthlyTrend:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_monthly_trend(df, cols)
        assert isinstance(result, pd.DataFrame)
        assert "Month" in result.columns

    def test_has_mom_change(self):
        df = make_enriched_df(400)
        cols = make_cols()
        result = build_monthly_trend(df, cols)
        assert "MoM_Detractors_%" in result.columns

    def test_has_nps_score(self):
        df = make_enriched_df(200)
        cols = make_cols()
        result = build_monthly_trend(df, cols)
        assert "NPS_Score" in result.columns


class TestShortCodeCatalog:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_shortcode_catalog(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_sorted_by_detractors(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_shortcode_catalog(df, cols)
        if len(result) > 1:
            assert result["Total_Detractors"].iloc[0] >= result["Total_Detractors"].iloc[-1]

    def test_missing_short_code_returns_empty(self):
        df = make_enriched_df()
        cols = make_cols()
        cols["short_code"] = None
        result = build_shortcode_catalog(df, cols)
        assert result.empty


class TestQueueCatalog:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_queue_catalog(df, cols)
        assert isinstance(result, pd.DataFrame)
        assert "AGENT_QUEUE" in result.columns

    def test_has_detractor_rate(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_queue_catalog(df, cols)
        assert "Detractor_Rate_%" in result.columns


class TestRepeatDetractors:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_repeat_detractors(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_all_have_count_ge_2(self):
        df = make_enriched_df(500)
        cols = make_cols()
        result = build_repeat_detractors(df, cols)
        if not result.empty:
            assert (result["Detractor_Count"] >= 2).all()

    def test_missing_msisdn_returns_empty(self):
        df = make_enriched_df()
        cols = make_cols()
        cols["msisdn"] = None
        result = build_repeat_detractors(df, cols)
        assert result.empty


class TestToxicCombos:
    def test_returns_dataframe(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_toxic_combos(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_sorted_by_detractor_rate(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_toxic_combos(df, cols)
        if len(result) > 1:
            assert result["Detractor_Rate_%"].iloc[0] >= result["Detractor_Rate_%"].iloc[-1]

    def test_has_combo_column(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_toxic_combos(df, cols)
        if not result.empty:
            assert "Combo" in result.columns


class TestVelocityAlerts:
    def test_returns_dataframe(self):
        df = make_enriched_df(400)
        cols = make_cols()
        result = build_velocity_alerts(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_missing_nps_returns_empty(self):
        df = make_enriched_df(400)
        cols = make_cols()
        cols["nps"] = None
        result = build_velocity_alerts(df, cols)
        assert result.empty


class TestCohortAnalysis:
    def test_returns_dataframe(self):
        df = make_enriched_df(400)
        cols = make_cols()
        result = build_cohort_analysis(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_no_msisdn_returns_empty(self):
        df = make_enriched_df()
        cols = make_cols()
        cols["msisdn"] = None
        result = build_cohort_analysis(df, cols)
        assert result.empty


class TestNPSWaterfall:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_nps_waterfall(df, cols)
        assert isinstance(result, pd.DataFrame)
        assert "Month" in result.columns

    def test_has_nps_score(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_nps_waterfall(df, cols)
        assert "NPS_Score" in result.columns

    def test_has_promoters_and_detractors(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_nps_waterfall(df, cols)
        if not result.empty:
            assert "Promoters" in result.columns
            assert "Detractors" in result.columns


class TestTopBottom:
    def test_returns_dataframe(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_top_bottom(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_has_rank_type_column(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_top_bottom(df, cols)
        if not result.empty:
            assert "Rank_Type" in result.columns

    def test_has_best_and_worst(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_top_bottom(df, cols)
        if not result.empty:
            rank_types = result["Rank_Type"].unique()
            assert any("Best" in rt for rt in rank_types)
            assert any("Worst" in rt for rt in rank_types)


class TestDuplicateSurveys:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_duplicate_surveys(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_no_msisdn_returns_empty(self):
        df = make_enriched_df()
        cols = make_cols()
        cols["msisdn"] = None
        result = build_duplicate_surveys(df, cols)
        assert result.empty


class TestMappingCoverage:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_mapping_coverage(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_missing_lev1_returns_empty_with_cols(self):
        df = make_enriched_df()
        cols = make_cols()
        cols["lev1"] = None
        result = build_mapping_coverage(df, cols)
        # Should return empty or empty-ish
        assert isinstance(result, pd.DataFrame)


class TestPatterns:
    def test_hour_pattern(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_hour_pattern(df, cols)
        assert isinstance(result, pd.DataFrame)
        assert "Hour" in result.columns
        assert len(result) == 24

    def test_dow_pattern(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_dow_pattern(df, cols)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 7

    def test_hour_has_detractor_rate(self):
        df = make_enriched_df()
        cols = make_cols()
        result = build_hour_pattern(df, cols)
        assert "Detractor_Rate_%" in result.columns

    def test_dow_missing_nps_returns_empty(self):
        df = make_enriched_df()
        cols = make_cols()
        cols["nps"] = None
        result = build_dow_pattern(df, cols)
        assert result.empty


class TestAgentRanking:
    def test_returns_dataframe(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_agent_ranking(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_no_agent_id_returns_empty(self):
        df = make_enriched_df()
        cols = make_cols()
        cols["agent_id"] = None
        result = build_agent_ranking(df, cols)
        assert result.empty

    def test_has_percentile_column(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_agent_ranking(df, cols)
        if not result.empty:
            assert "Percentile" in result.columns


class TestAgentPeerBenchmark:
    def test_returns_dataframe(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_agent_peer_benchmark(df, cols)
        assert isinstance(result, pd.DataFrame)

    def test_has_vs_queue_avg(self):
        df = make_enriched_df(300)
        cols = make_cols()
        result = build_agent_peer_benchmark(df, cols)
        if not result.empty:
            assert "vs_Queue_Avg" in result.columns

    def test_no_agent_id_returns_empty(self):
        df = make_enriched_df(300)
        cols = make_cols()
        cols["agent_id"] = None
        result = build_agent_peer_benchmark(df, cols)
        assert result.empty


class TestBreakdownBy:
    def test_returns_dataframe(self):
        df = make_enriched_df()
        cols = make_cols()
        result = breakdown_by(df, "CALL_TYPE", cols)
        assert isinstance(result, pd.DataFrame)

    def test_missing_column_returns_empty(self):
        df = make_enriched_df()
        cols = make_cols()
        result = breakdown_by(df, "NON_EXISTENT_COL", cols)
        assert result.empty

    def test_has_detractor_rate(self):
        df = make_enriched_df()
        cols = make_cols()
        result = breakdown_by(df, "CALL_TYPE", cols)
        if not result.empty:
            assert "Detractor_Rate_%" in result.columns


class TestForecast:
    def test_forecast_series_length(self):
        s = pd.Series(range(30), dtype=float)
        f, lo, hi = forecast_series(s, horizon=7, label="test")
        assert len(f) == 7
        assert len(lo) == 7
        assert len(hi) == 7

    def test_forecast_series_non_negative(self):
        s = pd.Series(range(20), dtype=float)
        f, lo, hi = forecast_series(s, horizon=10, label="test")
        assert (f >= 0).all()
        assert (lo >= 0).all()

    def test_forecast_too_short_series(self):
        s = pd.Series([5.0, 6.0], dtype=float)
        f, lo, hi = forecast_series(s, horizon=5, label="short")
        assert len(f) == 5

    def test_build_forecast_returns_dataframe(self):
        df = make_enriched_df(300)
        cols = make_cols()
        daily = build_daily_trend(df, cols)
        result = build_forecast(daily, horizon=14)
        assert isinstance(result, pd.DataFrame)
        if not result.empty:
            assert "Date" in result.columns

    def test_build_forecast_horizon_length(self):
        df = make_enriched_df(300)
        cols = make_cols()
        daily = build_daily_trend(df, cols)
        result = build_forecast(daily, horizon=14)
        if not result.empty:
            assert len(result) == 14

    def test_build_forecast_empty_daily(self):
        result = build_forecast(pd.DataFrame(), horizon=14)
        assert result.empty


class TestSQLiteIntegration:
    """Test load_tnps_df with actual SQLite."""

    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        df = make_test_df(100)
        df.drop(columns=["Month", "Week", "Hour", "DayOfWeek", "NPS_Category", "Date"],
                inplace=True)
        df.to_sql("survey", self.conn, if_exists="replace", index=False)
        # Also create _roma_sources table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS _roma_sources
            (name TEXT, kind TEXT, origin_file TEXT, rows INTEGER, added_at TEXT)
        """)
        self.conn.execute(
            "INSERT INTO _roma_sources VALUES ('survey','table','test.xlsx',100,datetime('now'))"
        )
        self.conn.commit()

    def test_load_tnps_df_returns_df_and_cols(self):
        df, cols = load_tnps_df(self.conn)
        assert isinstance(df, pd.DataFrame)
        assert isinstance(cols, dict)

    def test_load_tnps_df_detects_nps(self):
        df, cols = load_tnps_df(self.conn)
        assert cols["nps"] is not None

    def test_load_tnps_df_has_enriched_columns(self):
        df, cols = load_tnps_df(self.conn)
        if not df.empty:
            assert "NPS_Category" in df.columns

    def test_load_tnps_df_empty_db_returns_empty(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _roma_sources
            (name TEXT, kind TEXT, origin_file TEXT, rows INTEGER, added_at TEXT)
        """)
        conn.commit()
        df, cols = load_tnps_df(conn)
        assert df.empty
        conn.close()

    def teardown_method(self):
        self.conn.close()
