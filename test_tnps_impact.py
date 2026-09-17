"""Tests for tnps_impact_analysis.

Weighted toward the firing rules, because those decide whether a record should
have existed at all, and toward the baseline fixes, because those changed which
days get flagged.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import tnps_impact_analysis as T
from tnps_impact_analysis import Config, FiringRules, InputError, Notes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fire(msisdn: str, when: str, score: int | None = None, **extra) -> dict:
    """One fired record. `when` is 'YYYY-MM-DD HH:MM'."""
    row = {
        "MSISDN": msisdn,
        "OUTBOUND_IVR_DT": pd.Timestamp(when),
        "Q1_ANSWER: TNPS": score,
        "SURVEY_COMPLETION_FLAG": "Y" if score is not None else "N",
        "SHORT_CODE": "SC1",
        "AGENT_QUEUE": "Consumer Care",
        "SR NUMBER": "123456789012",
        "INTERACTION_ID": f"I{abs(hash((msisdn, when))) % 10**8:08d}",
    }
    row.update(extra)
    return row


def prepared(rows: list[dict], cfg: Config | None = None) -> pd.DataFrame:
    """Run rows through prepare() + the compliance engine."""
    cfg = cfg or Config()
    notes = Notes()
    queue_map = pd.DataFrame([{"Agent Queue": "Consumer Care",
                               **{c: "X" for c in cfg.columns.map_levels}}])
    sc_map = pd.DataFrame([{"SHORT_CODE": "SC1", "Topic / Reason": "Billing",
                            "Category": "Billing", "Owner": "Finance"}])
    df = T.prepare(pd.DataFrame(rows), queue_map, sc_map, cfg, notes)
    return T.check_firing_compliance(df, cfg, notes)


# ---------------------------------------------------------------------------
# Rule 3 - firing window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("time_str,expected", [
    ("09:59", False),   # before the window opens
    ("10:00", True),    # exactly at the open - allowed
    ("14:30", True),
    ("21:00", True),    # exactly at the close - allowed by default
    ("21:01", False),   # one minute past
    ("23:30", False),
    ("03:00", False),
])
def test_firing_window(time_str, expected):
    out = prepared([fire("0100000001", f"2026-02-02 {time_str}", 9)])
    assert bool(out["Fire_In_Window"].iat[0]) is expected
    assert bool(out[f"V: {T.RULE_WINDOW}"].iat[0]) is (not expected)


def test_window_end_can_be_exclusive():
    cfg = Config(firing=FiringRules(window_end_inclusive=False))
    out = prepared([fire("0100000001", "2026-02-02 21:00", 9)], cfg)
    assert bool(out["Fire_In_Window"].iat[0]) is False


def test_window_is_configurable():
    cfg = Config(firing=FiringRules(window_start="08:00", window_end="17:00"))
    out = prepared([fire("0100000001", "2026-02-02 08:30", 9),
                    fire("0100000002", "2026-02-02 20:00", 9)], cfg)
    assert out["Fire_In_Window"].tolist() == [True, False]


def test_allowed_weekdays():
    # 2026-02-07 is a Saturday, 2026-02-09 a Monday.
    cfg = Config(firing=FiringRules(allowed_weekdays=(0, 1, 2, 3, 4)))
    out = prepared([fire("0100000001", "2026-02-07 12:00", 9),
                    fire("0100000002", "2026-02-09 12:00", 9)], cfg)
    assert out[f"V: {T.RULE_WEEKDAY}"].tolist() == [1, 0]


# ---------------------------------------------------------------------------
# Rule 1 - cooldown after a response
# ---------------------------------------------------------------------------


def test_cooldown_breached_when_resurveyed_too_soon():
    rows = [fire("0100000001", "2026-02-01 11:00", 9),      # answered
            fire("0100000001", "2026-02-04 11:00")]          # 3 days later
    out = prepared(rows).sort_values("Fire_TS")
    assert out["Cooldown_Violation"].tolist() == [False, True]
    assert out["Days_Since_Response"].iat[1] == pytest.approx(3.0)


def test_cooldown_respected_after_the_window_passes():
    rows = [fire("0100000001", "2026-02-01 11:00", 9),
            fire("0100000001", "2026-02-07 11:00")]          # 6 days later
    out = prepared(rows).sort_values("Fire_TS")
    assert out["Cooldown_Violation"].tolist() == [False, False]


def test_cooldown_boundary_is_exactly_the_configured_days():
    rows = [fire("0100000001", "2026-02-01 11:00", 9),
            fire("0100000001", "2026-02-06 11:00")]          # exactly 5 days
    out = prepared(rows).sort_values("Fire_TS")
    # "within 5 days" is strictly less than 5 - at exactly 5 the customer is free.
    assert out["Cooldown_Violation"].tolist() == [False, False]


def test_cooldown_only_counts_answers_not_attempts():
    """An unanswered fire starts no cooldown - only a RESPONSE does."""
    rows = [fire("0100000001", "2026-02-01 11:00"),          # no answer
            fire("0100000001", "2026-02-02 11:00")]
    out = prepared(rows).sort_values("Fire_TS")
    assert out["Cooldown_Violation"].tolist() == [False, False]


def test_cooldown_is_per_subscriber():
    rows = [fire("0100000001", "2026-02-01 11:00", 9),
            fire("0100000002", "2026-02-02 11:00")]          # different customer
    out = prepared(rows)
    assert out["Cooldown_Violation"].sum() == 0


def test_cooldown_days_are_configurable():
    cfg = Config(firing=FiringRules(response_cooldown_days=10))
    rows = [fire("0100000001", "2026-02-01 11:00", 9),
            fire("0100000001", "2026-02-07 11:00")]
    out = prepared(rows, cfg).sort_values("Fire_TS")
    assert out["Cooldown_Violation"].tolist() == [False, True]


# ---------------------------------------------------------------------------
# Rule 2 - trial cap
# ---------------------------------------------------------------------------


def test_three_trials_allowed_fourth_is_not():
    rows = [fire("0100000001", f"2026-02-0{d} 11:00") for d in (1, 1, 2, 2)]
    rows[1]["OUTBOUND_IVR_DT"] = pd.Timestamp("2026-02-01 17:00")
    rows[3]["OUTBOUND_IVR_DT"] = pd.Timestamp("2026-02-02 17:00")
    out = prepared(rows).sort_values("Fire_TS")
    assert out["Trial_Index"].tolist() == [1, 2, 3, 4]
    assert out["Trial_Violation"].tolist() == [False, False, False, True]


def test_trial_counter_resets_after_an_answer():
    rows = [fire("0100000001", "2026-02-01 11:00"),
            fire("0100000001", "2026-02-01 17:00", 9),       # answered - cycle ends
            fire("0100000001", "2026-02-20 11:00"),          # new cycle
            fire("0100000001", "2026-02-20 17:00")]
    out = prepared(rows).sort_values("Fire_TS")
    assert out["Trial_Index"].tolist() == [1, 2, 1, 2]
    assert out["Trial_Violation"].sum() == 0


def test_a_long_gap_starts_a_new_cycle():
    rows = [fire("0100000001", "2026-02-01 11:00"),
            fire("0100000001", "2026-02-01 18:00"),
            fire("0100000001", "2026-02-02 10:00"),
            fire("0100000001", "2026-02-20 10:00")]          # weeks later
    out = prepared(rows).sort_values("Fire_TS")
    assert out["Trial_Index"].tolist() == [1, 2, 3, 1]
    assert out["Trial_Violation"].sum() == 0


def test_gap_rule_can_be_switched_off():
    cfg = Config(firing=FiringRules(trial_cycle_gap_hours=None))
    rows = [fire("0100000001", "2026-02-01 11:00"),
            fire("0100000001", "2026-02-01 18:00"),
            fire("0100000001", "2026-02-02 10:00"),
            fire("0100000001", "2026-02-20 10:00")]
    out = prepared(rows, cfg).sort_values("Fire_TS")
    assert out["Trial_Index"].tolist() == [1, 2, 3, 4]
    assert out["Trial_Violation"].tolist() == [False, False, False, True]


def test_trial_cycle_can_be_keyed_on_a_column():
    cfg = Config(firing=FiringRules(trial_cycle_key="SR NUMBER",
                                    trial_cycle_gap_hours=None))
    rows = [fire("0100000001", "2026-02-01 11:00", **{"SR NUMBER": "111111111111"}),
            fire("0100000001", "2026-02-01 15:00", **{"SR NUMBER": "111111111111"}),
            fire("0100000001", "2026-02-01 18:00", **{"SR NUMBER": "222222222222"})]
    out = prepared(rows, cfg).sort_values("Fire_TS")
    assert out["Trial_Index"].tolist() == [1, 2, 1]


def test_trial_cap_is_configurable():
    cfg = Config(firing=FiringRules(max_trials_per_cycle=2))
    rows = [fire("0100000001", "2026-02-01 11:00"),
            fire("0100000001", "2026-02-01 15:00"),
            fire("0100000001", "2026-02-01 19:00")]
    out = prepared(rows, cfg).sort_values("Fire_TS")
    assert out["Trial_Violation"].tolist() == [False, False, True]


# ---------------------------------------------------------------------------
# Subscriber identity
# ---------------------------------------------------------------------------


def test_msisdn_spellings_collapse_to_one_customer():
    rows = [fire("01001234567", "2026-02-01 11:00", 9),
            fire("201001234567", "2026-02-03 11:00"),
            fire("+20 100 123 4567", "2026-02-04 11:00")]
    out = prepared(rows).sort_values("Fire_TS")
    assert out["Subscriber"].nunique() == 1
    # All three are the same person, so the re-surveys breach the cooldown.
    assert out["Cooldown_Violation"].tolist() == [False, True, True]


def test_without_a_subscriber_column_only_the_window_rule_runs():
    rows = [{k: v for k, v in fire("0100000001", "2026-02-01 23:00", 9).items()
             if k != "MSISDN"}]
    out = prepared(rows)
    assert out.attrs["subscriber_rules_evaluated"] is False
    assert out[f"V: {T.RULE_WINDOW}"].iat[0] == 1        # still checked
    assert out[f"V: {T.RULE_COOLDOWN}"].iat[0] == 0      # cannot be checked
    summary = T.compliance_summary(out, Config())
    assert summary.loc[summary["Rule"] == T.RULE_COOLDOWN, "Evaluated"].iat[0] == \
        "not evaluated"


# ---------------------------------------------------------------------------
# Compliance reporting
# ---------------------------------------------------------------------------


def test_violations_are_reported_not_dropped_by_default():
    rows = [fire("0100000001", "2026-02-01 23:00", 0),   # out of window, detractor
            fire("0100000002", "2026-02-01 11:00", 10)]
    cfg = Config()
    df = prepared(rows, cfg)
    assert df["Is Answered"].sum() == 2
    base = df[df["Is Answered"] == 1]
    assert len(base) == 2, "default keeps every answered record in the base"


def test_excluding_non_compliant_changes_the_base_only_when_asked():
    rows = [fire("0100000001", "2026-02-01 23:00", 0),
            fire("0100000002", "2026-02-01 11:00", 10)]
    df = prepared(rows, Config(firing=FiringRules(exclude_non_compliant_from_base=True)))
    kept = df[(df["Is Answered"] == 1) & df["Is_Compliant"]]
    assert len(kept) == 1
    assert T.tnps_of(kept) == pytest.approx(100.0)


def test_compliance_summary_totals_and_sensitivity():
    rows = [fire("0100000001", "2026-02-01 23:00", 0),
            fire("0100000002", "2026-02-01 11:00", 10),
            fire("0100000003", "2026-02-01 12:00", 9)]
    df = prepared(rows)
    summary = T.compliance_summary(df, Config())
    window_row = summary[summary["Rule"] == T.RULE_WINDOW].iloc[0]
    assert window_row["Records"] == 1
    assert window_row["% of fires"] == pytest.approx(1 / 3)
    passed = summary[summary["Rule"] == "ALL RULES PASSED"].iloc[0]
    assert passed["Records"] == 2
    assert passed["tNPS of these"] == pytest.approx(100.0)


def test_over_surveyed_lists_the_worst_offenders():
    rows = [fire("0100000001", "2026-02-01 11:00", 9)]
    rows += [fire("0100000001", f"2026-02-0{d} 11:00") for d in (2, 3, 4)]
    df = prepared(rows)
    worst = T.over_surveyed_subscribers(df, Config())
    assert len(worst) == 1
    assert worst["Violations"].iat[0] >= 3
    assert worst["Min_Gap_Days"].iat[0] == pytest.approx(1.0)


def test_trial_effectiveness_orders_attempts_and_flags_over_cap():
    rows = []
    for i in range(20):
        msisdn = f"010000{i:05d}"
        rows.append(fire(msisdn, "2026-02-01 11:00", 9 if i < 10 else None))
        if i >= 10:
            rows.append(fire(msisdn, "2026-02-01 15:00", 9 if i < 15 else None))
    df = prepared(rows)
    eff = T.trial_effectiveness(df, Config())
    assert list(eff["Attempt"].astype(str))[:2] == ["1", "2"]
    assert eff.loc[eff["Attempt"].astype(str) == "1", "Response Rate"].iat[0] == \
        pytest.approx(0.5)


def test_hourly_profile_marks_the_window():
    rows = [fire("0100000001", "2026-02-01 08:00"),
            fire("0100000002", "2026-02-01 12:00", 9)]
    hourly = T.hourly_firing_profile(prepared(rows), Config())
    assert len(hourly) == 24
    assert bool(hourly.loc[hourly["Fire Hour"] == 8, "In Window"].iat[0]) is False
    assert bool(hourly.loc[hourly["Fire Hour"] == 12, "In Window"].iat[0]) is True


# ---------------------------------------------------------------------------
# Baselines - the fixes that changed which days get flagged
# ---------------------------------------------------------------------------


def weekly_series(n_weeks: int = 8, weekend_factor: float = 0.45,
                  noise: float = 0.02, seed: int = 11) -> pd.DataFrame:
    """Stable weekly pattern with ordinary day-to-day noise, no anomalies."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-05", periods=n_weeks * 7, freq="D")  # a Monday
    values = [1000 * (weekend_factor if d.weekday() >= 5 else 1.0)
              * (1 + rng.normal(0, noise)) for d in dates]
    return pd.DataFrame({"date": dates, "value": values})


def test_trailing_baseline_flags_every_weekend_on_clean_data():
    """Regression: a 7-day mean is weekday-neutral, so it marks weekends low."""
    s = weekly_series()
    base = T.trailing_baseline(s["value"], 7, 3, "mean")
    below = (s["value"] < base) & base.notna()
    weekend = s["date"].dt.weekday >= 5
    assert below[weekend].mean() > 0.9, "the old baseline flags nearly every weekend"
    assert below[~weekend].mean() < 0.15, "while weekdays almost never trip it"
    assert below[weekend].mean() - below[~weekend].mean() > 0.75, (
        "the flag is measuring the calendar, not the data")


def test_same_weekday_baseline_flags_nothing_on_clean_data():
    s = weekly_series()
    base = T.same_weekday_baseline(s["value"], s["date"], 4, 2, "mean")
    below = (s["value"] < base) & base.notna()
    weekend = s["date"].dt.weekday >= 5
    # Ordinary noise still puts a day either side of its own baseline, but the
    # weekend is no longer singled out - which is the whole point.
    assert abs(below[weekend].mean() - below[~weekend].mean()) < 0.25


def test_same_weekday_baseline_still_sees_a_real_outage():
    s = weekly_series()
    hit = 30                                    # a plain weekday
    assert s["date"].iat[hit].weekday() < 5
    s.loc[hit, "value"] *= 0.5                  # a 50% collapse
    base = T.same_weekday_baseline(s["value"], s["date"], 4, 2, "mean")
    sd = T.same_weekday_baseline(s["value"], s["date"], 4, 2, "std")
    z = (s["value"].iat[hit] - base.iat[hit]) / sd.iat[hit]
    assert z < -3, "a 50% weekday collapse must be unmistakable"


def test_zero_record_days_never_enter_a_baseline():
    """Regression: reindexing a missing day to 0 depressed the baseline for a week."""
    s = weekly_series()
    volume = s["value"].copy()
    s.loc[20, "value"] = 0          # feed outage
    volume.loc[20] = 0
    poisoned = T.trailing_baseline(s["value"], 7, 3, "mean")
    masked = T.trailing_baseline(s["value"], 7, 3, "mean", volume=volume)
    assert poisoned.iloc[21:27].lt(masked.iloc[21:27]).all(), \
        "the zero must not drag the following week's baseline down"


def test_t_critical_is_stricter_than_the_normal_value_at_small_n():
    crit = T.baseline_t_critical(pd.Series([2, 3, 5, 10, 60]), 0.05)
    assert crit.iat[0] > 12          # t(1) is enormous
    assert crit.iat[1] > 4           # t(2) = 4.30, not 1.96
    assert crit.iat[4] == pytest.approx(2.0, abs=0.05)
    assert pd.isna(T.baseline_t_critical(pd.Series([1]), 0.05).iat[0])


def test_weekday_factors_describe_the_weekly_shape():
    s = weekly_series(weekend_factor=0.5)
    factors = T.weekday_factors(s["value"], s["date"])
    assert factors[5] == pytest.approx(factors[6], rel=0.05)   # Sat ~ Sun
    assert factors[0] > factors[5] * 1.5                        # Mon well above both
    assert factors.mean() == pytest.approx(1.0, rel=0.05)       # normalised


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def test_nps_variance_is_exact():
    # All promoters: every score is +1, so the variance is genuinely zero.
    assert T.nps_variance(1.0, 0.0) == pytest.approx(0.0)
    # Half promoters, half detractors: scores +1/-1, mean 0, variance 1.
    assert T.nps_variance(0.5, 0.5) == pytest.approx(1.0)


def test_variance_floor_keeps_a_degenerate_group_honest():
    """Regression: 100% promoters gave SE = 0 and a zero-width interval."""
    se_small = T.nps_se(1.0, 0.0, 10, floor_n=50)
    se_large = T.nps_se(1.0, 0.0, 500, floor_n=50)
    assert se_small > 0, "a 10-person perfect score is not certainty"
    assert se_large > 0
    assert se_small > se_large


def test_fdr_controls_the_false_discovery_rate():
    # 100 tests: 95 nulls spread from 0.01 upward (so several sneak under a raw
    # 0.05 cut) plus 5 genuine effects.
    p = np.concatenate([np.linspace(0.01, 1.0, 95), [1e-6] * 5])
    q = T.fdr_qvalues(p)
    assert (q[-5:] < 0.05).all(), "real effects survive"
    raw_hits = int((p < 0.05).sum())
    fdr_hits = int((q < 0.05).sum())
    assert fdr_hits < raw_hits, "FDR must discard the borderline nulls"
    assert np.isnan(T.fdr_qvalues([np.nan, np.nan])).all()


def test_fdr_qvalues_are_monotone_in_p():
    p = np.array([0.001, 0.01, 0.02, 0.2, 0.9])
    q = T.fdr_qvalues(p)
    assert (np.diff(q) >= -1e-12).all()


def test_sig_tag_prefers_the_q_value():
    assert "significant" in T.sig_tag(0.001, 0.01)
    assert T.sig_tag(0.02, 0.30) == "~ noise after FDR"
    assert T.sig_tag(np.nan) == "n/a"


def test_mde_shrinks_as_the_sample_grows():
    small = T.tnps_mde(30, 0.5, 0.3)
    large = T.tnps_mde(3000, 0.5, 0.3)
    assert small > large > 0
    assert np.isnan(T.tnps_mde(0))


def test_wilson_interval_behaves_at_the_edges():
    lo, hi = T.wilson_ci(0, 10)
    assert lo == pytest.approx(0.0, abs=1e-9) and 0 < hi < 0.35
    lo, hi = T.wilson_ci(np.array([5]), np.array([0]))
    assert np.isnan(lo).all()


# ---------------------------------------------------------------------------
# Impact engine
# ---------------------------------------------------------------------------


def impact_frame() -> pd.DataFrame:
    rows = []
    for code, (n_prom, n_det) in {"A": (60, 10), "B": (20, 40), "C": (30, 20)}.items():
        rows += [fire(f"01{i:09d}", "2026-02-02 11:00", 10, SHORT_CODE=code)
                 for i in range(n_prom)]
        rows += [fire(f"01{i + 500:09d}", "2026-02-02 12:00", 2, SHORT_CODE=code)
                 for i in range(n_det)]
    df = prepared(rows)
    return df[df["Is Answered"] == 1]


def test_impact_column_closes_on_zero():
    table, total = T.tnps_impact_table(impact_frame(), "SHORT_CODE", Config())
    assert table["Impact (pts)"].sum() == pytest.approx(0.0, abs=1e-9)
    assert table["Weight"].sum() == pytest.approx(1.0)
    assert (table["Weight"] * table["tNPS"]).sum() == pytest.approx(total)


def test_loo_matches_the_algebraic_identity():
    table, _ = T.tnps_impact_table(impact_frame(), "SHORT_CODE", Config())
    expected = -table["Impact (pts)"] / (1 - table["Weight"])
    assert np.allclose(table["LOO (pts)"], expected)


def test_two_way_pockets_find_what_one_way_views_dilute():
    rows = []
    for seg in ("Consumer Care", "Enterprise Sales"):
        for code in ("A", "B"):
            # Only A-inside-Enterprise is bad; both one-way views average it away.
            bad = (seg == "Enterprise Sales" and code == "A")
            for i in range(60):
                score = 1 if (bad and i < 50) else 10
                rows.append(fire(f"01{seg[:3]}{code}{i:06d}", "2026-02-02 11:00",
                                 score, SHORT_CODE=code, AGENT_QUEUE=seg))
    cfg = Config()
    notes = Notes()
    queue_map = pd.DataFrame([
        {"Agent Queue": q, "Q Mapping Lev 1": "x", "Q Mapping Lev 2 Combined": "x",
         "Q Mapping Lev 3 New PF Seg": q, "Q Mapping Lev 4 Seg": q, "Site": "x"}
        for q in ("Consumer Care", "Enterprise Sales")])
    sc_map = pd.DataFrame([{"SHORT_CODE": c, "Topic / Reason": "T",
                            "Category": "C", "Owner": "O"} for c in ("A", "B")])
    df = T.prepare(pd.DataFrame(rows), queue_map, sc_map, cfg, notes)
    df = T.check_firing_compliance(df, cfg, notes)
    pockets = T.two_way_pockets(df[df["Is Answered"] == 1], "SC Label",
                               cfg.columns.segment_col, cfg)
    worst = pockets.iloc[0]
    assert worst[cfg.columns.segment_col] == "Enterprise Sales"
    assert worst["SC Label"].startswith("A")


# ---------------------------------------------------------------------------
# MoM bridge and changepoints
# ---------------------------------------------------------------------------


def bridge_frame() -> pd.DataFrame:
    rows = []
    # January: code A dominates and performs well.
    for i in range(100):
        rows.append(fire(f"01A{i:08d}", "2026-01-10 11:00", 10, SHORT_CODE="A"))
    for i in range(20):
        rows.append(fire(f"01B{i:08d}", "2026-01-10 12:00", 0, SHORT_CODE="B"))
    # February: the weak code B takes a much bigger share (a MIX shift), and
    # code A also slips a little (a RATE shift).
    for i in range(60):
        rows.append(fire(f"01C{i:08d}", "2026-02-10 11:00",
                         10 if i < 55 else 0, SHORT_CODE="A"))
    for i in range(60):
        rows.append(fire(f"01D{i:08d}", "2026-02-10 12:00", 0, SHORT_CODE="B"))
    df = prepared(rows)
    return df[df["Is Answered"] == 1]


def test_bridge_closes_exactly_with_no_residual():
    bridge = T.mom_bridge(bridge_frame(), "SHORT_CODE", Config())
    change = bridge.attrs["tnps_now"] - bridge.attrs["tnps_prev"]
    assert bridge["Total (pts)"].sum() == pytest.approx(change, abs=1e-9)
    assert (bridge["Mix (pts)"].sum() + bridge["Rate (pts)"].sum()) == \
        pytest.approx(change, abs=1e-9)


def test_bridge_separates_mix_from_rate():
    bridge = T.mom_bridge(bridge_frame(), "SHORT_CODE", Config())
    mix, rate = bridge["Mix (pts)"].sum(), bridge["Rate (pts)"].sum()
    assert mix < 0 and rate < 0
    assert abs(mix) > abs(rate), "the volume shift is the larger cause here"


def test_bridge_needs_two_months():
    single = prepared([fire("0100000001", "2026-02-01 11:00", 9)])
    assert T.mom_bridge(single, "SHORT_CODE", Config()).empty


def test_cusum_finds_a_level_shift_that_daily_tests_miss():
    dates = pd.date_range("2026-01-05", periods=56, freq="D")
    rng = np.random.default_rng(3)
    values = np.concatenate([rng.normal(30, 4, 28), rng.normal(12, 4, 28)])
    daily = pd.DataFrame({"Survey Date": dates, "tNPS": values,
                          "Answered": 100, "Has Records": 1})
    points = T.cusum_changepoints(daily, T.Analysis())
    assert not points.empty
    first = points.iloc[0]
    assert first["Direction"] == "DOWN"
    assert first["Shift (pts)"] < -10
    assert pd.Timestamp(first["Survey Date"]) >= dates[27]


def test_cusum_stays_quiet_on_a_flat_series():
    dates = pd.date_range("2026-01-05", periods=56, freq="D")
    rng = np.random.default_rng(5)
    daily = pd.DataFrame({"Survey Date": dates, "tNPS": rng.normal(30, 4, 56),
                          "Answered": 100, "Has Records": 1})
    assert T.cusum_changepoints(daily, T.Analysis()).empty


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_time_parsing():
    assert T._parse_time("10:00") == dt.time(10, 0)
    assert T._parse_time("9") == dt.time(9, 0)
    assert T._parse_time("21:30:15") == dt.time(21, 30, 15)
    with pytest.raises(ValueError):
        T._parse_time("25:00")
    with pytest.raises(ValueError):
        T._parse_time("lunchtime")


def test_config_round_trips_through_json(tmp_path):
    cfg = Config(firing=FiringRules(response_cooldown_days=7, max_trials_per_cycle=2,
                                    window_start="09:00", window_end="20:00"))
    path = tmp_path / "rules.json"
    path.write_text(cfg.to_json())
    loaded = Config.from_file(path)
    assert loaded.firing.response_cooldown_days == 7
    assert loaded.firing.max_trials_per_cycle == 2
    assert loaded.firing.start_time == dt.time(9, 0)
    # JSON has no tuples; they must come back as tuples, not lists.
    assert isinstance(loaded.firing.subscriber_col_candidates, tuple)


def test_cli_overrides_beat_the_config_file(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(Config(firing=FiringRules(response_cooldown_days=7)).to_json())
    args = T.build_parser().parse_args(
        ["--config", str(path), "--cooldown-days", "3", "--max-trials", "5",
         "--fire-window", "08:00-22:00", "--baseline", "trailing", "--no-fdr"])
    cfg = T.config_from_args(args)
    assert cfg.firing.response_cooldown_days == 3
    assert cfg.firing.max_trials_per_cycle == 5
    assert cfg.firing.start_time == dt.time(8, 0)
    assert cfg.analysis.baseline_mode == "trailing"
    assert cfg.analysis.fdr_correction is False


@pytest.mark.parametrize("argv", [
    ["--fire-window", "10:00"],          # no dash
    ["--max-trials", "0"],
    ["--roll", "1"],
    ["--alpha", "1.5"],
])
def test_bad_cli_values_are_rejected(argv):
    with pytest.raises(InputError):
        T.config_from_args(T.build_parser().parse_args(argv))


def test_dump_config_writes_valid_json(tmp_path):
    out = tmp_path / "dump.json"
    assert T.main(["--dump-config", str(out)]) == 0
    assert json.loads(out.read_text())["firing"]["max_trials_per_cycle"] == 3


# ---------------------------------------------------------------------------
# Preparation details
# ---------------------------------------------------------------------------


def test_non_numeric_sr_number_is_blank_not_activity():
    """Regression: "ABC-XYZ" stripped to 0 digits and classified as Activity."""
    rows = [fire("0100000001", "2026-02-01 11:00", 9, **{"SR NUMBER": "ABC-XYZ"}),
            fire("0100000002", "2026-02-01 11:00", 9, **{"SR NUMBER": "12345678"}),
            fire("0100000003", "2026-02-01 11:00", 9, **{"SR NUMBER": "123456789012"})]
    out = prepared(rows)
    assert out["Request Type"].tolist() == ["(blank)", "Activity", "SR"]


def test_out_of_range_scores_are_excluded_everywhere():
    """Regression: a 99 was excluded from the base but still read as a Promoter."""
    out = prepared([fire("0100000001", "2026-02-01 11:00", 99),
                    fire("0100000002", "2026-02-01 11:00", 10)])
    bad = out[out["Out Of Range"] == 1].iloc[0]
    assert bad["Is Answered"] == 0
    assert bad["Is Promoter"] == 0
    assert bad["NPS Category"] == "No Response"


def test_iso_week_carries_its_year():
    out = prepared([fire("0100000001", "2026-12-31 11:00", 9)])
    assert out["ISO Week"].iat[0].startswith("2026-W") or \
        out["ISO Week"].iat[0].startswith("2027-W")
    assert "-W" in out["ISO Week"].iat[0]


def test_duplicate_records_are_detected():
    row = fire("0100000001", "2026-02-01 11:00", 9)
    out = prepared([row, dict(row)])
    assert out.attrs["duplicate_records"] == 1


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Run the real pipeline over a small generated month."""
    folder = tmp_path_factory.mktemp("run")
    rows = []
    for day in pd.date_range("2026-01-05", "2026-02-20", freq="D"):
        n = 30 if day.weekday() < 5 else 14
        for i in range(n):
            msisdn = f"01{day.dayofyear:03d}{i:06d}"
            answered = i % 3 == 0
            score = (9 if i % 5 else 3) if answered else None
            rows.append(fire(msisdn, f"{day:%Y-%m-%d} {10 + i % 10}:15", score,
                             SHORT_CODE=f"SC{i % 4 + 1}"))
    # A handful of deliberate violations.
    rows.append(fire("01005000000", "2026-02-10 23:30", 3))
    data = folder / "survey.xlsx"
    pd.DataFrame(rows).to_excel(data, index=False)
    qm = folder / "queue.xlsx"
    pd.DataFrame([{"Agent Queue": "Consumer Care", "Q Mapping Lev 1": "Care",
                   "Q Mapping Lev 2 Combined": "CARE",
                   "Q Mapping Lev 3 New PF Seg": "Consumer",
                   "Q Mapping Lev 4 Seg": "Consumer - CARE",
                   "Site": "Cairo"}]).to_excel(qm, index=False)
    sm = folder / "codes.xlsx"
    pd.DataFrame([{"SHORT_CODE": f"SC{i}", "Topic / Reason": f"Topic {i}",
                   "Category": "Cat", "Owner": "Owner"}
                  for i in range(1, 5)]).to_excel(sm, index=False)
    return data, qm, sm, folder


def test_pipeline_runs_and_totals_reconcile(built):
    data, qm, sm, _ = built
    res = T.analyse([data], qm, sm, Config())
    t = res.totals
    assert t["fires"] == len(res.df)
    assert t["answered"] == int(res.base["Is Answered"].sum())
    assert t["compliant"] + (t["fires"] - t["compliant"]) == t["fires"]
    assert res.compliance_daily["Fires"].sum() == t["fires"]
    assert not res.daily.empty and not res.sc_impact.empty


def test_workbook_has_every_expected_sheet(built):
    data, qm, sm, folder = built
    res = T.analyse([data], qm, sm, Config())
    out = T.build_workbook(res, folder / "report.xlsx", dt.datetime(2026, 3, 1, 9, 0))
    names = pd.ExcelFile(out).sheet_names
    for expected in ("Contents", "Executive Summary", "Firing Compliance",
                     "Compliance by Day", "Daily Survey Volume", "Daily tNPS",
                     "Daily Detractors", "Short Codes by Detractors",
                     "SC tNPS Impact", "Monthly", "Fact (Answered)",
                     "Run Info & DQ", "Glossary"):
        assert expected in names, f"{expected} missing from {names}"


def test_fact_sheet_keeps_dates_as_dates(built):
    data, qm, sm, folder = built
    res = T.analyse([data], qm, sm, Config())
    out = T.build_workbook(res, folder / "report2.xlsx", dt.datetime(2026, 3, 1, 9, 0))
    fact = pd.read_excel(out, sheet_name="Fact (Answered)")
    assert pd.api.types.is_datetime64_any_dtype(fact["Survey Date"]), \
        "a text date cannot be grouped by month in a PivotTable"


def test_cli_end_to_end(built, capsys):
    data, qm, sm, folder = built
    code = T.main(["--data", str(data), "--queue-map", str(qm), "--sc-map", str(sm),
                   "--out", str(folder / "cli.xlsx"), "--cooldown-days", "5",
                   "--max-trials", "3", "--fire-window", "10:00-21:00"])
    assert code == 0
    printed = capsys.readouterr().out
    assert "Firing compliance" in printed
    assert (folder / "cli.xlsx").exists()


def test_dry_run_writes_nothing(built, capsys):
    data, qm, sm, folder = built
    assert T.main(["--data", str(data), "--queue-map", str(qm), "--sc-map", str(sm),
                   "--out", str(folder / "none.xlsx"), "--dry-run"]) == 0
    assert not (folder / "none.xlsx").exists()
    assert "Dry run" in capsys.readouterr().out


def test_missing_input_reports_cleanly(capsys):
    assert T.main(["--data", "nope.xlsx", "--queue-map", "nope.xlsx"]) == 1
    assert "Not found" in capsys.readouterr().err
