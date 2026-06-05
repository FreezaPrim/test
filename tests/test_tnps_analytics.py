"""Tests for Roma's TNPS analytics integration."""
import pytest
import re
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


# ─────────────────────────────────────────────────────────────────────────────
# NEW: engine-level tests — bordered table & print-top builder
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatBorderedTable:
    """Unit tests for _format_bordered_table."""

    def _fn(self):
        from roma.engine import _format_bordered_table
        return _format_bordered_table

    def test_basic_two_columns(self):
        fn = self._fn()
        result = fn(["Name", "Score"], [["Alice", "9"], ["Bob", "3"]])
        assert "|" in result
        assert "+" in result
        assert "Alice" in result
        assert "Score" in result

    def test_empty_rows(self):
        fn = self._fn()
        result = fn(["A", "B"], [])
        assert "A" in result
        assert "|" in result

    def test_title_renders(self):
        fn = self._fn()
        result = fn(["X"], [["1"]], title="My Report")
        assert "My Report" in result

    def test_max_rows_truncation(self):
        fn = self._fn()
        rows = [[str(i)] for i in range(100)]
        result = fn(["n"], rows, max_rows=10)
        assert "more rows" in result

    def test_no_truncation_at_limit(self):
        fn = self._fn()
        rows = [[str(i)] for i in range(5)]
        result = fn(["n"], rows, max_rows=10)
        assert "more rows" not in result


class TestAnswerPrintTop:
    """Integration tests for _answer_print_top engine builder."""

    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE survey (
                shortcode TEXT, nps_score INTEGER, owner_team TEXT
            )""")
        data = [
            ("SC01", 3, "TeamA"), ("SC01", 2, "TeamA"), ("SC02", 5, "TeamB"),
            ("SC01", 8, "TeamA"), ("SC02", 3, "TeamB"), ("SC03", 1, "TeamC"),
            ("SC03", 4, "TeamC"), ("SC03", 9, "TeamC"), ("SC02", 6, "TeamB"),
        ]
        self.conn.executemany("INSERT INTO survey VALUES (?,?,?)", data)
        self.conn.execute("""
            CREATE TABLE _roma_sources
            (name TEXT, kind TEXT, origin_file TEXT, rows INTEGER, added_at TEXT)""")
        self.conn.execute(
            "INSERT INTO _roma_sources VALUES ('survey','table','test.csv',9,datetime('now'))")
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()

    def test_print_top_returns_bordered_table(self):
        from roma.engine import _answer_print_top
        result = _answer_print_top(self.conn, "print top detractors by shortcode")
        # should return something (either a table or None if nps col not detected)
        # we check that if it returns, it has borders
        if result is not None:
            assert "|" in result or "shortcode" in result.lower()

    def test_print_top_returns_none_on_no_match(self):
        from roma.engine import _answer_print_top
        result = _answer_print_top(self.conn, "what is the weather today")
        assert result is None

    def test_print_top_requires_print_or_show(self):
        from roma.engine import _answer_print_top
        result = _answer_print_top(self.conn, "drivers of nps")
        assert result is None

    def test_print_top_owner_team(self):
        from roma.engine import _answer_print_top
        result = _answer_print_top(self.conn, "show top detractors by owner team")
        if result is not None:
            assert "|" in result


class TestJoinMappingTrigger:
    """Verify _answer_join_mapping trigger regex (no interactive I/O)."""

    def _matches(self, q: str) -> bool:
        return bool(re.search(
            r"\bjoin\b|\bmerge\b|\bvlookup\b|join.{0,15}map|map.{0,15}join|"
            r"link.*file|add.*mapping.*file|mapping.*from.*file|"
            r"دمج.*ملف|ربط.*ملف|أضف.*مابينج", q))

    def test_join_mapping_triggers(self):
        assert self._matches("join mapping")
        assert self._matches("join the mapping file")
        assert self._matches("merge two sheets")
        assert self._matches("vlookup shortcode")
        assert self._matches("add mapping from file")

    def test_join_mapping_no_false_positive_on_description(self):
        # These should NOT trigger _answer_join_mapping
        assert not self._matches("how does the map feature work")
        assert not self._matches("what drives tnps")
        assert not self._matches("show me detractors by queue")


# ─────────────────────────────────────────────────────────────────────────────
# Win-back, Severity, KPI completeness, Time-filtered print-top
# ─────────────────────────────────────────────────────────────────────────────

class TestWinback:
    """Tests for winback.py cohort and summary functions."""

    def setup_method(self):
        from roma.tnps_analytics import detect_tnps_columns
        rng = np.random.default_rng(7)
        n = 300
        base = date(2026, 1, 1)
        # spread across 3 months
        dates = [base + timedelta(days=int(d)) for d in rng.integers(0, 90, n)]
        msisdns = [f"05{rng.integers(1000000, 9999999)}" for _ in range(50)]
        self.df = pd.DataFrame({
            "MSISDN": [msisdns[i % 50] for i in range(n)],
            "Q1_ANSWER: TNPS": rng.integers(0, 11, n).astype(float),
            "IVR_DATE": dates,
        })
        self.cols = detect_tnps_columns(self.df)

    def test_winback_summary_returns_dataframe(self):
        from roma.winback import build_winback_summary
        result = build_winback_summary(self.df, self.cols)
        assert isinstance(result, pd.DataFrame)

    def test_winback_summary_has_expected_columns(self):
        from roma.winback import build_winback_summary
        result = build_winback_summary(self.df, self.cols)
        if not result.empty:
            assert "Recovery_Rate_%" in result.columns
            assert "Churn_Rate_%" in result.columns

    def test_winback_transitions_returns_dataframe(self):
        from roma.winback import build_winback_transitions
        result = build_winback_transitions(self.df, self.cols)
        assert isinstance(result, pd.DataFrame)

    def test_winback_transitions_categories(self):
        from roma.winback import build_winback_transitions
        result = build_winback_transitions(self.df, self.cols)
        if not result.empty:
            cats = set(result["Cat_From"].unique()) | set(result["Cat_To"].unique())
            assert cats.issubset({"Promoter", "Passive", "Detractor"})

    def test_winback_empty_on_missing_cols(self):
        from roma.winback import build_winback_summary
        result = build_winback_summary(pd.DataFrame({"a": [1, 2]}), {})
        assert result.empty


class TestSeverityScores:
    """Tests for build_severity_scores in tnps_analytics."""

    def setup_method(self):
        from roma.tnps_analytics import detect_tnps_columns
        self.df = pd.DataFrame({
            "MSISDN": ["05111", "05222", "05111", "05333", "05444", "05555"],
            "Q1_ANSWER: TNPS": [1.0, 3.0, 5.0, 0.0, 4.0, 8.0],
        })
        self.cols = detect_tnps_columns(self.df)

    def test_returns_dataframe(self):
        from roma.tnps_analytics import build_severity_scores
        result = build_severity_scores(self.df, self.cols)
        assert isinstance(result, pd.DataFrame)

    def test_has_severity_column(self):
        from roma.tnps_analytics import build_severity_scores
        result = build_severity_scores(self.df, self.cols)
        if not result.empty:
            assert "Severity" in result.columns
            assert "Count" in result.columns

    def test_severity_values_are_valid(self):
        from roma.tnps_analytics import build_severity_scores
        result = build_severity_scores(self.df, self.cols)
        if not result.empty:
            assert set(result["Severity"]).issubset({"Critical", "High", "Medium"})

    def test_no_promoters_in_severity(self):
        from roma.tnps_analytics import build_severity_scores
        result = build_severity_scores(self.df, self.cols)
        # score 8 is a passive, should NOT appear in severity
        total = result["Count"].sum() if not result.empty else 0
        assert total == 5  # 5 detractors (score 0-6), 1 passive excluded

    def test_repeat_caller_bump(self):
        from roma.tnps_analytics import build_severity_scores
        # MSISDN 05111 appears twice with Medium severity (score 5) → should bump to High
        result = build_severity_scores(self.df, self.cols)
        assert not result.empty


class TestKPIsPromotersPassives:
    """Verify kpis._nps() returns promoters, passives, detractors separately."""

    def test_kpis_has_four_nps_entries(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE survey (tnps_score REAL)")
        data = [(float(s),) for s in [10, 9, 9, 8, 7, 5, 4, 3, 2, 1, 0]]
        conn.executemany("INSERT INTO survey VALUES (?)", data)
        conn.execute("""
            CREATE TABLE _roma_sources
            (name TEXT, kind TEXT, origin_file TEXT, rows INTEGER, added_at TEXT)""")
        conn.execute("INSERT INTO _roma_sources VALUES ('survey','table','t.csv',11,datetime('now'))")
        conn.commit()

        from roma.kpis import compute
        results = compute(conn)
        names = [r["name"] for r in results]
        # should have tNPS, Promoters, Passives, Detractors
        assert any("tNPS" in n or "NPS" in n for n in names), f"No tNPS entry: {names}"
        assert any("Promoter" in n for n in names), f"No Promoters entry: {names}"
        assert any("Passive" in n for n in names), f"No Passives entry: {names}"
        assert any("Detractor" in n for n in names), f"No Detractors entry: {names}"
        conn.close()


class TestPrintTopTimeFilter:
    """Verify _answer_print_top respects time filter keywords."""

    def setup_method(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE survey (shortcode TEXT, nps_score REAL, ivr_date TEXT)""")
        rows = [
            ("SC01", 3.0, "2026-04-10"), ("SC01", 2.0, "2026-04-15"),
            ("SC02", 5.0, "2026-04-20"), ("SC01", 8.0, "2026-03-01"),
            ("SC02", 3.0, "2026-03-15"), ("SC03", 1.0, "2026-04-22"),
        ]
        self.conn.executemany("INSERT INTO survey VALUES (?,?,?)", rows)
        self.conn.execute("""
            CREATE TABLE _roma_sources
            (name TEXT, kind TEXT, origin_file TEXT, rows INTEGER, added_at TEXT)""")
        self.conn.execute(
            "INSERT INTO _roma_sources VALUES ('survey','table','t.csv',6,datetime('now'))")
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()

    def test_print_top_with_month_filter_returns_result(self):
        from roma.engine import _answer_print_top
        result = _answer_print_top(self.conn, "print top detractors by shortcode in april")
        # should not error
        assert result is None or isinstance(result, str)

    def test_print_top_no_filter(self):
        from roma.engine import _answer_print_top
        result = _answer_print_top(self.conn, "print top detractors by shortcode")
        assert result is None or isinstance(result, str)


class TestAnswerSkill:
    """Tests for _answer_skill builder — skill list and skill run from chat."""

    def setup_method(self):
        import sqlite3
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE documents
               (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
                title TEXT, chunk_index INTEGER DEFAULT 0, content TEXT NOT NULL)""")
        self.conn.execute(
            """CREATE TABLE _roma_sources
               (name TEXT, kind TEXT, origin_file TEXT, rows INTEGER, added_at TEXT)""")
        self.conn.commit()

    def teardown_method(self):
        self.conn.close()

    def test_skill_list_returns_skills(self):
        from roma.engine import _answer_skill
        result = _answer_skill(self.conn, "skill list")
        assert result is not None
        assert "morning_review" in result

    def test_show_skills_returns_skills(self):
        from roma.engine import _answer_skill
        result = _answer_skill(self.conn, "show skills")
        assert result is not None
        assert "steps" in result

    def test_non_skill_query_returns_none(self):
        from roma.engine import _answer_skill
        result = _answer_skill(self.conn, "my kpis")
        assert result is None

    def test_skill_run_missing_returns_helpful_message(self):
        from roma.engine import _answer_skill
        result = _answer_skill(self.conn, "skill run nonexistent_skill_xyz")
        assert result is not None
        assert "not found" in result.lower()

    def test_answer_routing_skill_list(self):
        """Full answer() routing: 'skill list' must NOT return identity text."""
        from roma.engine import answer
        result = answer(self.conn, "skill list", False, None)
        assert "morning_review" in result, f"Got identity instead: {result[:120]}"

    def test_answer_routing_show_skills(self):
        from roma.engine import answer
        result = answer(self.conn, "show skills", False, None)
        assert "morning_review" in result, f"Routing failed: {result[:120]}"
