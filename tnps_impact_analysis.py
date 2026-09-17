"""
tNPS Impact Analysis v6 - Firing Compliance / Daily Series / tNPS Impact Engine
==============================================================================
Answers one question chain, end to end:

  0. Was the survey even FIRED CORRECTLY?  (new in v6 - see FiringRules)
  1. How many surveys went out each day, and which days were genuinely abnormal?
  2. How many detractors, over the ANSWERED base (not completed surveys)?
  3. What was tNPS each day, and which days were genuinely abnormal?
  4. Which SHORT_CODEs carry the detractors?
  5. WHAT IS DRIVING tNPS - per day, per month, and versus last month?

Everything that can be tuned lives in the CONFIG section: firing rules, statistical
thresholds, column bindings and chart styling. `--config my.json` overrides any of
it; `--dump-config` writes the current settings out as a starting point.

WHAT CHANGED IN v6
------------------
Firing compliance (new)
  Three dispatch rules are now enforced and reported per record:
    - a customer who RESPONDED is not re-surveyed within N days (default 5)
    - a customer who does NOT respond gets at most N attempts (default 3)
    - surveys are only fired inside the allowed window (default 10:00-21:00)
  Violations are flagged, counted, charted and broken down by day and queue.
  They are NOT dropped from the tNPS base by default - that would silently change
  the headline - but `exclude_non_compliant_from_base` makes the swap in one line,
  and the compliance sheet always shows tNPS both ways.

Baselines (fixed - this changed which days get flagged)
  The old 7-day trailing mean was weekday-NEUTRAL, so every weekend was compared
  against a mostly-weekday baseline. On pure noise that flagged ~94% of weekend
  days and 0% of weekdays. Worse, mixing weekdays into the rolling SD inflated it
  far above real day-to-day noise, so the significance test could not see a real
  weekday outage: a simulated 50% volume collapse scored z = -1.20 and was MISSED.
  Baselines are now same-weekday by default (`baseline_mode = "same_weekday"`).

  A day with zero records used to be reindexed to 0 and fed into the rolling mean
  as if it were a real observation, depressing the baseline ~17% for the next
  seven days and hiding genuine dips. Zero-record days are now shown in the table
  and masked out of the baseline.

  The z-test against a rolling SD used 1.96 with as few as 3 prior points. It now
  uses the t critical value for the actual number of observations behind the SD.

Statistics
  - Benjamini-Hochberg FDR across each family of tests (days, short codes, ...),
    because ~100 short codes at alpha = 0.05 yields ~5 false "significant" labels.
  - A small-sample variance floor: a group that is 100% promoters has an exact
    variance of zero, which produced a zero-width CI and overstated confidence.
  - Minimum detectable effect per day, so "not significant" can be read as
    "this day was too small to tell" rather than "this day was fine".

New analyses
  - Month-over-month bridge decomposing the tNPS change into MIX (volume moved
    between groups) and RATE (groups genuinely changed), which is the question a
    review actually asks and which a single MoM delta cannot answer.
  - CUSUM changepoint detection, because day-vs-baseline cannot see a level shift:
    once tNPS steps down the trailing baseline follows it within a week and every
    day afterwards reads "normal".
  - Two-way pockets (short code x segment), where a problem confined to one code
    inside one segment is diluted in both one-way views.

Charts
  Rebuilt to the e& design system and the data-viz rules: no dual-axis (the old
  Pareto plotted counts and cumulative share on two y-scales, which invents a
  correlation), red reserved for negative meaning only, hairline solid gridlines,
  thin marks, and direct labels only where they carry the point.

CLI
  python tnps_impact_analysis.py --data jan.xlsx feb.xlsx
         --queue-map QueueMapping.xlsx [--sc-map ShortCode_Mapping.xlsx]
         [--out report.xlsx] [--config rules.json] [--dump-config rules.json]
         [--cooldown-days 5] [--max-trials 3] [--fire-window 10:00-21:00]
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Final, Sequence

import numpy as np
import pandas as pd

SCRIPT_VERSION: Final[str] = "v6.0"


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG - every rule, threshold and column binding lives here
# ══════════════════════════════════════════════════════════════════════════════


def _norm_key(value: object) -> str:
    """Lowercase, letters and digits only - for tolerant name matching."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _parse_time(text: object) -> dt.time:
    """Accept '10:00', '10', '10:00:00', or a time object."""
    if isinstance(text, dt.time):
        return text
    s = str(text).strip()
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?(?::(\d{2}))?$", s)
    if not match:
        raise ValueError(f"Could not read a time from {text!r} - use HH:MM, e.g. 10:00")
    h, m, sec = int(match.group(1)), int(match.group(2) or 0), int(match.group(3) or 0)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"{text!r} is not a valid clock time")
    return dt.time(h, m, sec)


@dataclass(frozen=True)
class FiringRules:
    """When a survey is ALLOWED to be fired. Every record is checked against these.

    These are dispatch-behaviour rules, not analysis rules: they describe how the
    IVR campaign is supposed to behave. A violation means the campaign misfired,
    which is a finding in its own right and also a reason to distrust the affected
    responses (an over-surveyed customer answers differently).
    """

    # --- Rule 1: cooldown after a response ----------------------------------
    # A customer who ANSWERED must not be surveyed again for this many days.
    response_cooldown_days: float = 5.0

    # --- Rule 2: attempt cap when there is no response -----------------------
    # If the customer does not answer, the campaign may try this many times.
    max_trials_per_cycle: int = 3
    # What ends a trial cycle. A response always ends one. Beyond that, either a
    # gap longer than this many hours starts a fresh cycle (the usual IVR retry
    # pattern), or set trial_cycle_key to a column (e.g. "SR NUMBER") to make the
    # cycle explicit. Set the gap to None to count attempts over the whole period.
    trial_cycle_gap_hours: float | None = 72.0
    trial_cycle_key: str | None = None

    # --- Rule 3: allowed firing window --------------------------------------
    # Local clock. Both ends inclusive to the minute: a 21:00:00 fire is allowed,
    # 21:00:30 is not. Set window_end_inclusive = False for a strict [start, end).
    window_start: str = "10:00"
    window_end: str = "21:00"
    window_end_inclusive: bool = True
    # Days of the week the campaign may fire on. 0 = Monday ... 6 = Sunday.
    # None means every day is allowed.
    allowed_weekdays: tuple[int, ...] | None = None

    # --- How violations feed the rest of the report --------------------------
    # False (default): violations are reported but every record still counts
    # toward tNPS, so the headline does not move silently. True: the base drops
    # non-compliant records. The compliance sheet reports tNPS BOTH ways either
    # way, so the sensitivity is always visible.
    exclude_non_compliant_from_base: bool = False

    # --- Subscriber identity -------------------------------------------------
    # Rules 1 and 2 need to know who was called. Columns are matched tolerantly
    # (case, spaces, underscores ignored). Without one, only Rule 3 is checked.
    subscriber_col_candidates: tuple[str, ...] = (
        "MSISDN", "MOBILE_NUMBER", "MOBILE", "PHONE", "PHONE_NUMBER", "A_NUMBER",
        "ANI", "SUBSCRIBER_ID", "SUBSCRIBER", "CUSTOMER_ID", "BAN",
        "CONTACT_NUMBER", "CALLED_NUMBER", "DIALED_NUMBER", "B_NUMBER",
    )
    # Egyptian mobile numbers arrive as 01XXXXXXXXX, 201XXXXXXXXX or +201XXXXXXXXX.
    # Keeping the last N digits makes those three spellings one customer. Set to
    # None to match on the raw value.
    subscriber_normalise_last_n: int | None = 10

    @property
    def start_time(self) -> dt.time:
        return _parse_time(self.window_start)

    @property
    def end_time(self) -> dt.time:
        return _parse_time(self.window_end)

    def window_label(self) -> str:
        edge = "inclusive" if self.window_end_inclusive else "exclusive"
        return f"{self.window_start}-{self.window_end} ({edge} end)"

    def describe(self) -> list[tuple[str, str]]:
        """Rule rows for the Config / Compliance sheets."""
        cycle = (f"a response, or a gap over {self.trial_cycle_gap_hours}h"
                 if self.trial_cycle_gap_hours else "a response only")
        if self.trial_cycle_key:
            cycle = f"a response, or a new {self.trial_cycle_key}"
        days = ("every day" if not self.allowed_weekdays else ", ".join(
            ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]
            for d in self.allowed_weekdays))
        return [
            ("Rule 1 - cooldown after a response",
             f"no re-survey within {self.response_cooldown_days:g} days of an answer"),
            ("Rule 2 - attempt cap without a response",
             f"at most {self.max_trials_per_cycle} trials per cycle"),
            ("Rule 2 - what starts a new cycle", cycle),
            ("Rule 3 - allowed firing window", self.window_label()),
            ("Rule 3 - allowed days", days),
            ("Non-compliant records in the tNPS base",
             "EXCLUDED" if self.exclude_non_compliant_from_base else
             "included (reported both ways on the Firing Compliance sheet)"),
        ]


@dataclass(frozen=True)
class Analysis:
    """Statistical settings for the daily series and the impact engine."""

    # Baseline. "same_weekday" compares Monday with Mondays - the only mode that
    # survives a weekly volume cycle. "trailing" is the old calendar-day window,
    # kept for short files with under ~4 weeks of history.
    baseline_mode: str = "same_weekday"          # "same_weekday" | "trailing"
    roll_window: int = 7                          # days, trailing mode
    same_weekday_window: int = 4                  # same-weekday observations
    roll_min_periods: int = 3                     # trailing mode
    same_weekday_min_periods: int = 2

    alpha: float = 0.05
    fdr_correction: bool = True                   # Benjamini-Hochberg per family
    min_n_day: int = 20                           # answered/day before a daily test
    min_n_group: int = 30                         # answered before a group impact
    variance_floor_n: int = 50                    # add-1/2 smoothing below this n

    top_n_impact: int = 15
    top_n_per_day: int = 3
    pareto_target: float = 0.80

    cusum_k_sigma: float = 0.5                    # slack, in SD units
    cusum_h_sigma: float = 4.0                    # decision threshold, in SD units
    pocket_min_n: int = 40                        # min answered for a two-way pocket
    pocket_top_n: int = 20

    def z_alpha(self) -> float:
        from scipy import stats
        return float(stats.norm.ppf(1 - self.alpha / 2))


@dataclass(frozen=True)
class Columns:
    """Column bindings. Rename here when the export headers change."""

    date: str = "OUTBOUND_IVR_DT"
    score: str = "Q1_ANSWER: TNPS"
    completion_flag: str = "SURVEY_COMPLETION_FLAG"
    short_code: str = "SHORT_CODE"
    agent_queue: str = "AGENT_QUEUE"
    interaction_id: str = "INTERACTION_ID"
    sr_number_candidates: tuple[str, ...] = (
        "SR NUMBER", "SR_NUMBER", "SR No", "SR_NO", "SRNUMBER", "SR Number",
        "SR#", "SR NO.",
    )
    # Agent-queue mapping sheet
    map_queue_key: str = "Agent Queue"
    map_levels: tuple[str, ...] = (
        "Q Mapping Lev 1", "Q Mapping Lev 2 Combined",
        "Q Mapping Lev 3 New PF Seg", "Q Mapping Lev 4 Seg", "Site",
    )
    segment_col: str = "Q Mapping Lev 3 New PF Seg"
    # SHORT_CODE mapping sheet
    map_sc_key: str = "SHORT_CODE"
    map_sc_cols: tuple[str, ...] = ("Topic / Reason", "Category", "Owner")


@dataclass(frozen=True)
class RequestTypeRule:
    """SR NUMBER -> SR / Activity.

    'length' compares the DIGIT COUNT to the threshold, which is what separates
    the two ID formats. 'value' compares the numeric value and will label an
    entire file "SR", because an identifier is not a quantity.
    """

    rule: str = "length"          # "length" | "value"
    threshold: int = 10
    label_hi: str = "SR"          # strictly above the threshold
    label_lo: str = "Activity"    # at or below
    label_na: str = "(blank)"     # empty / unparseable
    equal_is: str = "lo"          # exactly == threshold


@dataclass(frozen=True)
class Config:
    """Everything the run needs. `--config file.json` overrides any field."""

    firing: FiringRules = field(default_factory=FiringRules)
    analysis: Analysis = field(default_factory=Analysis)
    columns: Columns = field(default_factory=Columns)
    request_type: RequestTypeRule = field(default_factory=RequestTypeRule)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    @classmethod
    def from_file(cls, path: Path) -> "Config":
        raw = json.loads(Path(path).read_text())
        return cls(
            firing=FiringRules(**_coerce(raw.get("firing", {}), FiringRules)),
            analysis=Analysis(**_coerce(raw.get("analysis", {}), Analysis)),
            columns=Columns(**_coerce(raw.get("columns", {}), Columns)),
            request_type=RequestTypeRule(**_coerce(raw.get("request_type", {}),
                                                   RequestTypeRule)),
        )


def _coerce(raw: dict[str, Any], klass: type) -> dict[str, Any]:
    """Keep known keys and restore tuple fields that JSON turned into lists."""
    fields = {f.name: f for f in dataclasses.fields(klass)}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in fields:
            continue
        if isinstance(value, list):
            value = tuple(value)
        out[key] = value
    return out


CONFIG: Final[Config] = Config()

# ── DEFAULTS for a double-click / Run-button launch ───────────────────────────
BASE_FOLDER = r"C:\Users\Mohammad Ayman\Desktop\Survey"
if not os.path.isdir(BASE_FOLDER):
    BASE_FOLDER = os.getcwd()
DEFAULT_SC_MAPPING_PATH = os.path.join(BASE_FOLDER, "ShortCode_Mapping.xlsx")


# ══════════════════════════════════════════════════════════════════════════════
# e& BRAND TOKENS
# Red means NEGATIVE in this workbook (detractors, violations, below target), so
# brand-accent duty sits with graphite and gold - the design system's
# "semantic-safe" palette. Nothing here is an ad-hoc hex.
# ══════════════════════════════════════════════════════════════════════════════
RED, RED_DARK, RED_DEEP, RED_TINT = "E00800", "B00600", "7A0400", "FFE5E3"
GOLD, GOLD_DARK, GOLD_TINT = "B4892B", "8F6B1E", "F5EBD3"
INK, GRAPHITE, SLATE = "17181C", "3A3D45", "5C5F66"
MUTED, LINE, SURFACE, WHITE = "8A8D93", "E6E7EA", "F7F7F8", "FFFFFF"
POS, WARN = "1E9E62", "C9971F"
SAND = "D4B36A"
FONT = "Arial"

# Categorical identity, in fixed order, never cycled. Red is absent on purpose.
CATEGORICAL: Final[tuple[str, ...]] = (GRAPHITE, GOLD, MUTED, SAND, SLATE)
SEQUENTIAL: Final[tuple[str, ...]] = (RED_TINT, "FF6B63", RED, RED_DEEP)

FMT_INT, FMT_DEC1, FMT_DEC2 = "#,##0", "#,##0.0", "#,##0.00"
FMT_PCT, FMT_PCT2 = "0.0%", "0.00%"
FMT_VAR, FMT_VARP = "+0.0;[Red]-0.0;0.0", "+0.0%;[Red]-0.0%;0.0%"
FMT_DATE, FMT_DATETIME = "dd-mmm-yy", "dd-mmm-yy hh:mm"
FMT_P = "0.000"


# ══════════════════════════════════════════════════════════════════════════════
# ERRORS AND RUN NOTES
# ══════════════════════════════════════════════════════════════════════════════


class InputError(Exception):
    """A problem with the inputs that the operator has to resolve.

    Raised by the loaders instead of calling sys.exit, so the module stays
    importable from a notebook or a test.
    """


class Notes:
    """Collects what the loaders did quietly, for the Data Quality sheet.

    Header renames, auto-detected tabs and skipped lookups are reasonable
    defaults and invisible the month a number looks wrong.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def add(self, category: str, message: str) -> None:
        self.entries.append((category, message))
        print(f"      . {category}: {message}")

    def rows(self) -> list[dict[str, Any]]:
        return [{"Check": f"Load note - {c}", "Value": "", "Detail": m}
                for c, m in self.entries]

    def __len__(self) -> int:
        return len(self.entries)


# ══════════════════════════════════════════════════════════════════════════════
# STATISTICS
# ══════════════════════════════════════════════════════════════════════════════


def wilson_ci(k, n, z: float = 1.96):
    """Wilson score interval - reliable at small n, unlike the normal approx."""
    k = np.asarray(k, dtype=float)
    n = np.asarray(n, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        safe_n = np.where(n > 0, n, np.nan)
        p = k / safe_n
        denom = 1 + z ** 2 / safe_n
        centre = (p + z ** 2 / (2 * safe_n)) / denom
        half = (z / denom) * np.sqrt(p * (1 - p) / safe_n + z ** 2 / (4 * safe_n ** 2))
    return centre - half, centre + half


def two_prop_z(x1, n1, x2, n2):
    """Two-proportion z-test -> (z, two-sided p)."""
    from scipy import stats
    try:
        x1, n1, x2, n2 = float(x1), float(n1), float(x2), float(n2)
    except (TypeError, ValueError):
        return np.nan, np.nan
    if not np.isfinite(n1) or not np.isfinite(n2) or min(n1, n2) <= 0:
        return np.nan, np.nan
    p1, p2 = x1 / n1, x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    se = np.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return np.nan, np.nan
    z = (p1 - p2) / se
    return z, 2 * (1 - stats.norm.cdf(abs(z)))


def nps_variance(p_prom, p_det):
    """Exact variance of the NPS score variable s in {+1, 0, -1}.

    Var(s) = E[s^2] - (E[s])^2 = (p_prom + p_det) - (p_prom - p_det)^2
    tNPS is a MEAN, not a proportion, so no percentage approximation is needed.
    """
    return (p_prom + p_det) - (p_prom - p_det) ** 2


def nps_se(p_prom, p_det, n, floor_n: int = 50):
    """Standard error of tNPS, on the 0-100 point scale, with a small-n floor.

    A group that is 100% promoters has an exact variance of ZERO, which yields a
    zero-width confidence interval and overstates certainty at any n. Below
    floor_n the proportions are nudged by add-1/2 smoothing so a degenerate group
    still carries honest uncertainty.
    """
    n = np.asarray(n, dtype=float)
    p_prom = np.asarray(p_prom, dtype=float)
    p_det = np.asarray(p_det, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        small = (n > 0) & (n < floor_n)
        k_prom = p_prom * n
        k_det = p_det * n
        p_prom_s = np.where(small, (k_prom + 0.5) / (n + 1.5), p_prom)
        p_det_s = np.where(small, (k_det + 0.5) / (n + 1.5), p_det)
        v = nps_variance(p_prom_s, p_det_s)
        # Even at large n a degenerate group deserves a non-zero SE.
        v = np.where((v <= 0) & (n > 0), nps_variance((k_prom + 0.5) / (n + 1.5),
                                                      (k_det + 0.5) / (n + 1.5)), v)
        return 100.0 * np.sqrt(np.where(n > 0, v / n, np.nan))


def nps_diff_z(nps1, se1, nps2, se2):
    """Two-sample z on a difference of tNPS values (both on the 0-100 scale)."""
    from scipy import stats
    with np.errstate(divide="ignore", invalid="ignore"):
        se = np.sqrt(np.asarray(se1, float) ** 2 + np.asarray(se2, float) ** 2)
        z = np.where(se > 0,
                     (np.asarray(nps1, float) - np.asarray(nps2, float)) / se, np.nan)
    return z, 2 * (1 - stats.norm.cdf(np.abs(z)))


def fdr_qvalues(p_values: Sequence[float]) -> np.ndarray:
    """Benjamini-Hochberg q-values, NaNs preserved.

    A family of ~100 short-code tests at alpha = 0.05 yields about five
    "significant" labels from noise alone. The q-value is the share of
    discoveries at that cut expected to be false.
    """
    p = np.asarray(p_values, dtype=float)
    q = np.full(p.shape, np.nan)
    mask = np.isfinite(p)
    if not mask.any():
        return q
    vals = p[mask]
    order = np.argsort(vals)
    ranked = vals[order]
    m = len(ranked)
    adj = ranked * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]     # enforce monotonicity
    out = np.empty(m)
    out[order] = np.clip(adj, 0, 1)
    q[mask] = out
    return q


def sig_tag(p, q=None, alpha: float = 0.05) -> str:
    """Significance label. When an FDR q-value is supplied, IT decides."""
    if p is None or not np.isfinite(p):
        return "n/a"
    if q is not None and np.isfinite(q):
        if q < alpha:
            return f"significant (q={q:.3f})"
        return "~ noise after FDR" if p < alpha else "~ noise"
    if p < 0.01:
        return "significant (p<0.01)"
    if p < alpha:
        return f"significant (p={p:.3f})"
    return "~ noise"


def tnps_mde(n, p_prom=0.5, p_det=0.3, alpha: float = 0.05, power: float = 0.80):
    """Smallest tNPS gap this sample size could detect, in points.

    Turns "not significant" into "this day was too small to tell". Uses the
    observed mix so the answer reflects the real variance, not a worst case.
    """
    from scipy import stats
    n = np.asarray(n, dtype=float)
    z_a = stats.norm.ppf(1 - alpha / 2)
    z_b = stats.norm.ppf(power)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = nps_variance(np.asarray(p_prom, float), np.asarray(p_det, float))
        v = np.where(v > 0, v, 0.25)
        return np.where(n > 0, (z_a + z_b) * 100.0 * np.sqrt(v / n), np.nan)


# ══════════════════════════════════════════════════════════════════════════════
# BASELINES
#
# Every baseline EXCLUDES the day being tested (.shift(1)). Including it pulls
# the baseline toward the day and blunts the flag.
#
# "same_weekday" is the default because a 7-day mean is weekday-NEUTRAL: it
# contains one of each weekday, so a Saturday is measured against a mostly
# weekday baseline and is flagged below average every single week. On simulated
# pure noise around a stable weekly pattern that flagged 94% of weekend days and
# 0% of weekdays. The same mixing inflates the rolling SD well above real
# day-to-day noise, so the significance test loses the power to see a genuine
# outage - a simulated 50% weekday collapse scored z = -1.20 and was missed.
# ══════════════════════════════════════════════════════════════════════════════


def _mask_empty(series: pd.Series, volume: pd.Series | None) -> pd.Series:
    """Blank out days with no records so they never enter a baseline.

    A zero-record day is an absence of data, not an observation of zero. Feeding
    it into a rolling mean depresses the baseline for a full window and hides the
    dips that follow it.
    """
    if volume is None:
        return series
    return series.where(volume.fillna(0) > 0)


def trailing_baseline(series: pd.Series, window: int, min_periods: int,
                      how: str = "mean", volume: pd.Series | None = None) -> pd.Series:
    """Calendar-window baseline over the prior `window` days."""
    s = _mask_empty(series, volume).shift(1).rolling(window, min_periods=min_periods)
    return {"mean": s.mean, "std": lambda: s.std(ddof=1), "sum": s.sum,
            "count": s.count}[how]()


def same_weekday_baseline(series: pd.Series, dates: pd.Series, window: int,
                          min_periods: int, how: str = "mean",
                          volume: pd.Series | None = None) -> pd.Series:
    """Baseline built from the same weekday only - Mondays against Mondays."""
    masked = _mask_empty(series, volume)
    grouped = masked.groupby(pd.Series(dates).dt.weekday.values)

    def roll(s: pd.Series) -> pd.Series:
        r = s.shift(1).rolling(window, min_periods=min_periods)
        return {"mean": r.mean, "std": lambda: r.std(ddof=1), "sum": r.sum,
                "count": r.count}[how]()

    return grouped.transform(roll)


def baseline(series: pd.Series, dates: pd.Series, cfg: Analysis, how: str = "mean",
             volume: pd.Series | None = None) -> pd.Series:
    """Baseline for the configured mode."""
    if cfg.baseline_mode == "same_weekday":
        return same_weekday_baseline(series, dates, cfg.same_weekday_window,
                                     cfg.same_weekday_min_periods, how, volume)
    return trailing_baseline(series, cfg.roll_window, cfg.roll_min_periods, how, volume)


def baseline_t_critical(counts: pd.Series, alpha: float = 0.05) -> pd.Series:
    """Two-sided t critical value for the number of observations behind an SD.

    A rolling SD from three points needs t(2) = 4.30 at 5%, not 1.96. Using the
    normal value there over-flags exactly when the baseline is least trustworthy.
    """
    from scipy import stats
    n = pd.to_numeric(counts, errors="coerce")
    out = pd.Series(np.nan, index=n.index, dtype="float64")
    valid = n > 1
    out[valid] = stats.t.ppf(1 - alpha / 2, n[valid] - 1)
    return out


def weekday_factors(series: pd.Series, dates: pd.Series) -> pd.Series:
    """Multiplicative weekday seasonality, normalised to mean 1.

    Reported so a reader can see the weekly shape the baseline is correcting for.
    """
    frame = pd.DataFrame({"v": pd.to_numeric(series, errors="coerce"),
                          "wd": pd.Series(dates).dt.weekday.values})
    frame = frame[frame["v"] > 0]
    if frame.empty:
        return pd.Series(dtype="float64")
    means = frame.groupby("wd")["v"].mean()
    overall = frame["v"].mean()
    return (means / overall) if overall else means


def _nz(series: pd.Series) -> pd.Series:
    """Coerce to float and turn zeros into NaN, for safe division."""
    numeric = pd.to_numeric(series, errors="coerce").astype("float64")
    return numeric.where(numeric != 0)


def _share(series: pd.Series) -> pd.Series:
    """Each row's share of the column total; NaN when the total is zero."""
    numeric = pd.to_numeric(series, errors="coerce").astype("float64")
    total = float(numeric.sum())
    return numeric / total if total else pd.Series(np.nan, index=series.index)


def as_text(series: pd.Series) -> pd.Series:
    """Strip to text without inventing the string 'nan'.

    astype(str) turns a missing value into "nan" on pandas 2 and keeps it missing
    on pandas 3; neither behaviour is relied on here.
    """
    return series.where(series.isna(), series.astype(str).str.strip())


# ══════════════════════════════════════════════════════════════════════════════
# LOADING
# ══════════════════════════════════════════════════════════════════════════════


def _normalise(name: object) -> str:
    return _norm_key(name)


def align_headers(frame: pd.DataFrame, expected: Sequence[str],
                  notes: Notes | None = None, source: str = "") -> pd.DataFrame:
    """Rename columns that match an expected header apart from spelling.

    An exact match is never touched; only case, spacing, underscore and
    punctuation differences are corrected, and every rename is recorded. Two
    columns that normalise to the same target would collide into one name, so
    only the first is renamed and the clash is reported.
    """
    present = set(frame.columns)
    wanted = {_normalise(n): n for n in expected if n not in present}
    if not wanted:
        return frame
    renames: dict[Any, str] = {}
    claimed: set[str] = set()
    for col in frame.columns:
        target = wanted.get(_normalise(col))
        if target is None:
            continue
        if target in claimed:
            if notes:
                notes.add("header clash",
                          f"{source}'{col}' also matches '{target}'; left as is")
            continue
        claimed.add(target)
        renames[col] = target
    if renames and notes:
        for old, new in renames.items():
            notes.add("header renamed", f"{source}'{old}' -> '{new}'")
    return frame.rename(columns=renames) if renames else frame


def find_column(frame: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    """First candidate present, matched tolerantly on case and punctuation."""
    for cand in candidates:
        if cand in frame.columns:
            return cand
    lookup = {_normalise(c): c for c in frame.columns}
    for cand in candidates:
        hit = lookup.get(_normalise(cand))
        if hit:
            return hit
    return None


def _read_table(path: Path, sheet: str | int = 0) -> pd.DataFrame:
    if Path(path).suffix.lower() in {".csv", ".txt"}:
        return pd.read_csv(path, dtype=object)
    return pd.read_excel(path, sheet_name=sheet, dtype=object)


def read_mapping_workbook(path: Path, key: str, expected: Sequence[str],
                          notes: Notes, label: str) -> pd.DataFrame:
    """Read a mapping file, finding the right tab rather than assuming the first.

    Mapping workbooks routinely open on an instructions or lookup page, so every
    sheet is scanned and the first one carrying the key column wins. Headers are
    realigned per sheet before the check, so a tab spelling it 'Agent_Queue'
    still matches.
    """
    path = Path(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        frame = pd.read_csv(path, dtype=object)
        frame.columns = [str(c).strip() for c in frame.columns]
        frame = align_headers(frame, expected, notes, f"{label}: ")
        if key not in frame.columns:
            raise InputError(f"'{key}' is not in {path.name}.\n"
                             f"Columns found: {list(frame.columns)[:20]}")
        return frame

    book = pd.ExcelFile(path)
    named = [s for s in book.sheet_names if "mapping" in str(s).lower()]
    candidates = named + [s for s in book.sheet_names if s not in named]
    inspected: list[str] = []
    for sheet in candidates:
        trial = book.parse(sheet, dtype=object)
        trial.columns = [str(c).strip() for c in trial.columns]
        trial = align_headers(trial, expected, notes, f"{label} tab '{sheet}': ")
        inspected.append(f"  {sheet}: {', '.join(map(str, trial.columns[:8])) or '(empty)'}")
        if key in trial.columns:
            notes.add(f"{label} tab", f"read from '{sheet}' in {path.name}")
            return trial
    raise InputError(
        f"Could not find '{key}' in any sheet of {path.name}.\n"
        "Sheets inspected and their first columns:\n" + "\n".join(inspected)
        + f"\n\nRename the column to '{key}', or point the config at the right "
        "column name.")


def load_survey_files(paths: Sequence[Path], cfg: Config,
                      notes: Notes) -> tuple[pd.DataFrame, list[tuple[str, int]]]:
    """Load and concatenate the survey exports."""
    frames, loaded = [], []
    for path in paths:
        name = Path(path).name
        try:
            frame = _read_table(Path(path))
        except Exception as exc:
            notes.add("file skipped", f"{name}: {exc}")
            continue
        frame.columns = [str(c).strip() for c in frame.columns]
        frame["Source.Name"] = name
        frames.append(frame)
        loaded.append((name, len(frame)))
        print(f"    + {name} ({len(frame):,} rows)")
    if not frames:
        raise InputError("No survey data could be read from the files supplied.")

    df = pd.concat(frames, ignore_index=True)
    df.columns = [str(c).strip() for c in df.columns]
    expected = [cfg.columns.date, cfg.columns.score, cfg.columns.completion_flag,
                cfg.columns.short_code, cfg.columns.agent_queue,
                cfg.columns.interaction_id]
    df = align_headers(df, expected, notes, "export: ")

    required = [cfg.columns.date, cfg.columns.score, cfg.columns.short_code,
                cfg.columns.agent_queue]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise InputError(
            f"Required column(s) missing: {missing}\n"
            f"Columns found: {list(df.columns)[:25]}")
    return df, loaded


def load_queue_mapping(path: Path | None, cfg: Config, notes: Notes) -> pd.DataFrame:
    """Agent queue -> segment levels."""
    if path is None:
        return pd.DataFrame(columns=[cfg.columns.map_queue_key,
                                     *cfg.columns.map_levels])
    if not Path(path).exists():
        raise InputError(f"Agent Queue mapping not found: {path}")
    mapping = read_mapping_workbook(
        Path(path), cfg.columns.map_queue_key,
        [cfg.columns.map_queue_key, *cfg.columns.map_levels], notes, "queue map")
    mapping[cfg.columns.map_queue_key] = as_text(
        mapping[cfg.columns.map_queue_key]).fillna("")
    before = len(mapping)
    mapping = mapping.drop_duplicates(subset=[cfg.columns.map_queue_key], keep="first")
    if before != len(mapping):
        notes.add("queue map deduped", f"{before - len(mapping)} duplicate key row(s)")
    for col in cfg.columns.map_levels:
        if col not in mapping.columns:
            mapping[col] = np.nan
            notes.add("queue map column missing", f"'{col}' -> Unmapped")
    print(f"    + Queue mapping loaded ({len(mapping):,} rows)")
    return mapping


def normalize_short_code(val: Any) -> str:
    """One definition of a SHORT_CODE, shared with the detractor tooling."""
    if pd.isna(val) or val is None:
        return ""
    s = str(val).strip().replace("\xa0", "").replace("\t", "")
    if s.endswith(".0") and s[:-2].replace("-", "").isdigit():
        s = s[:-2]
    if s.startswith("'"):
        s = s[1:]
    return s


def load_short_code_mapping(path: Path | None, codes: Sequence[str], cfg: Config,
                            notes: Notes) -> pd.DataFrame:
    """SHORT_CODE -> topic / category / owner, generating a template if absent."""
    key, extra = cfg.columns.map_sc_key, list(cfg.columns.map_sc_cols)
    if path and Path(path).exists():
        sc = read_mapping_workbook(Path(path), key, [key, *extra], notes,
                                   "short-code map")
        print(f"    + SHORT_CODE mapping loaded ({len(sc):,} rows)")
    else:
        target = Path(path) if path else Path(DEFAULT_SC_MAPPING_PATH)
        sc = pd.DataFrame([{key: c, "Topic / Reason": "", "Category": "",
                            "Owner": "", "Notes": ""} for c in codes if c])
        try:
            sc.to_excel(target, index=False)
            notes.add("short-code template written", str(target))
        except Exception as exc:
            notes.add("short-code template not written", f"{target}: {exc}")
    sc[key] = sc[key].apply(normalize_short_code)
    sc = sc.drop_duplicates(subset=[key], keep="first")
    for col in extra:
        if col not in sc.columns:
            sc[col] = ""
    return sc


def file_digest(path: Path | None) -> str:
    """Short SHA-256, so a workbook can be traced back to its input."""
    if path is None or not Path(path).exists():
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:12]


# ══════════════════════════════════════════════════════════════════════════════
# PREPARE
# ══════════════════════════════════════════════════════════════════════════════


def normalise_msisdn(series: pd.Series, last_n: int | None) -> pd.Series:
    """One customer, one key, whatever spelling the export used.

    01001234567, 201001234567 and +20 100 123 4567 are the same subscriber;
    keeping the last N digits collapses them.
    """
    text = as_text(series).fillna("")
    digits = text.str.replace(r"\D", "", regex=True)
    # Excel float artefact: 1.00123e+11 style values arrive already expanded by
    # the digit strip, but a trailing ".0" would leave a stray zero.
    trimmed = text.str.endswith(".0")
    digits = digits.where(~trimmed, digits.str[:-1])
    if last_n:
        digits = digits.str[-last_n:]
    return digits.where(digits.str.len() > 0)


def prepare(df: pd.DataFrame, queue_map: pd.DataFrame, sc_map: pd.DataFrame,
            cfg: Config, notes: Notes) -> pd.DataFrame:
    """Clean, classify and join. One row in = one outbound IVR record = one FIRE."""
    cols = cfg.columns
    df = df.copy()

    # ── Timestamps. The full timestamp is kept: the firing window rule needs the
    # clock time, not just the date.
    df["Fire_TS"] = pd.to_datetime(df[cols.date], errors="coerce")
    dropped_no_date = int(df["Fire_TS"].isna().sum())
    df = df[df["Fire_TS"].notna()].copy()
    if dropped_no_date:
        notes.add("rows dropped", f"{dropped_no_date:,} with an unreadable {cols.date}")

    df["Survey Date"] = df["Fire_TS"].dt.normalize()
    df["Fire Hour"] = df["Fire_TS"].dt.hour
    df["Survey Month Key"] = df["Fire_TS"].dt.to_period("M")
    df["Month Name"] = df["Fire_TS"].dt.strftime("%b %Y")
    df["Day Name"] = df["Fire_TS"].dt.strftime("%a")
    df["Weekday"] = df["Fire_TS"].dt.weekday
    # ISO year-week: week 1 of 2027 starts inside Dec 2026, so the year has to
    # travel with the week or the two blur together in a slicer.
    df["ISO Week"] = df["Fire_TS"].dt.strftime("%G-W%V")

    # ── Score and NPS bands ─────────────────────────────────────────────────
    score = pd.to_numeric(df[cols.score], errors="coerce")
    out_of_range = score.notna() & ((score < 0) | (score > 10))
    df["Out Of Range"] = out_of_range.astype(int)
    # Classify from the CLEAN score, so an impossible value can never be counted
    # as a promoter in one column and excluded from the base in another.
    clean = score.where(~out_of_range)
    df[cols.score] = clean

    df["Is Answered"] = clean.notna().astype(int)
    df["Is Detractor"] = clean.between(0, 6).astype(int)
    df["Is Passive"] = clean.between(7, 8).astype(int)
    df["Is Promoter"] = clean.between(9, 10).astype(int)
    df["NPS Category"] = np.select(
        [clean.isna(), clean <= 6, clean <= 8],
        ["No Response", "Detractor", "Passive"], default="Promoter")
    df["Detractor Severity"] = np.select(
        [clean.isna() | (clean > 6), clean <= 2, clean <= 4],
        ["", "Severe (0-2)", "Moderate (3-4)"], default="Mild (5-6)")
    if int(out_of_range.sum()):
        notes.add("scores out of range",
                  f"{int(out_of_range.sum()):,} outside 0-10, excluded from the base")

    # ── Completion, reported for context only ───────────────────────────────
    if cols.completion_flag in df.columns:
        df["Is Completed"] = (df[cols.completion_flag].astype(str).str.strip()
                              .str.upper().eq("Y").astype(int))
        df["Has Completion Flag"] = 1
    else:
        notes.add("column missing",
                  f"{cols.completion_flag} - completion reported as not available")
        df["Is Completed"] = 0
        df["Has Completion Flag"] = 0

    # ── Subscriber identity, for the firing rules ───────────────────────────
    sub_col = find_column(df, cfg.firing.subscriber_col_candidates)
    if sub_col:
        df["Subscriber"] = normalise_msisdn(df[sub_col],
                                            cfg.firing.subscriber_normalise_last_n)
        blank = int(df["Subscriber"].isna().sum())
        notes.add("subscriber column", f"'{sub_col}'"
                  + (f"; {blank:,} row(s) blank" if blank else ""))
    else:
        df["Subscriber"] = pd.Series(pd.NA, index=df.index, dtype="object")
        notes.add("subscriber column not found",
                  "cooldown and trial-cap rules cannot be evaluated; "
                  f"looked for {list(cfg.firing.subscriber_col_candidates)[:6]}")
    df.attrs["subscriber_col"] = sub_col

    # ── Request Type from SR NUMBER ─────────────────────────────────────────
    df = classify_request_type(df, cfg, notes)

    # ── Keys and joins ──────────────────────────────────────────────────────
    df[cols.short_code] = df[cols.short_code].apply(normalize_short_code)
    df[cols.agent_queue] = as_text(df[cols.agent_queue]).fillna("(blank)")

    levels = list(cols.map_levels)
    if cols.map_queue_key in queue_map.columns and len(queue_map):
        df = df.merge(queue_map[[cols.map_queue_key, *levels]].rename(
            columns={cols.map_queue_key: "_qkey"}),
            left_on=cols.agent_queue, right_on="_qkey", how="left").drop(
            columns=["_qkey"])
    else:
        for col in levels:
            df[col] = np.nan
        notes.add("queue mapping unavailable", "all segment levels read 'Unmapped'")

    unmatched_rows = int(df[cols.segment_col].isna().sum()) if cols.segment_col in df else 0
    unmatched_queues = ([(str(q), int(n)) for q, n in
                         df.loc[df[cols.segment_col].isna(), cols.agent_queue]
                         .value_counts().head(30).items()] if unmatched_rows else [])
    for col in levels:
        df[col] = df[col].fillna("Unmapped")

    data_codes = set(df[cols.short_code]) - {""}
    map_codes = set(sc_map[cols.map_sc_key]) - {""}
    unmatched_codes = sorted(data_codes - map_codes)
    df = df.merge(sc_map[[cols.map_sc_key, *cols.map_sc_cols]],
                  left_on=cols.short_code, right_on=cols.map_sc_key, how="left")
    if cols.map_sc_key != cols.short_code:
        df = df.drop(columns=[cols.map_sc_key])
    for col in cols.map_sc_cols:
        df[col] = df[col].replace("", np.nan).fillna("Unmapped")
    df[cols.short_code] = df[cols.short_code].replace("", "(blank)")
    df["SC Label"] = np.where(df["Topic / Reason"] == "Unmapped", df[cols.short_code],
                              df[cols.short_code] + " - " + df["Topic / Reason"].astype(str))
    print(f"    -> SHORT_CODE match: {len(data_codes & map_codes):,} matched / "
          f"{len(unmatched_codes):,} unmatched")

    # ── Duplicate records across files ──────────────────────────────────────
    dup_count = 0
    if cols.interaction_id in df.columns:
        dupes = df[cols.interaction_id].notna() & df[cols.interaction_id].duplicated()
        dup_count = int(dupes.sum())
        if dup_count:
            notes.add("duplicate records",
                      f"{dup_count:,} repeated {cols.interaction_id} value(s) - "
                      "overlapping exports double-count everything")
    df["Is Duplicate"] = (df[cols.interaction_id].duplicated()
                          if cols.interaction_id in df.columns else False)

    df.attrs.update({
        "dropped_no_date": dropped_no_date,
        "out_of_range": int(out_of_range.sum()),
        "unmatched_queue_rows": unmatched_rows,
        "unmatched_queues": unmatched_queues,
        "unmatched_codes": unmatched_codes,
        "duplicate_records": dup_count,
    })
    return df


def classify_request_type(df: pd.DataFrame, cfg: Config, notes: Notes) -> pd.DataFrame:
    """SR NUMBER -> SR / Activity, per the configured rule."""
    rule = cfg.request_type
    sr_col = find_column(df, cfg.columns.sr_number_candidates)
    df.attrs["sr_col"] = sr_col
    if sr_col is None:
        notes.add("SR column not found", f"Request Type reads '{rule.label_na}'")
        df["SR Number Clean"] = ""
        df["SR Digits"] = np.nan
        df["Request Type"] = rule.label_na
        return df

    def clean_sr(v: Any) -> str:
        if pd.isna(v) or v is None:
            return ""
        t = str(v).strip().replace("\xa0", "").replace("\t", "")
        if t.startswith("'"):
            t = t[1:]
        if t.endswith(".0") and t[:-2].replace("-", "").isdigit():
            t = t[:-2]
        return t

    df["SR Number Clean"] = df[sr_col].apply(clean_sr)
    digits = df["SR Number Clean"].str.replace(r"\D", "", regex=True).str.len()
    # No digits at all is not a zero-digit identifier, it is an unusable value -
    # otherwise "ABC-XYZ" silently classifies as Activity.
    df["SR Digits"] = digits.where(digits > 0)
    df["SR Value"] = pd.to_numeric(
        df["SR Number Clean"].str.replace(r"[^\d.-]", "", regex=True), errors="coerce")

    metric = df["SR Digits"] if rule.rule == "length" else df["SR Value"]
    above = metric > rule.threshold
    if rule.equal_is == "hi":
        above = above | (metric == rule.threshold)
    df["Request Type"] = np.where(metric.isna(), rule.label_na,
                                  np.where(above, rule.label_hi, rule.label_lo))

    dist = df["Request Type"].value_counts()
    print(f"    + SR column '{sr_col}' ({rule.rule} > {rule.threshold}): "
          + "  ".join(f"{k}={v:,}" for k, v in dist.items()))
    if len(dist) == 1 or (len(df) and dist.max() / len(df) > 0.99):
        notes.add("request type one-sided",
                  f"nearly every row is '{dist.idxmax()}' - check SR_RULE/threshold")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# FIRING COMPLIANCE ENGINE
#
# Three dispatch rules, checked on every fired record:
#   Rule 1  a customer who ANSWERED is not re-surveyed within N days
#   Rule 2  a customer who does NOT answer gets at most N attempts per cycle
#   Rule 3  surveys only fire inside the allowed clock window (and on allowed days)
#
# Rules 1 and 2 need a subscriber identity; Rule 3 needs only the timestamp, so a
# file without a phone column still gets the window check rather than nothing.
#
# Everything is vectorised: a per-subscriber Python loop is O(rows) in the
# interpreter and this runs over a full month of IVR traffic.
# ══════════════════════════════════════════════════════════════════════════════

RULE_COOLDOWN: Final[str] = "Re-surveyed within cooldown"
RULE_TRIALS: Final[str] = "Over the trial cap"
RULE_WINDOW: Final[str] = "Fired outside the time window"
RULE_WEEKDAY: Final[str] = "Fired on a disallowed day"
RULE_ORDER: Final[tuple[str, ...]] = (RULE_COOLDOWN, RULE_TRIALS, RULE_WINDOW,
                                      RULE_WEEKDAY)


def check_firing_compliance(df: pd.DataFrame, cfg: Config,
                            notes: Notes) -> pd.DataFrame:
    """Flag every record against the firing rules.

    Adds, per row:
        Fire_In_Window      bool   inside the allowed clock window
        Fire_Day_Allowed    bool   fired on an allowed weekday
        Days_Since_Response float  days since that customer last ANSWERED
        Cooldown_Violation  bool   re-surveyed too soon after an answer
        Trial_Cycle         int    which attempt cycle this fire belongs to
        Trial_Index         int    1-based attempt number inside that cycle
        Trial_Violation     bool   attempt number above the cap
        Violations          str    the rules broken, joined with " | "
        Is_Compliant        bool   no rule broken
    """
    rules = cfg.firing
    out = df.copy()
    n = len(out)

    # ── Rule 3: clock window and allowed days ───────────────────────────────
    times = out["Fire_TS"].dt.time
    start, end = rules.start_time, rules.end_time
    if rules.window_end_inclusive:
        in_window = pd.Series([start <= t <= end for t in times], index=out.index)
    else:
        in_window = pd.Series([start <= t < end for t in times], index=out.index)
    out["Fire_In_Window"] = in_window.fillna(False)

    if rules.allowed_weekdays:
        out["Fire_Day_Allowed"] = out["Weekday"].isin(list(rules.allowed_weekdays))
    else:
        out["Fire_Day_Allowed"] = True

    # ── Rules 1 and 2 need to know who was called ───────────────────────────
    has_subscriber = out["Subscriber"].notna().any()
    if not has_subscriber:
        out["Days_Since_Response"] = np.nan
        out["Cooldown_Violation"] = False
        out["Trial_Cycle"] = np.nan
        out["Trial_Index"] = np.nan
        out["Trials_In_Cycle"] = np.nan
        out["Cycle_Answered"] = np.nan
        out["Trial_Violation"] = False
        out.attrs["subscriber_rules_evaluated"] = False
    else:
        out = _evaluate_subscriber_rules(out, rules)
        out.attrs["subscriber_rules_evaluated"] = True

    # ── Collect the verdict ─────────────────────────────────────────────────
    flags = pd.DataFrame({
        RULE_COOLDOWN: out["Cooldown_Violation"].fillna(False).astype(bool),
        RULE_TRIALS: out["Trial_Violation"].fillna(False).astype(bool),
        RULE_WINDOW: ~out["Fire_In_Window"].astype(bool),
        RULE_WEEKDAY: ~out["Fire_Day_Allowed"].astype(bool),
    })
    for rule in RULE_ORDER:
        out[f"V: {rule}"] = flags[rule].astype(int)
    out["Violations"] = flags.apply(
        lambda row: " | ".join(r for r in RULE_ORDER if row[r]), axis=1)
    out["Is_Compliant"] = ~flags.any(axis=1)
    out["Violation_Count"] = flags.sum(axis=1)

    broken = int((~out["Is_Compliant"]).sum())
    print(f"    -> Firing compliance: {n - broken:,} of {n:,} records compliant "
          f"({(n - broken) / n:.1%})" if n else "    -> no records")
    for rule in RULE_ORDER:
        count = int(flags[rule].sum())
        if count:
            print(f"       x {rule}: {count:,} ({count / n:.1%})")
    if not has_subscriber:
        notes.add("firing rules partially evaluated",
                  "no subscriber column - only the time-window rule was checked")
    return out


def _evaluate_subscriber_rules(out: pd.DataFrame, rules: FiringRules) -> pd.DataFrame:
    """Cooldown and trial-cap rules, per subscriber, in fire order."""
    order = out.sort_values(["Subscriber", "Fire_TS"], kind="mergesort").index
    d = out.loc[order]
    sub = d["Subscriber"]
    ts = d["Fire_TS"]
    answered = d["Is Answered"].fillna(0).astype(int)

    # ── Rule 1: days since this customer last ANSWERED, strictly before now ──
    # shift(1) inside the group takes the previous row, ffill carries the most
    # recent answer forward - so a fire is never compared against its own answer.
    response_ts = ts.where(answered.eq(1))
    last_response = response_ts.groupby(sub, sort=False).shift(1)
    last_response = last_response.groupby(sub, sort=False).ffill()
    days_since = (ts - last_response).dt.total_seconds() / 86400.0
    d["Days_Since_Response"] = days_since
    d["Cooldown_Violation"] = days_since.lt(rules.response_cooldown_days).fillna(False)

    # ── Rule 2: attempt number inside the current cycle ─────────────────────
    prev_ts = ts.groupby(sub, sort=False).shift(1)
    prev_answered = answered.groupby(sub, sort=False).shift(1).fillna(0)
    new_cycle = prev_ts.isna() | prev_answered.eq(1)
    if rules.trial_cycle_gap_hours:
        gap_hours = (ts - prev_ts).dt.total_seconds() / 3600.0
        new_cycle = new_cycle | gap_hours.gt(rules.trial_cycle_gap_hours).fillna(False)
    if rules.trial_cycle_key and rules.trial_cycle_key in d.columns:
        key = d[rules.trial_cycle_key].astype(str)
        new_cycle = new_cycle | key.ne(key.groupby(sub, sort=False).shift(1)).fillna(True)

    d["Trial_Cycle"] = new_cycle.astype(int).groupby(sub, sort=False).cumsum()
    d["Trial_Index"] = d.groupby([sub, d["Trial_Cycle"]], sort=False).cumcount() + 1
    d["Trial_Violation"] = d["Trial_Index"] > rules.max_trials_per_cycle

    cycle_group = d.groupby([sub, d["Trial_Cycle"]], sort=False)
    d["Trials_In_Cycle"] = cycle_group["Trial_Index"].transform("max")
    d["Cycle_Answered"] = cycle_group["Is Answered"].transform("max")

    carry = ["Days_Since_Response", "Cooldown_Violation", "Trial_Cycle",
             "Trial_Index", "Trial_Violation", "Trials_In_Cycle", "Cycle_Answered"]
    return out.join(d[carry])


def compliance_summary(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """One row per rule: how often it was broken and what it cost."""
    n = len(df)
    rows: list[dict[str, Any]] = []
    evaluated = df.attrs.get("subscriber_rules_evaluated", False)
    rule_state = {
        RULE_COOLDOWN: evaluated, RULE_TRIALS: evaluated,
        RULE_WINDOW: True,
        RULE_WEEKDAY: bool(cfg.firing.allowed_weekdays),
    }
    detail = {
        RULE_COOLDOWN: f"no re-survey within {cfg.firing.response_cooldown_days:g} days "
                       "of an answer",
        RULE_TRIALS: f"at most {cfg.firing.max_trials_per_cycle} attempts per cycle",
        RULE_WINDOW: f"fire only between {cfg.firing.window_label()}",
        RULE_WEEKDAY: "fire only on the allowed weekdays",
    }
    for rule in RULE_ORDER:
        col = f"V: {rule}"
        hits = int(df[col].sum()) if col in df.columns else 0
        subset = df[df[col].eq(1)] if col in df.columns else df.iloc[0:0]
        rows.append({
            "Rule": rule,
            "Requirement": detail[rule],
            "Evaluated": "yes" if rule_state[rule] else "not evaluated",
            "Records": hits,
            "% of fires": (hits / n) if n else np.nan,
            "Subscribers": int(subset["Subscriber"].nunique()) if hits else 0,
            "Answered": int(subset["Is Answered"].sum()) if hits else 0,
            "tNPS of these": tnps_of(subset),
        })
    compliant = df[df["Is_Compliant"]]
    rows.append({
        "Rule": "ALL RULES PASSED", "Requirement": "no rule broken",
        "Evaluated": "yes", "Records": int(len(compliant)),
        "% of fires": (len(compliant) / n) if n else np.nan,
        "Subscribers": int(compliant["Subscriber"].nunique()),
        "Answered": int(compliant["Is Answered"].sum()),
        "tNPS of these": tnps_of(compliant),
    })
    return pd.DataFrame(rows)


def tnps_of(frame: pd.DataFrame) -> float:
    """tNPS over the answered rows of any subset."""
    answered = float(frame["Is Answered"].sum()) if len(frame) else 0.0
    if not answered:
        return np.nan
    return 100.0 * (frame["Is Promoter"].sum() - frame["Is Detractor"].sum()) / answered


def compliance_by_day(df: pd.DataFrame) -> pd.DataFrame:
    """Daily violation counts - a rule that breaks on one day is an incident."""
    agg = {"Fires": ("Is Answered", "size"), "Answered": ("Is Answered", "sum"),
           "Compliant": ("Is_Compliant", "sum")}
    for rule in RULE_ORDER:
        agg[rule] = (f"V: {rule}", "sum")
    out = df.groupby("Survey Date", observed=True).agg(**agg).reset_index()
    out["Violations"] = out[list(RULE_ORDER)].sum(axis=1)
    out["Compliance %"] = out["Compliant"] / _nz(out["Fires"])
    return out.sort_values("Survey Date")


def compliance_by_dimension(df: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """Where the misfires concentrate - queue, segment, request type."""
    if dimension not in df.columns:
        return pd.DataFrame()
    agg = {"Fires": ("Is Answered", "size"), "Compliant": ("Is_Compliant", "sum")}
    for rule in RULE_ORDER:
        agg[rule] = (f"V: {rule}", "sum")
    out = df.groupby(dimension, dropna=False, observed=True).agg(**agg).reset_index()
    out["Violations"] = out[list(RULE_ORDER)].sum(axis=1)
    out["Violation %"] = out["Violations"] / _nz(out["Fires"])
    out["Compliance %"] = out["Compliant"] / _nz(out["Fires"])
    return out.sort_values("Violations", ascending=False)


def over_surveyed_subscribers(df: pd.DataFrame, cfg: Config,
                              top_n: int = 100) -> pd.DataFrame:
    """Customers the campaign hit hardest - the human cost of a misfire.

    Sorted by violations, because a customer called eight times in a week is a
    complaint waiting to happen and the single most persuasive evidence that the
    dispatch rules are not being enforced.
    """
    if not df.attrs.get("subscriber_rules_evaluated", False):
        return pd.DataFrame()
    sub = df[df["Subscriber"].notna()]
    if sub.empty:
        return pd.DataFrame()
    out = sub.groupby("Subscriber", observed=True).agg(
        Fires=("Is Answered", "size"),
        Answered=("Is Answered", "sum"),
        Cycles=("Trial_Cycle", "nunique"),
        Max_Trials_In_A_Cycle=("Trial_Index", "max"),
        Cooldown_Breaches=(f"V: {RULE_COOLDOWN}", "sum"),
        Trial_Breaches=(f"V: {RULE_TRIALS}", "sum"),
        Window_Breaches=(f"V: {RULE_WINDOW}", "sum"),
        Min_Gap_Days=("Days_Since_Response", "min"),
        First_Fire=("Fire_TS", "min"),
        Last_Fire=("Fire_TS", "max"),
    ).reset_index()
    out["Violations"] = (out["Cooldown_Breaches"] + out["Trial_Breaches"]
                         + out["Window_Breaches"])
    out["Span_Days"] = (out["Last_Fire"] - out["First_Fire"]).dt.total_seconds() / 86400.0
    out = out[out["Violations"] > 0]
    return out.sort_values(["Violations", "Fires"], ascending=False).head(top_n)


def trial_effectiveness(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Does attempt 2 or 3 actually convert?

    Falls straight out of the trial-cap engine and answers the question the cap
    exists to settle: if attempt 3 converts at a fraction of attempt 1 and its
    respondents are no different, the cap is too high and the extra call is pure
    customer annoyance. If it converts well, the cap may be costing coverage.
    """
    if not df.attrs.get("subscriber_rules_evaluated", False):
        return pd.DataFrame()
    sub = df[df["Trial_Index"].notna()].copy()
    if sub.empty:
        return pd.DataFrame()
    cap = cfg.firing.max_trials_per_cycle
    sub["Attempt"] = np.where(sub["Trial_Index"] > cap, f"{cap + 1}+ (over cap)",
                              sub["Trial_Index"].astype(int).astype(str))
    order = [str(i) for i in range(1, cap + 1)] + [f"{cap + 1}+ (over cap)"]
    out = sub.groupby("Attempt", observed=True).agg(
        Fires=("Is Answered", "size"),
        Answered=("Is Answered", "sum"),
        Detractors=("Is Detractor", "sum"),
        Promoters=("Is Promoter", "sum"),
    ).reset_index()
    out["Response Rate"] = out["Answered"] / _nz(out["Fires"])
    out["tNPS"] = 100.0 * (out["Promoters"] - out["Detractors"]) / _nz(out["Answered"])
    out["Detractor Rate"] = out["Detractors"] / _nz(out["Answered"])
    lo, hi = wilson_ci(out["Answered"], out["Fires"])
    out["RR CI Lo"], out["RR CI Hi"] = lo, hi
    out["Attempt"] = pd.Categorical(out["Attempt"], categories=order, ordered=True)
    return out.sort_values("Attempt").reset_index(drop=True)


def hourly_firing_profile(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Fires by hour of day, with the allowed window marked."""
    out = df.groupby("Fire Hour", observed=True).agg(
        Fires=("Is Answered", "size"),
        Answered=("Is Answered", "sum"),
        Detractors=("Is Detractor", "sum"),
        Promoters=("Is Promoter", "sum"),
    ).reset_index()
    full = pd.DataFrame({"Fire Hour": range(24)})
    out = full.merge(out, on="Fire Hour", how="left").fillna(
        {"Fires": 0, "Answered": 0, "Detractors": 0, "Promoters": 0})
    start, end = cfg.firing.start_time.hour, cfg.firing.end_time.hour
    out["In Window"] = out["Fire Hour"].between(start, end)
    out["Response Rate"] = out["Answered"] / _nz(out["Fires"])
    out["tNPS"] = 100.0 * (out["Promoters"] - out["Detractors"]) / _nz(out["Answered"])
    out["Share"] = _share(out["Fires"])
    return out


# ══════════════════════════════════════════════════════════════════════════════
# DAILY SERIES
# ══════════════════════════════════════════════════════════════════════════════

FLAG_BUILDING = "baseline building"
FLAG_NO_DATA = "no records"
FLAG_LOW_N = "low n"
FLAG_NORMAL = "normal"
FLAG_BELOW = "below avg"
FLAG_ABOVE = "above avg"
FLAG_BELOW_SIG = "BELOW - significant"
FLAG_SPIKE_SIG = "SPIKE - significant"


def build_daily(frame: pd.DataFrame, cfg: Config,
                calendar: pd.DatetimeIndex | None = None) -> tuple[pd.DataFrame, list]:
    """Daily series with baselines, confidence intervals and flags.

    Used for the headline series and for each Request Type split, so a split is
    always computed with exactly the same rules as the total.
    """
    an = cfg.analysis
    daily = frame.groupby("Survey Date", observed=True).agg(
        Fires=("Is Answered", "size"),
        Compliant_Fires=("Is_Compliant", "sum"),
        Answered=("Is Answered", "sum"),
        Completed=("Is Completed", "sum"),
        Detractors=("Is Detractor", "sum"),
        Passives=("Is Passive", "sum"),
        Promoters=("Is Promoter", "sum"),
    ).reset_index().sort_values("Survey Date")

    index = calendar if calendar is not None else pd.date_range(
        daily["Survey Date"].min(), daily["Survey Date"].max(), freq="D")
    present = set(daily["Survey Date"])
    missing = [d for d in index if d not in present]
    daily = (daily.set_index("Survey Date").reindex(index, fill_value=0)
             .rename_axis("Survey Date").reset_index())

    daily["Day Name"] = daily["Survey Date"].dt.strftime("%a")
    daily["Weekday"] = daily["Survey Date"].dt.weekday
    daily["Month Key"] = daily["Survey Date"].dt.to_period("M")
    # A reindexed day has no records at all. It is shown, and excluded from every
    # baseline: an absence of data is not an observation of zero.
    daily["Has Records"] = (daily["Fires"] > 0).astype(int)
    volume = daily["Fires"]

    daily["Response Rate"] = daily["Answered"] / _nz(daily["Fires"])
    daily["Completion Rate"] = daily["Completed"] / _nz(daily["Fires"])
    daily["Compliance Rate"] = daily["Compliant_Fires"] / _nz(daily["Fires"])
    daily["Detractor Rate"] = daily["Detractors"] / _nz(daily["Answered"])
    daily["tNPS"] = 100.0 * (daily["Promoters"] - daily["Detractors"]) / _nz(daily["Answered"])

    dates = daily["Survey Date"]
    z95 = an.z_alpha()

    # ── A. Fired volume vs baseline ─────────────────────────────────────────
    daily["Fires Base"] = baseline(daily["Fires"], dates, an, "mean", volume)
    daily["Fires Base SD"] = baseline(daily["Fires"], dates, an, "std", volume)
    daily["Fires Base N"] = baseline(daily["Fires"], dates, an, "count", volume)
    daily["Fires vs Base"] = daily["Fires"] - daily["Fires Base"]
    daily["Fires vs Base %"] = daily["Fires vs Base"] / _nz(daily["Fires Base"])
    daily["Fires Z"] = daily["Fires vs Base"] / _nz(daily["Fires Base SD"])
    # t, not 1.96: the SD can rest on as few as two prior same-weekday points.
    t_crit = baseline_t_critical(daily["Fires Base N"], an.alpha)
    daily["Fires T Crit"] = t_crit
    daily["Fires Flag"] = np.select(
        [daily["Has Records"].eq(0),
         daily["Fires Base"].isna(),
         daily["Fires Z"].le(-t_crit),
         daily["Fires"] < daily["Fires Base"]],
        [FLAG_NO_DATA, FLAG_BUILDING, FLAG_BELOW_SIG, FLAG_BELOW],
        default=FLAG_NORMAL)

    # ── B. Detractor rate vs baseline ───────────────────────────────────────
    # Pooled prior periods (sum of detractors / sum of answered), not the mean of
    # daily rates: pooling weights each prior day by its own volume, which is
    # what a proportion test requires.
    det_prior = baseline(daily["Detractors"], dates, an, "sum", volume)
    ans_prior = baseline(daily["Answered"], dates, an, "sum", volume)
    prom_prior = baseline(daily["Promoters"], dates, an, "sum", volume)
    daily["Det Rate Base"] = det_prior / _nz(ans_prior)
    daily["Det Rate vs Base"] = daily["Detractor Rate"] - daily["Det Rate Base"]
    daily["Det CI Lo"], daily["Det CI Hi"] = wilson_ci(daily["Detractors"],
                                                       daily["Answered"], z95)
    det_z, det_p = [], []
    for i in range(len(daily)):
        z, p = two_prop_z(daily["Detractors"].iat[i], daily["Answered"].iat[i],
                          det_prior.iat[i], ans_prior.iat[i])
        det_z.append(z); det_p.append(p)
    daily["Det Z"] = det_z
    daily["Det p"] = det_p
    daily["Det q"] = fdr_qvalues(daily["Det p"]) if an.fdr_correction else np.nan

    # ── C. tNPS vs baseline ─────────────────────────────────────────────────
    daily["tNPS Base"] = 100.0 * (prom_prior - det_prior) / _nz(ans_prior)
    daily["tNPS vs Base"] = daily["tNPS"] - daily["tNPS Base"]
    p_hat = daily["Promoters"] / _nz(daily["Answered"])
    d_hat = daily["Detractors"] / _nz(daily["Answered"])
    daily["tNPS SE"] = nps_se(p_hat, d_hat, daily["Answered"], an.variance_floor_n)
    daily["tNPS CI Lo"] = daily["tNPS"] - z95 * daily["tNPS SE"]
    daily["tNPS CI Hi"] = daily["tNPS"] + z95 * daily["tNPS SE"]
    se_base = nps_se(prom_prior / _nz(ans_prior), det_prior / _nz(ans_prior),
                     ans_prior, an.variance_floor_n)
    z_t, p_t = nps_diff_z(daily["tNPS"], daily["tNPS SE"], daily["tNPS Base"], se_base)
    daily["tNPS Z"] = z_t
    daily["tNPS p"] = p_t
    daily["tNPS q"] = fdr_qvalues(daily["tNPS p"]) if an.fdr_correction else np.nan
    # What this day COULD have detected - "not significant" often just means
    # "too few answers to tell", and that is a different management response.
    daily["tNPS MDE"] = tnps_mde(daily["Answered"], p_hat.fillna(0.5),
                                 d_hat.fillna(0.3), an.alpha)

    sig_col = "tNPS q" if an.fdr_correction else "tNPS p"
    daily["tNPS Flag"] = np.select(
        [daily["Has Records"].eq(0),
         daily["tNPS Base"].isna(),
         daily["Answered"] < an.min_n_day,
         daily[sig_col].lt(an.alpha) & daily["tNPS"].lt(daily["tNPS Base"]),
         daily["tNPS"] < daily["tNPS Base"]],
        [FLAG_NO_DATA, FLAG_BUILDING, FLAG_LOW_N, FLAG_BELOW_SIG, FLAG_BELOW],
        default=FLAG_NORMAL)

    det_sig = "Det q" if an.fdr_correction else "Det p"
    daily["Det Flag"] = np.select(
        [daily["Has Records"].eq(0),
         daily["Det Rate Base"].isna(),
         daily["Answered"] < an.min_n_day,
         daily[det_sig].lt(an.alpha) & daily["Detractor Rate"].gt(daily["Det Rate Base"]),
         daily["Detractor Rate"] > daily["Det Rate Base"]],
        [FLAG_NO_DATA, FLAG_BUILDING, FLAG_LOW_N, FLAG_SPIKE_SIG, FLAG_ABOVE],
        default=FLAG_NORMAL)

    return daily, missing


def cusum_changepoints(daily: pd.DataFrame, cfg: Analysis) -> pd.DataFrame:
    """Detect a sustained LEVEL SHIFT in daily tNPS.

    Day-vs-baseline cannot see a step: once tNPS drops, a trailing baseline
    follows it within a week and every day afterwards reads "normal". CUSUM
    accumulates small deviations, so a shift that is individually unremarkable
    but persistent is caught - which is exactly the pattern a real service
    regression produces.
    """
    series = daily.loc[daily["Has Records"].eq(1), ["Survey Date", "tNPS", "Answered"]]
    series = series[series["tNPS"].notna()]
    if len(series) < 8:
        return pd.DataFrame()

    values = series["tNPS"].to_numpy(dtype=float)
    # Reference level and scale from the first third, so the baseline period is
    # not contaminated by the shift being looked for.
    warmup = max(5, len(values) // 3)
    mu = float(np.mean(values[:warmup]))
    sd = float(np.std(values[:warmup], ddof=1)) or float(np.std(values, ddof=1)) or 1.0
    k = cfg.cusum_k_sigma * sd
    h = cfg.cusum_h_sigma * sd

    hi = lo = 0.0
    rows: list[dict[str, Any]] = []
    for i, (date, value) in enumerate(zip(series["Survey Date"], values)):
        hi = max(0.0, hi + (value - mu) - k)
        lo = min(0.0, lo + (value - mu) + k)
        if lo < -h or hi > h:
            direction = "DOWN" if lo < -h else "UP"
            after = values[i:]
            rows.append({
                "Survey Date": date,
                "Direction": direction,
                "tNPS Before": mu,
                "tNPS After": float(np.mean(after)),
                "Shift (pts)": float(np.mean(after)) - mu,
                "Days Observed": int(len(after)),
                "Detail": (f"level shifted {direction.lower()} and stayed there; "
                           f"reference {mu:.1f} from the first {warmup} days"),
            })
            # Re-anchor on the new level and keep scanning for the next shift.
            mu = float(np.mean(values[i:min(i + warmup, len(values))]))
            hi = lo = 0.0
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# IMPACT ENGINE
#
# tNPS is a weighted MEAN of a per-response score in {+1, 0, -1}, so
# tNPS_total = SUM(w_g x tNPS_g) holds identically and the decomposition closes
# on zero with no residual.
#
#   Impact_g = w_g x (tNPS_g - tNPS_total)   additive, sums to zero
#   LOO_g    = tNPS_without_g - tNPS_total   counterfactual
#   LOO_g    = -Impact_g / (1 - w_g)         so the two rank identically
# ══════════════════════════════════════════════════════════════════════════════


def tnps_impact_table(frame: pd.DataFrame, group_col: str,
                      cfg: Config) -> tuple[pd.DataFrame, float]:
    """Decompose a tNPS figure into per-group impact, with FDR-corrected tests."""
    an = cfg.analysis
    g = frame.groupby(group_col, dropna=False, observed=True).agg(
        Answered=("Is Answered", "sum"),
        Promoters=("Is Promoter", "sum"),
        Passives=("Is Passive", "sum"),
        Detractors=("Is Detractor", "sum"),
    ).reset_index()
    g = g[g["Answered"] > 0].copy()
    if g.empty:
        return pd.DataFrame(), np.nan

    N = float(g["Answered"].sum())
    P = float(g["Promoters"].sum())
    D = float(g["Detractors"].sum())
    if N == 0:
        return pd.DataFrame(), np.nan
    tnps_total = 100.0 * (P - D) / N

    g["Weight"] = g["Answered"] / N
    g["tNPS"] = 100.0 * (g["Promoters"] - g["Detractors"]) / g["Answered"]
    g["Detractor Rate"] = g["Detractors"] / g["Answered"]
    g["Gap vs Total"] = g["tNPS"] - tnps_total
    g["Impact (pts)"] = g["Weight"] * g["Gap vs Total"]

    rest_n = N - g["Answered"]
    rest_pd = (P - g["Promoters"]) - (D - g["Detractors"])
    g["tNPS Excl."] = np.where(rest_n > 0, 100.0 * rest_pd / rest_n, np.nan)
    g["LOO (pts)"] = g["tNPS Excl."] - tnps_total
    g["Detractor Excess"] = g["Detractors"] - g["Answered"] * (D / N)

    # Is the group genuinely different from everybody else?
    n_g = g["Answered"].to_numpy(dtype=float)
    n_r = N - n_g
    se_g = nps_se(g["Promoters"] / n_g, g["Detractors"] / n_g, n_g, an.variance_floor_n)
    p_rest = (P - g["Promoters"]) / np.where(n_r > 0, n_r, np.nan)
    d_rest = (D - g["Detractors"]) / np.where(n_r > 0, n_r, np.nan)
    se_r = nps_se(p_rest, d_rest, n_r, an.variance_floor_n)
    z, p = nps_diff_z(g["tNPS"], se_g, 100.0 * (p_rest - d_rest), se_r)
    too_small = (n_g < an.min_n_group) | (n_r <= 0)
    g["z vs Rest"] = np.where(too_small, np.nan, z)
    g["p-value"] = np.where(too_small, np.nan, p)
    g["q-value"] = fdr_qvalues(g["p-value"]) if an.fdr_correction else np.nan
    g["Significance"] = [
        sig_tag(pp, qq if an.fdr_correction else None, an.alpha)
        for pp, qq in zip(g["p-value"], g["q-value"] if an.fdr_correction
                          else [None] * len(g))]
    g["Reliable"] = np.where(n_g >= an.min_n_group, "yes", f"low n (<{an.min_n_group})")
    g = g.sort_values("Impact (pts)").reset_index(drop=True)
    return g, tnps_total


def mom_bridge(frame: pd.DataFrame, group_col: str, cfg: Config) -> pd.DataFrame:
    """Decompose the tNPS change between two months into MIX and RATE.

        d tNPS = SUM dw x (tNPS_prev + tNPS_now)/2      <- MIX
               + SUM (w_prev + w_now)/2 x d tNPS        <- RATE

    This is the question a monthly review actually asks. A bare MoM delta cannot
    separate "our service got worse" from "we simply sent more surveys to the
    worst-performing queue", and those need opposite responses.

    The symmetric (Shapley) form is used rather than the textbook Laspeyres one.
    Laspeyres leaves an interaction residual that has to be shown as a third bar
    and explained, and a group that BOTH grew and degraded - the usual real
    case - dumps most of its effect into that residual, which is precisely the
    group the reader most needs attributed. The symmetric split closes exactly:
    mix + rate = the whole change, no residual. The Laspeyres components are
    kept alongside for anyone reconciling to an older report.
    """
    months = sorted(frame["Survey Month Key"].dropna().unique())
    if len(months) < 2:
        return pd.DataFrame()
    prev_key, now_key = months[-2], months[-1]

    def profile(month) -> pd.DataFrame:
        sub = frame[frame["Survey Month Key"] == month]
        g = sub.groupby(group_col, dropna=False, observed=True).agg(
            Answered=("Is Answered", "sum"),
            Promoters=("Is Promoter", "sum"),
            Detractors=("Is Detractor", "sum"),
        ).reset_index()
        g = g[g["Answered"] > 0]
        total = float(g["Answered"].sum())
        g["Weight"] = g["Answered"] / total if total else np.nan
        g["tNPS"] = 100.0 * (g["Promoters"] - g["Detractors"]) / g["Answered"]
        return g[[group_col, "Answered", "Weight", "tNPS"]]

    prev, now = profile(prev_key), profile(now_key)
    merged = prev.merge(now, on=group_col, how="outer", suffixes=(" Prev", " Now"))
    # A group absent in one month has zero weight there; its tNPS is undefined,
    # so the other month's value is used to keep the bridge additive.
    merged["Weight Prev"] = merged["Weight Prev"].fillna(0.0)
    merged["Weight Now"] = merged["Weight Now"].fillna(0.0)
    merged["tNPS Prev"] = merged["tNPS Prev"].fillna(merged["tNPS Now"])
    merged["tNPS Now"] = merged["tNPS Now"].fillna(merged["tNPS Prev"])
    merged[["Answered Prev", "Answered Now"]] = merged[
        ["Answered Prev", "Answered Now"]].fillna(0)

    dw = merged["Weight Now"] - merged["Weight Prev"]
    dt_ = merged["tNPS Now"] - merged["tNPS Prev"]
    merged["Mix (pts)"] = dw * (merged["tNPS Prev"] + merged["tNPS Now"]) / 2.0
    merged["Rate (pts)"] = (merged["Weight Prev"] + merged["Weight Now"]) / 2.0 * dt_
    merged["Total (pts)"] = merged["Mix (pts)"] + merged["Rate (pts)"]
    # Laspeyres form, for reconciliation with a previously published bridge.
    merged["Mix (Laspeyres)"] = dw * merged["tNPS Prev"]
    merged["Rate (Laspeyres)"] = merged["Weight Prev"] * dt_
    merged["Interaction (Laspeyres)"] = dw * dt_
    merged["Weight Change"] = dw
    merged["tNPS Change"] = dt_
    merged.attrs["prev"] = str(prev_key)
    merged.attrs["now"] = str(now_key)
    merged.attrs["tnps_prev"] = float((merged["Weight Prev"] * merged["tNPS Prev"]).sum())
    merged.attrs["tnps_now"] = float((merged["Weight Now"] * merged["tNPS Now"]).sum())
    return merged.sort_values("Total (pts)").reset_index(drop=True)


def two_way_pockets(frame: pd.DataFrame, row_col: str, col_col: str,
                    cfg: Config) -> pd.DataFrame:
    """Find pockets a one-way view dilutes.

    A problem confined to one short code INSIDE one segment is averaged away in
    both the by-code and the by-segment tables. Crossing them surfaces it.
    """
    an = cfg.analysis
    if row_col not in frame.columns or col_col not in frame.columns:
        return pd.DataFrame()
    answered = frame[frame["Is Answered"] == 1]
    if answered.empty:
        return pd.DataFrame()
    total = float(answered["Is Answered"].sum())
    overall = 100.0 * (answered["Is Promoter"].sum()
                       - answered["Is Detractor"].sum()) / total

    g = answered.groupby([row_col, col_col], dropna=False, observed=True).agg(
        Answered=("Is Answered", "sum"),
        Promoters=("Is Promoter", "sum"),
        Detractors=("Is Detractor", "sum"),
    ).reset_index()
    g = g[g["Answered"] >= an.pocket_min_n].copy()
    if g.empty:
        return pd.DataFrame()

    g["tNPS"] = 100.0 * (g["Promoters"] - g["Detractors"]) / g["Answered"]
    g["Weight"] = g["Answered"] / total
    g["Gap vs Total"] = g["tNPS"] - overall
    g["Impact (pts)"] = g["Weight"] * g["Gap vs Total"]
    g["Detractor Rate"] = g["Detractors"] / g["Answered"]

    n_g = g["Answered"].to_numpy(dtype=float)
    n_r = total - n_g
    se_g = nps_se(g["Promoters"] / n_g, g["Detractors"] / n_g, n_g, an.variance_floor_n)
    p_rest = (answered["Is Promoter"].sum() - g["Promoters"]) / np.where(n_r > 0, n_r, np.nan)
    d_rest = (answered["Is Detractor"].sum() - g["Detractors"]) / np.where(n_r > 0, n_r, np.nan)
    se_r = nps_se(p_rest, d_rest, n_r, an.variance_floor_n)
    _, p = nps_diff_z(g["tNPS"], se_g, 100.0 * (p_rest - d_rest), se_r)
    g["p-value"] = p
    g["q-value"] = fdr_qvalues(p) if an.fdr_correction else np.nan
    g["Significance"] = [sig_tag(pp, qq if an.fdr_correction else None, an.alpha)
                         for pp, qq in zip(g["p-value"],
                                           g["q-value"] if an.fdr_correction
                                           else [None] * len(g))]
    g["Overall tNPS"] = overall
    return g.sort_values("Impact (pts)").head(an.pocket_top_n).reset_index(drop=True)


def verdict_line(row: pd.Series, label_col: str) -> str:
    """One-sentence verdict for an impact row."""
    name = row[label_col]
    if row["Impact (pts)"] < 0:
        return (f"{name} drags tNPS down by {abs(row['Impact (pts)']):.1f} pts "
                f"({row['Weight']:.1%} of answers at {row['tNPS']:.1f} tNPS) - "
                f"without it the period would read {row['tNPS Excl.']:.1f} "
                f"(+{row['LOO (pts)']:.1f}). {row['Significance']}.")
    return (f"{name} lifts tNPS by {row['Impact (pts)']:.1f} pts "
            f"({row['Weight']:.1%} of answers at {row['tNPS']:.1f} tNPS). "
            f"{row['Significance']}.")


def verdict_line_ar(row: pd.Series, label_col: str) -> str:
    """Arabic mirror. The RLM prefix and readingOrder on the cell keep the
    Latin numerals from re-ordering inside the right-to-left sentence."""
    name = row[label_col]
    if row["Impact (pts)"] < 0:
        return ("‏" + f"{name} بيسحب الـ tNPS لتحت بمقدار "
                f"{abs(row['Impact (pts)']):.1f} نقطة "
                f"({row['Weight']:.1%} من الردود بـ tNPS {row['tNPS']:.1f}) — "
                f"من غيره كان هيبقى {row['tNPS Excl.']:.1f}.")
    return ("‏" + f"{name} بيرفع الـ tNPS بمقدار {row['Impact (pts)']:.1f} نقطة "
            f"({row['Weight']:.1%} من الردود بـ tNPS {row['tNPS']:.1f}).")


# ══════════════════════════════════════════════════════════════════════════════
# CHARTS
#
# Design rules applied throughout:
#   - No dual-axis anywhere. The old Pareto put counts and cumulative share on
#     two y-scales, which invents a correlation the data does not contain; it is
#     now two stacked panels sharing one x-axis.
#   - Red means NEGATIVE only (violations, significant drops, drags). Identity
#     is carried by graphite / gold / muted / sand, never by red.
#   - Hairline SOLID gridlines one shade off the surface; no dashed grid, no
#     chart borders, no top/right spines.
#   - Thin marks, generous padding, and direct labels only where they carry the
#     point - never a number on every bar.
# ══════════════════════════════════════════════════════════════════════════════

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator
from matplotlib.dates import DateFormatter, AutoDateLocator

H = lambda token: "#" + token          # brand token -> matplotlib hex


def init_chart_style() -> None:
    """One rcParams pass, so every figure is consistent by default."""
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Liberation Sans", "Helvetica", "DejaVu Sans"],
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": H(LINE),
        "axes.linewidth": 0.8,
        "axes.labelcolor": H(SLATE),
        "axes.labelsize": 8.5,
        "axes.titlesize": 11,
        "axes.titlecolor": H(INK),
        "axes.grid": False,
        "xtick.color": H(MUTED),
        "ytick.color": H(MUTED),
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "legend.fontsize": 8,
        "legend.frameon": False,
        "lines.linewidth": 1.8,
        "lines.solid_capstyle": "round",
        "figure.dpi": 130,
    })


def _axes(ax, *, title: str = "", subtitle: str = "", ygrid: bool = True,
          xgrid: bool = False) -> None:
    """Recessive chrome: hairline solid grid, no box, left-aligned title."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(H(LINE))
    if ygrid:
        ax.grid(axis="y", color=H(LINE), lw=0.7, ls="-")
    if xgrid:
        ax.grid(axis="x", color=H(LINE), lw=0.7, ls="-")
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, fontsize=11, fontweight="bold", loc="left",
                     color=H(INK), pad=16 if subtitle else 10)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=8,
                color=H(MUTED), va="bottom", ha="left")


def _date_axis(ax) -> None:
    """Short, horizontal date ticks. '08 Jan' beats a rotated '2026-01-08'."""
    ax.xaxis.set_major_locator(AutoDateLocator(maxticks=9))
    ax.xaxis.set_major_formatter(DateFormatter("%d %b"))
    for label in ax.get_xticklabels():
        label.set_rotation(0)
        label.set_ha("center")


def _headroom(ax, frac: float = 0.18) -> None:
    """Explicit space above the tallest mark for a direct label.

    ax.margins() is ignored on the zero side of a bar chart (sticky edges), so
    a top label lands on the subtitle unless the limit is set by hand.
    """
    low, high = ax.get_ylim()
    span = high - low
    if span > 0:
        ax.set_ylim(low, high + span * frac)


def _label_point(ax, x, y, text: str, color: str = INK, dy: int = 8) -> None:
    """A single direct label. Used sparingly - never one per data point."""
    ax.annotate(text, xy=(x, y), xytext=(0, dy), textcoords="offset points",
                ha="center", fontsize=8, fontweight="bold", color=H(color))


def fig_to_png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="white")
    buf.seek(0)
    plt.close(fig)
    return buf


def _pct_axis(ax) -> None:
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0%}"))


def chart_hourly_firing(hourly: pd.DataFrame, cfg: Config):
    """Fires by hour with the permitted window marked.

    The clearest possible statement of Rule 3: everything outside the shaded
    band should not exist.
    """
    fig, ax = plt.subplots(figsize=(11, 3.0))
    inside = hourly["In Window"].to_numpy()
    colors = [H(GRAPHITE) if ok else H(RED) for ok in inside]
    ax.bar(hourly["Fire Hour"], hourly["Fires"], color=colors, width=0.72)

    start, end = cfg.firing.start_time.hour, cfg.firing.end_time.hour
    ax.axvspan(start - 0.5, end + 0.5, color=H(SURFACE), zorder=0)

    outside = int(hourly.loc[~hourly["In Window"], "Fires"].sum())
    if outside:
        worst = hourly[~hourly["In Window"]].nlargest(1, "Fires").iloc[0]
        _label_point(ax, worst["Fire Hour"], worst["Fires"],
                     f"{int(worst['Fires']):,}", RED)
    window = f"{cfg.firing.window_start}-{cfg.firing.window_end}"
    _axes(ax, title="Surveys fired by hour of day",
          subtitle=(f"shaded band is the permitted window {window}  ·  "
                    + (f"{outside:,} fires fall outside it" if outside
                       else "every fire falls inside it")))
    _headroom(ax, 0.16)
    ax.set_xticks(range(0, 24, 2))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v):02d}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v):,}"))
    return fig_to_png(fig)


def chart_compliance_daily(by_day: pd.DataFrame):
    """Violations per day, split by rule.

    Every category here is a failure, so none of them gets red - that would
    imply one rule is the bad one. Identity is carried by the neutral
    categorical ramp; the headline colour stays available for the total.
    """
    active = [r for r in RULE_ORDER if r in by_day.columns and by_day[r].sum() > 0]
    if not active or by_day.empty:
        return None
    fig, ax = plt.subplots(figsize=(11, 3.0))
    bottom = np.zeros(len(by_day))
    for i, rule in enumerate(active):
        values = by_day[rule].to_numpy(dtype=float)
        ax.bar(by_day["Survey Date"], values, bottom=bottom, width=0.72,
               color=H(CATEGORICAL[i % len(CATEGORICAL)]), label=rule,
               edgecolor="white", linewidth=0.8)
        bottom += values
    total = int(by_day[active].to_numpy().sum())
    _axes(ax, title="Firing-rule violations per day",
          subtitle=f"{total:,} violations across {len(active)} rule(s)")
    ax.legend(ncol=min(len(active), 4), loc="upper left", bbox_to_anchor=(0, 1.0))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v):,}"))
    _date_axis(ax)
    return fig_to_png(fig)


def chart_trial_effectiveness(trials: pd.DataFrame, cfg: Config):
    """Response rate by attempt number, with Wilson intervals.

    Answers the question the trial cap exists to settle: whether the later
    attempts are earning their annoyance.
    """
    if trials.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.6, 3.0))
    labels = trials["Attempt"].astype(str)
    over = labels.str.contains("over cap")
    colors = [H(RED) if o else H(GRAPHITE) for o in over]
    x = np.arange(len(trials))
    rate = trials["Response Rate"].to_numpy(dtype=float)
    ax.bar(x, rate, color=colors, width=0.62)

    lo = np.clip(rate - trials["RR CI Lo"].to_numpy(dtype=float), 0, None)
    hi = np.clip(trials["RR CI Hi"].to_numpy(dtype=float) - rate, 0, None)
    # A 2xN array, not a list of two arrays: matplotlib inspects the first
    # element of a list and numpy deprecates treating an array as a scalar.
    ax.errorbar(x, rate, yerr=np.vstack([lo, hi]), fmt="none", ecolor=H(MUTED),
                elinewidth=1, capsize=3)
    for xi, value, fires in zip(x, rate, trials["Fires"]):
        if np.isfinite(value):
            _label_point(ax, xi, value + hi[int(xi)], f"{value:.0%}", INK, dy=4)
    _axes(ax, title="Response rate by attempt",
          subtitle=f"cap is {cfg.firing.max_trials_per_cycle} attempts; "
                   "bars show 95% Wilson intervals")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_xlabel("Attempt number within a cycle")
    _pct_axis(ax)
    _headroom(ax, 0.20)
    return fig_to_png(fig)


def chart_daily_volume(daily: pd.DataFrame, cfg: Config):
    """Fired volume against its baseline."""
    fig, ax = plt.subplots(figsize=(11, 3.0))
    sig = daily["Fires Flag"].eq(FLAG_BELOW_SIG).to_numpy()
    colors = np.where(sig, H(RED), H(GRAPHITE))
    ax.bar(daily["Survey Date"], daily["Fires"], color=colors, width=0.72)
    ax.plot(daily["Survey Date"], daily["Fires Base"], color=H(GOLD), lw=1.8,
            label=f"{cfg.analysis.baseline_mode.replace('_', '-')} baseline")
    n_sig = int(sig.sum())
    if n_sig:
        worst = daily.loc[daily["Fires vs Base"].idxmin()]
        _label_point(ax, worst["Survey Date"], worst["Fires"],
                     f"{int(worst['Fires']):,}", RED)
    _axes(ax, title="Surveys fired per day",
          subtitle=(f"{n_sig} day(s) significantly below baseline"
                    if n_sig else "no day significantly below baseline"))
    ax.legend(loc="upper right")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v):,}"))
    _headroom(ax, 0.14)
    _date_axis(ax)
    return fig_to_png(fig)


def chart_daily_tnps(daily: pd.DataFrame, overall: float, cfg: Config):
    """Daily tNPS with its uncertainty band and baseline."""
    fig, ax = plt.subplots(figsize=(11, 3.2))
    ax.fill_between(daily["Survey Date"], daily["tNPS CI Lo"], daily["tNPS CI Hi"],
                    color=H(LINE), alpha=0.75, lw=0, label="95% CI")
    ax.plot(daily["Survey Date"], daily["tNPS"], color=H(GRAPHITE), lw=1.8,
            label="tNPS")
    ax.plot(daily["Survey Date"], daily["tNPS Base"], color=H(GOLD), lw=1.6,
            alpha=0.9, label="baseline")
    ax.axhline(overall, color=H(MUTED), lw=1, label="period tNPS")
    sig = daily[daily["tNPS Flag"] == FLAG_BELOW_SIG]
    if len(sig):
        ax.scatter(sig["Survey Date"], sig["tNPS"], color=H(RED), s=26, zorder=5,
                   label="significantly below", edgecolor="white", linewidth=0.8)
        worst = sig.loc[sig["tNPS vs Base"].idxmin()]
        _label_point(ax, worst["Survey Date"], worst["tNPS"],
                     f"{worst['tNPS']:.0f}", RED, dy=-14)
    _axes(ax, title="Daily tNPS against baseline",
          subtitle=f"{len(sig)} day(s) significantly below after FDR correction")
    ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.16))
    ax.margins(y=0.12)
    _date_axis(ax)
    return fig_to_png(fig)


def chart_pareto(sc_det: pd.DataFrame, cfg: Config):
    """Detractor counts and cumulative share - as two panels, never two y-axes.

    The previous version put counts on the left axis and cumulative percent on a
    twin right axis. Two scales aligned arbitrarily on one plot manufacture a
    relationship, which is the single most misleading thing a chart can do.
    """
    top = sc_det.head(cfg.analysis.top_n_impact)
    if top.empty:
        return None
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 4.4), sharex=True,
        gridspec_kw={"height_ratios": [2, 1], "hspace": 0.16})
    x = np.arange(len(top))

    ax1.bar(x, top["Detractors"], color=H(RED), width=0.5)
    biggest = top["Detractors"].idxmax()
    _label_point(ax1, x[top.index.get_loc(biggest)], top.loc[biggest, "Detractors"],
                 f"{int(top.loc[biggest, 'Detractors']):,}", RED, dy=5)
    _axes(ax1, title="SHORT_CODE Pareto",
          subtitle="detractor count (top) and cumulative share of all detractors (below)")
    ax1.set_ylabel("Detractors")
    _headroom(ax1, 0.16)
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v):,}"))

    ax2.plot(x, top["Cumulative %"], color=H(GRAPHITE), lw=1.6)
    ax2.scatter(x, top["Cumulative %"], color=H(GRAPHITE), s=16, zorder=4,
                edgecolor="white", linewidth=0.8)
    ax2.axhline(cfg.analysis.pareto_target, color=H(GOLD), lw=1.4)
    ax2.text(len(top) - 0.4, cfg.analysis.pareto_target,
             f" {cfg.analysis.pareto_target:.0%}", fontsize=8, color=H(GOLD_DARK),
             va="center")
    _axes(ax2, ygrid=True)
    ax2.set_ylim(0, 1.05)
    _pct_axis(ax2)
    ax2.set_xticks(x)
    ax2.set_xticklabels(top["SHORT_CODE"].astype(str).str.slice(0, 16),
                        rotation=45, ha="right", fontsize=8)
    return fig_to_png(fig)


def chart_impact_tornado(table: pd.DataFrame, label_col: str, title: str,
                         cfg: Config):
    """Who drags and who lifts, on one axis, sorted."""
    if table.empty:
        return None
    worst = table.nsmallest(cfg.analysis.top_n_impact, "Impact (pts)")
    best = table.nlargest(5, "Impact (pts)")
    t = pd.concat([worst, best]).drop_duplicates(subset=[label_col])
    t = t.sort_values("Impact (pts)")
    fig, ax = plt.subplots(figsize=(11, max(3.0, 0.34 * len(t))))
    colors = [H(RED) if v < 0 else H(POS) for v in t["Impact (pts)"]]
    y = np.arange(len(t))
    ax.barh(y, t["Impact (pts)"], color=colors, height=0.62)
    ax.axvline(0, color=H(INK), lw=1)
    ax.set_yticks(y)
    ax.set_yticklabels(t[label_col].astype(str).str.slice(0, 44), fontsize=8)
    biggest = t["Impact (pts)"].abs().idxmax()
    value = t.loc[biggest, "Impact (pts)"]
    pos = list(t.index).index(biggest)
    ax.annotate(f"{value:+.2f}", xy=(value, pos),
                xytext=(6 if value > 0 else -6, 0), textcoords="offset points",
                va="center", ha="left" if value > 0 else "right",
                fontsize=8, fontweight="bold", color=H(RED if value < 0 else POS))
    _axes(ax, title=title, subtitle="weighted contribution to period tNPS, in points",
          ygrid=False, xgrid=True)
    ax.set_xlabel("Impact (tNPS points)")
    ax.margins(x=0.10)
    return fig_to_png(fig)


def chart_mom_bridge(bridge: pd.DataFrame, cfg: Config):
    """Waterfall: last month's tNPS -> mix -> rate -> this month's tNPS.

    Two movements, not three: the symmetric split leaves no interaction residual
    to explain away, so every point of the change is attributed to either mix or
    rate.
    """
    if bridge.empty:
        return None
    prev = float(bridge.attrs.get("tnps_prev", np.nan))
    now = float(bridge.attrs.get("tnps_now", np.nan))
    mix = float(bridge["Mix (pts)"].sum())
    rate = float(bridge["Rate (pts)"].sum())

    labels = [bridge.attrs.get("prev", "previous"), "Mix", "Rate",
              bridge.attrs.get("now", "current")]
    fig, ax = plt.subplots(figsize=(8.0, 3.2))

    # Anchors are solid columns; movements float between them.
    ax.bar(0, prev, color=H(GRAPHITE), width=0.5)
    ax.bar(3, now, color=H(GRAPHITE), width=0.5)
    running = prev
    for i, value in ((1, mix), (2, rate)):
        colour = H(POS) if value >= 0 else H(RED)
        ax.bar(i, value, bottom=running, color=colour, width=0.5)
        ax.annotate(f"{value:+.1f}", xy=(i, running + max(value, 0)),
                    xytext=(0, 5), textcoords="offset points", ha="center",
                    fontsize=8, fontweight="bold",
                    color=H(POS if value >= 0 else RED))
        running += value
    # Connectors make the arithmetic legible at a glance.
    for x0, y in ((0, prev), (1, prev + mix), (2, prev + mix + rate)):
        ax.plot([x0 + 0.25, x0 + 0.75], [y, y], color=H(LINE), lw=1, zorder=0)
    for i, value in ((0, prev), (3, now)):
        ax.annotate(f"{value:.1f}", xy=(i, value), xytext=(0, 5),
                    textcoords="offset points", ha="center", fontsize=8,
                    fontweight="bold", color=H(INK))

    ax.axhline(0, color=H(LINE), lw=1)
    _axes(ax, title="What moved tNPS since last month",
          subtitle="Mix = volume moved between codes. Rate = codes performed differently.")
    ax.set_xticks(range(4))
    ax.set_xticklabels(labels, fontsize=8)
    _headroom(ax, 0.20)
    return fig_to_png(fig)


def chart_weekday_profile(daily: pd.DataFrame):
    """The weekly shape the baseline has to correct for.

    Published because it is the evidence for using a same-weekday baseline: if
    these bars are flat, a plain 7-day mean would have been fine.
    """
    factors = weekday_factors(daily["Fires"], daily["Survey Date"])
    if factors.empty:
        return None
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    values = [factors.get(i, np.nan) for i in range(7)]
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.bar(range(7), values, color=H(GRAPHITE), width=0.6)
    ax.axhline(1.0, color=H(GOLD), lw=1.4)
    ax.text(6.4, 1.0, " average", fontsize=8, color=H(GOLD_DARK), va="center")
    spread = (np.nanmax(values) - np.nanmin(values)) if np.isfinite(values).any() else 0
    _axes(ax, title="Weekday volume profile",
          subtitle=f"lightest day is {spread:.0%} below the heaviest - "
                   "why the baseline compares like weekdays")
    _headroom(ax, 0.14)
    ax.set_xticks(range(7))
    ax.set_xticklabels(names, fontsize=8)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}x"))
    return fig_to_png(fig)


def chart_day_impact(day_impact: pd.DataFrame):
    """Which days moved the month."""
    if day_impact.empty:
        return None
    latest = day_impact["Month"].iloc[-1]
    t = day_impact[day_impact["Month"] == latest].copy()
    t["Survey Date"] = pd.to_datetime(t["Survey Date"])
    t = t.sort_values("Survey Date")
    fig, ax = plt.subplots(figsize=(11, 3.0))
    colors = [H(RED) if v < 0 else H(POS) for v in t["Impact (pts)"]]
    ax.bar(t["Survey Date"], t["Impact (pts)"], color=colors, width=0.72)
    ax.axhline(0, color=H(INK), lw=1)
    worst = t.loc[t["Impact (pts)"].idxmin()]
    _label_point(ax, worst["Survey Date"], worst["Impact (pts)"],
                 f"{worst['Impact (pts)']:+.2f}", RED, dy=-14)
    _axes(ax, title=f"Which days moved {latest}",
          subtitle="weighted contribution to the month's tNPS - sums to zero")
    ax.set_ylabel("Impact (pts)")
    _date_axis(ax)
    return fig_to_png(fig)


# ══════════════════════════════════════════════════════════════════════════════
# EXCEL OUTPUT
#
# One generic table writer drives every sheet, so column formatting, semantic
# colour and date handling are defined once instead of twenty times.
# ══════════════════════════════════════════════════════════════════════════════

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.drawing.image import Image as XLImage
from openpyxl.worksheet.table import Table, TableStyleInfo


def _fill(hexcode: str) -> PatternFill:
    return PatternFill("solid", fgColor=hexcode)


def _font(size: float = 9, bold: bool = False, color: str = INK,
          italic: bool = False) -> Font:
    return Font(name=FONT, size=size, bold=bold, color=color, italic=italic)


_HAIR = Side(style="thin", color=LINE)
_BORDER = Border(left=_HAIR, right=_HAIR, top=_HAIR, bottom=_HAIR)


@dataclass
class Col:
    """One output column: where the value comes from and how it is presented."""

    header: str
    key: str
    fmt: str | None = None
    width: float = 12
    align: str = "center"
    bold: bool = False
    color: str = INK
    neg_red: bool = False     # negative values in red - red = negative, always
    pos_bad: bool = False     # inverted: a POSITIVE value is the bad one
    flag: bool = False        # semantic fill from the flag vocabulary
    is_date: bool = False     # write a real date, never a string


def flag_style(flag: object) -> tuple[str | None, str]:
    """Semantic fill and text colour for a flag value."""
    text = str(flag)
    if text in (FLAG_BELOW_SIG, FLAG_SPIKE_SIG):
        return RED_TINT, RED
    if text in (FLAG_BELOW, FLAG_ABOVE):
        return GOLD_TINT, GOLD_DARK
    if text in (FLAG_BUILDING, FLAG_LOW_N, FLAG_NO_DATA):
        return SURFACE, MUTED
    return None, POS


def sheet_title(ws, text: str, ncols: int, subtitle: str = "") -> None:
    """Row 1 title, row 2 caption, row 3 blank, headers land on row 4."""
    ws.sheet_view.showGridLines = False
    span = max(ncols, 2)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    cell = ws.cell(row=1, column=1, value=text)
    cell.font = _font(14, True, INK)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=span)
    cap = ws.cell(row=2, column=1, value=subtitle)
    cap.font = _font(8, False, MUTED, italic=True)
    cap.alignment = Alignment(horizontal="left", vertical="center")


def header_row(ws, row: int, headers: Sequence[str], fill: str = INK,
               size: float = 9) -> None:
    for j, head in enumerate(headers, 1):
        cell = ws.cell(row=row, column=j, value=head)
        cell.font = _font(size, True, WHITE)
        cell.fill = _fill(fill)
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        cell.border = _BORDER
    ws.row_dimensions[row].height = 30


def put(ws, row: int, col: int, value: Any, fmt: str | None = None,
        bold: bool = False, color: str = INK, fill: str | None = None,
        align: str = "center", size: float = 9, rtl: bool = False):
    """Write one cell, normalising numpy and missing values on the way in."""
    if value is not None and not isinstance(value, (str, bool, dt.date, dt.datetime)):
        if isinstance(value, (np.integer,)):
            value = int(value)
        elif isinstance(value, (np.floating, float)):
            value = None if pd.isna(value) else float(value)
        elif isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()
        elif pd.isna(value):
            value = None
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = _font(size, bold, color)
    cell.border = _BORDER
    cell.alignment = Alignment(
        horizontal=align, vertical="center", wrap_text=False,
        readingOrder=2 if rtl else 0)
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = _fill(fill)
    return cell


def write_table(ws, frame: pd.DataFrame, cols: Sequence[Col], start_row: int = 4,
                total_row: dict[str, Any] | None = None) -> int:
    """Header + body for one dataframe. Returns the next free row."""
    header_row(ws, start_row, [c.header for c in cols])
    for i, col in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = col.width
    row = start_row + 1
    for record in frame.to_dict("records"):
        for j, col in enumerate(cols, 1):
            value = record.get(col.key)
            colour, fill = col.color, None
            if col.flag:
                fill, colour = flag_style(value)
            elif col.neg_red and isinstance(value, (int, float, np.number)) and \
                    pd.notna(value) and value < 0:
                colour = RED
            elif col.pos_bad and isinstance(value, (int, float, np.number)) and \
                    pd.notna(value) and value > 0:
                colour = RED
            if col.is_date and pd.notna(value):
                value = pd.Timestamp(value).to_pydatetime()
            put(ws, row, j, value, col.fmt, col.bold, colour, fill, col.align)
        row += 1
    if total_row:
        for j, col in enumerate(cols, 1):
            value = total_row.get(col.key)
            put(ws, row, j, value, col.fmt if value is not None else None,
                bold=True, fill=SURFACE, align=col.align)
        row += 1
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1).coordinate
    return row


def add_note(ws, row: int, text: str, ncols: int) -> int:
    """A footnote explaining how to read the table above it."""
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = _font(8, False, MUTED, italic=True)
    cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(ncols, 2))
    ws.row_dimensions[row].height = 30
    return row + 2


def add_image(ws, png, row: int, rows_tall: int = 20) -> int:
    """Embed a chart, returning the first row clear of it."""
    if png is None:
        return row
    try:
        img = XLImage(png)
        img.anchor = f"A{row}"
        ws.add_image(img)
        # Row height is ~20px; size the skip from the image's own height so a
        # taller chart never lands on top of the note that follows it.
        return row + max(rows_tall, int(img.height / 19) + 2)
    except Exception:
        return row


def kpi_block(ws, row: int, kpis: Sequence[tuple[str, Any, str, str]],
              per_row: int = 3) -> int:
    """A grid of headline figures. The number is the chart."""
    for i, (label, value, fmt, colour) in enumerate(kpis):
        col = 1 + (i % per_row) * 2
        r = row + (i // per_row) * 3
        lab = ws.cell(row=r, column=col, value=label.upper())
        lab.font = _font(9, True, MUTED)
        val = ws.cell(row=r + 1, column=col, value=(
            None if (isinstance(value, float) and pd.isna(value)) else value))
        val.font = _font(20, True, colour)
        val.number_format = fmt
    return row + ((len(kpis) - 1) // per_row + 1) * 3 + 1


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class Results:
    """Everything one run produces. Returned by analyse() so the pipeline can be
    driven from a notebook or a test without touching Excel."""

    cfg: Config
    notes: Notes
    df: pd.DataFrame                  # every fired record
    base: pd.DataFrame                # the answered analysis base
    daily: pd.DataFrame
    daily_by_type: dict[str, pd.DataFrame]
    request_types: list[str]
    compliance: pd.DataFrame
    compliance_daily: pd.DataFrame
    compliance_by_queue: pd.DataFrame
    hourly: pd.DataFrame
    trials: pd.DataFrame
    over_surveyed: pd.DataFrame
    sc_det: pd.DataFrame
    sc_impact: pd.DataFrame
    topic_impact: pd.DataFrame
    type_impact: pd.DataFrame
    seg_impact: pd.DataFrame
    day_impact: pd.DataFrame
    sc_day: pd.DataFrame
    monthly: pd.DataFrame
    bridge: pd.DataFrame
    pockets: pd.DataFrame
    changepoints: pd.DataFrame
    missing_days: list
    totals: dict[str, Any]
    files_loaded: list[tuple[str, int]]
    inputs: dict[str, Any]


def analyse(data_paths: Sequence[Path], queue_map_path: Path | None,
            sc_map_path: Path | None, cfg: Config = CONFIG) -> Results:
    """Load, prepare, check compliance and compute every table."""
    notes = Notes()
    print("\n[1] Loading...")
    raw, files_loaded = load_survey_files(data_paths, cfg, notes)
    queue_map = load_queue_mapping(queue_map_path, cfg, notes)

    print("\n[2] Preparing...")
    codes = sorted({normalize_short_code(v) for v in raw[cfg.columns.short_code]} - {""})
    sc_map = load_short_code_mapping(sc_map_path, codes, cfg, notes)
    df = prepare(raw, queue_map, sc_map, cfg, notes)

    print("\n[3] Checking firing compliance...")
    df = check_firing_compliance(df, cfg, notes)

    # The analysis base. Non-compliant records stay in by default so the headline
    # cannot move without the operator asking for it; the compliance sheet always
    # reports tNPS both ways so the sensitivity is visible either way.
    base = df[df["Is Answered"] == 1]
    if cfg.firing.exclude_non_compliant_from_base:
        before = len(base)
        base = base[base["Is_Compliant"]]
        notes.add("base filtered",
                  f"{before - len(base):,} non-compliant answered record(s) excluded "
                  "from every tNPS figure")
    base = base.copy()
    if base.empty:
        raise InputError("No answered surveys in the data - nothing to analyse.")

    print("\n[4] Building daily series...")
    calendar = pd.date_range(df["Survey Date"].min(), df["Survey Date"].max(), freq="D")
    daily, missing_days = build_daily(df, cfg, calendar)
    request_types = [t for t in (cfg.request_type.label_hi, cfg.request_type.label_lo,
                                 cfg.request_type.label_na)
                     if t in set(df["Request Type"])]
    daily_by_type = {t: build_daily(df[df["Request Type"] == t], cfg, calendar)[0]
                     for t in request_types}
    changepoints = cusum_changepoints(daily, cfg.analysis)

    print("[5] Computing impact...")
    sc_impact, _ = tnps_impact_table(base, "SC Label", cfg)
    sc_impact = sc_impact.rename(columns={"SC Label": "Short Code"})
    topic_impact, _ = tnps_impact_table(base, "Topic / Reason", cfg)
    topic_impact = topic_impact.rename(columns={"Topic / Reason": "Topic"})
    type_impact, _ = tnps_impact_table(base, "Request Type", cfg)
    seg_impact, _ = tnps_impact_table(base, cfg.columns.segment_col, cfg)
    seg_impact = seg_impact.rename(columns={cfg.columns.segment_col: "Segment"})

    day_frames = []
    for _, grp in base.groupby("Survey Month Key", observed=True):
        table, month_tnps = tnps_impact_table(grp, "Survey Date", cfg)
        if table.empty:
            continue
        table.insert(0, "Month", str(grp["Month Name"].iloc[0]))
        table["Month tNPS"] = month_tnps
        day_frames.append(table)
    day_impact = pd.concat(day_frames, ignore_index=True) if day_frames else pd.DataFrame()
    if not day_impact.empty:
        day_impact["Day Name"] = pd.to_datetime(day_impact["Survey Date"]).dt.strftime("%a")

    sc_day_rows = []
    for date, grp in base.groupby("Survey Date", observed=True):
        if len(grp) < cfg.analysis.min_n_day:
            continue
        table, day_tnps = tnps_impact_table(grp, "SC Label", cfg)
        if table.empty:
            continue
        table = table.rename(columns={"SC Label": "Short Code"})
        table = table.sort_values("Impact (pts)").head(cfg.analysis.top_n_per_day)
        table.insert(0, "Survey Date", date)
        table["Day tNPS"] = day_tnps
        table["Rank"] = range(1, len(table) + 1)
        sc_day_rows.append(table)
    sc_day = pd.concat(sc_day_rows, ignore_index=True) if sc_day_rows else pd.DataFrame()

    print("[6] Short code Pareto, bridge, pockets...")
    total_det = int(base["Is Detractor"].sum())
    sc_det = base.groupby([cfg.columns.short_code, "Topic / Reason", "Category",
                           "Owner"], dropna=False, observed=True).agg(
        Answered=("Is Answered", "sum"),
        Detractors=("Is Detractor", "sum"),
        Promoters=("Is Promoter", "sum"),
        Severe=("Detractor Severity", lambda s: int((s == "Severe (0-2)").sum())),
    ).reset_index()
    sc_det = sc_det[sc_det["Answered"] > 0].copy()
    sc_det["Detractor Rate"] = sc_det["Detractors"] / _nz(sc_det["Answered"])
    sc_det["tNPS"] = 100.0 * (sc_det["Promoters"] - sc_det["Detractors"]) / _nz(sc_det["Answered"])
    sc_det["Severe %"] = sc_det["Severe"] / _nz(sc_det["Detractors"])
    sc_det = sc_det.sort_values("Detractors", ascending=False).reset_index(drop=True)
    sc_det["Share of Detractors"] = sc_det["Detractors"] / max(total_det, 1)
    sc_det["Cumulative %"] = sc_det["Share of Detractors"].cumsum()
    sc_det["Pareto"] = np.where(sc_det["Cumulative %"] <= cfg.analysis.pareto_target,
                                f"top {cfg.analysis.pareto_target:.0%}", "tail")
    lo, hi = wilson_ci(sc_det["Detractors"], sc_det["Answered"])
    sc_det["Rate CI Lo"], sc_det["Rate CI Hi"] = lo, hi

    monthly = base.groupby("Survey Month Key", observed=True).agg(
        Month=("Month Name", "first"),
        Answered=("Is Answered", "sum"),
        Detractors=("Is Detractor", "sum"),
        Passives=("Is Passive", "sum"),
        Promoters=("Is Promoter", "sum"),
    ).reset_index().sort_values("Survey Month Key")
    fires = df.groupby("Survey Month Key", observed=True).size().rename("Fires")
    monthly = monthly.merge(fires, on="Survey Month Key", how="left")
    monthly["Response Rate"] = monthly["Answered"] / _nz(monthly["Fires"])
    monthly["Detractor Rate"] = monthly["Detractors"] / _nz(monthly["Answered"])
    monthly["tNPS"] = 100.0 * (monthly["Promoters"] - monthly["Detractors"]) / _nz(monthly["Answered"])
    monthly["tNPS MoM"] = monthly["tNPS"].diff()

    bridge = mom_bridge(base, "SC Label", cfg)
    pockets = two_way_pockets(base, "SC Label", cfg.columns.segment_col, cfg)

    compliance = compliance_summary(df, cfg)
    compliance_daily = compliance_by_day(df)
    compliance_by_queue = compliance_by_dimension(df, cfg.columns.agent_queue)
    hourly = hourly_firing_profile(df, cfg)
    trials = trial_effectiveness(df, cfg)
    over_surveyed = over_surveyed_subscribers(df, cfg)

    total_fires = len(df)
    total_ans = int(base["Is Answered"].sum())
    total_det = int(base["Is Detractor"].sum())
    total_prom = int(base["Is Promoter"].sum())
    answered_not_completed = int(((df["Is Answered"] == 1) &
                                  (df["Is Completed"] == 0)).sum())
    hidden_det = int(((df["Is Detractor"] == 1) & (df["Is Completed"] == 0)).sum())
    totals = {
        "fires": total_fires,
        "answered": total_ans,
        "completed": int(df["Is Completed"].sum()),
        "has_completion_flag": bool(df["Has Completion Flag"].max()),
        "answered_not_completed": answered_not_completed,
        "hidden_detractors": hidden_det,
        "hidden_detractor_share": (hidden_det / total_det) if total_det else 0.0,
        "detractors": total_det,
        "promoters": total_prom,
        "passives": int(base["Is Passive"].sum()),
        "tnps": 100.0 * (total_prom - total_det) / total_ans if total_ans else np.nan,
        "detractor_rate": total_det / total_ans if total_ans else np.nan,
        "response_rate": total_ans / total_fires if total_fires else np.nan,
        "compliant": int(df["Is_Compliant"].sum()),
        "compliance_rate": df["Is_Compliant"].mean() if total_fires else np.nan,
        "tnps_compliant": tnps_of(df[df["Is_Compliant"]]),
        "tnps_non_compliant": tnps_of(df[~df["Is_Compliant"]]),
        "period_from": df["Survey Date"].min(),
        "period_to": df["Survey Date"].max(),
    }

    print(f"\n    -> Fired {total_fires:,} | Answered {total_ans:,} "
          f"({totals['response_rate']:.1%}) | Detractors {total_det:,} "
          f"({totals['detractor_rate']:.1%}) | tNPS {totals['tnps']:.1f}")

    return Results(
        cfg=cfg, notes=notes, df=df, base=base, daily=daily,
        daily_by_type=daily_by_type, request_types=request_types,
        compliance=compliance, compliance_daily=compliance_daily,
        compliance_by_queue=compliance_by_queue, hourly=hourly, trials=trials,
        over_surveyed=over_surveyed, sc_det=sc_det, sc_impact=sc_impact,
        topic_impact=topic_impact, type_impact=type_impact, seg_impact=seg_impact,
        day_impact=day_impact, sc_day=sc_day, monthly=monthly, bridge=bridge,
        pockets=pockets, changepoints=changepoints, missing_days=missing_days,
        totals=totals, files_loaded=files_loaded,
        inputs={"data": [str(p) for p in data_paths],
                "queue_map": str(queue_map_path) if queue_map_path else "",
                "sc_map": str(sc_map_path) if sc_map_path else "",
                "digest": file_digest(Path(data_paths[0])) if data_paths else ""},
    )


# ══════════════════════════════════════════════════════════════════════════════
# WORKBOOK
# ══════════════════════════════════════════════════════════════════════════════


def build_workbook(res: Results, out_path: Path, run_ts: dt.datetime) -> Path:
    """Render every result into one branded workbook."""
    cfg = res.cfg
    t = res.totals
    init_chart_style()
    wb = Workbook()
    wb.remove(wb.active)
    sheets: list[tuple[str, str]] = []

    def new_sheet(name: str, purpose: str, tab: str = RED):
        ws = wb.create_sheet(name[:31])
        ws.sheet_properties.tabColor = tab
        sheets.append((name[:31], purpose))
        return ws

    period = (f"Period {t['period_from']:%d-%b-%Y} to {t['period_to']:%d-%b-%Y}  |  "
              f"Base = ANSWERED (Q1 answered), not completed  |  "
              f"Baseline = {cfg.analysis.baseline_mode.replace('_', '-')}, excludes the day itself  |  "
              f"Firing rules: cooldown {cfg.firing.response_cooldown_days:g}d, "
              f"max {cfg.firing.max_trials_per_cycle} trials, window "
              f"{cfg.firing.window_start}-{cfg.firing.window_end}  |  "
              f"Generated {run_ts:%d-%b-%Y %H:%M} by tNPS Impact {SCRIPT_VERSION}")

    print("\n[7] Rendering charts...")
    png_hourly = chart_hourly_firing(res.hourly, cfg)
    png_comp_daily = chart_compliance_daily(res.compliance_daily)
    png_trials = chart_trial_effectiveness(res.trials, cfg)
    png_volume = chart_daily_volume(res.daily, cfg)
    png_weekday = chart_weekday_profile(res.daily)
    png_tnps = chart_daily_tnps(res.daily, t["tnps"], cfg)
    png_pareto = chart_pareto(res.sc_det, cfg)
    png_sc = chart_impact_tornado(res.sc_impact, "Short Code",
                                  "SHORT_CODE impact on period tNPS", cfg)
    png_topic = chart_impact_tornado(res.topic_impact, "Topic",
                                     "Topic impact on period tNPS", cfg)
    png_bridge = chart_mom_bridge(res.bridge, cfg)
    png_day_impact = chart_day_impact(res.day_impact)

    print("[8] Writing workbook...")

    # ── 1. EXECUTIVE SUMMARY ────────────────────────────────────────────────
    ws = new_sheet("Executive Summary", "Headline numbers, firing health and the verdict")
    sheet_title(ws, "tNPS Impact - Executive Summary", 8, period)
    for i, width in enumerate([26, 16, 16, 16, 14, 14, 14, 30], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    row = kpi_block(ws, 4, [
        ("Surveys fired", t["fires"], FMT_INT, INK),
        ("Answered (base)", t["answered"], FMT_INT, INK),
        ("Response rate", t["response_rate"], FMT_PCT, INK),
        ("Detractors", t["detractors"], FMT_INT, RED),
        ("Detractor rate", t["detractor_rate"], FMT_PCT, RED),
        ("tNPS", t["tnps"], FMT_DEC1, INK),
        ("Firing compliance", t["compliance_rate"], FMT_PCT,
         POS if t["compliance_rate"] >= 0.99 else RED),
        ("Non-compliant fires", t["fires"] - t["compliant"], FMT_INT,
         RED if t["compliant"] < t["fires"] else INK),
        ("tNPS if compliant only", t["tnps_compliant"], FMT_DEC1, INK),
    ])

    ws.cell(row=row, column=1, value="Firing behaviour").font = _font(11, True, INK)
    row += 1
    comp_cols = [
        Col("Rule", "Rule", width=30, align="left", bold=True),
        Col("Requirement", "Requirement", width=42, align="left", color=SLATE),
        Col("Evaluated", "Evaluated", width=14, color=MUTED),
        Col("Records", "Records", FMT_INT, 12, pos_bad=True),
        Col("% of fires", "% of fires", FMT_PCT, 12, pos_bad=True),
        Col("Subscribers", "Subscribers", FMT_INT, 13),
        Col("Answered", "Answered", FMT_INT, 12),
        Col("tNPS of these", "tNPS of these", FMT_DEC1, 14),
    ]
    row = write_table(ws, res.compliance, comp_cols, row)
    row = add_note(ws, row + 1, (
        "Violations are reported, not removed: the headline tNPS above includes every "
        "record so the number cannot move without being asked to. 'tNPS if compliant "
        "only' is the sensitivity - if the two differ materially, the misfires are "
        "biasing the score and the base should be reconsidered."), 8)

    ws.cell(row=row, column=1, value="What is driving tNPS - top drags").font = _font(11, True, INK)
    row += 1
    drag_cols = [
        Col("Short Code", "Short Code", width=34, align="left"),
        Col("Answered", "Answered", FMT_INT, 12),
        Col("Weight", "Weight", FMT_PCT, 11),
        Col("tNPS", "tNPS", FMT_DEC1, 11),
        Col("Impact (pts)", "Impact (pts)", FMT_VAR, 13, bold=True, neg_red=True),
        Col("tNPS Excl.", "tNPS Excl.", FMT_DEC1, 12, color=MUTED),
        Col("LOO (pts)", "LOO (pts)", FMT_VAR, 12),
        Col("Significance", "Significance", width=26, align="left", color=MUTED),
    ]
    row = write_table(ws, res.sc_impact.nsmallest(8, "Impact (pts)"), drag_cols, row)
    row = add_note(ws, row + 1, (
        "Impact = weight x (group tNPS - period tNPS); the column sums to zero. "
        "LOO = the period tNPS you would see if that code did not exist. Both rank "
        "identically (LOO = -Impact / (1 - weight)); Impact apportions the gap, LOO "
        "states the counterfactual. Significance is FDR-corrected across all codes."), 8)

    if not res.sc_impact.empty:
        top = res.sc_impact.nsmallest(1, "Impact (pts)").iloc[0]
        cell = ws.cell(row=row, column=1, value=verdict_line(top, "Short Code"))
        cell.font = _font(10, True, INK)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        row += 1
        ar = put(ws, row, 1, verdict_line_ar(top, "Short Code"), align="right",
                 color=SLATE, size=10, rtl=True)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        row += 2

    if not res.changepoints.empty:
        ws.cell(row=row, column=1, value="Level shifts detected").font = _font(11, True, INK)
        row += 1
        row = write_table(ws, res.changepoints, [
            Col("Date", "Survey Date", FMT_DATE, 13, is_date=True),
            Col("Direction", "Direction", width=12),
            Col("tNPS before", "tNPS Before", FMT_DEC1, 13),
            Col("tNPS after", "tNPS After", FMT_DEC1, 13),
            Col("Shift (pts)", "Shift (pts)", FMT_VAR, 13, bold=True, neg_red=True),
            Col("Days observed", "Days Observed", FMT_INT, 14),
            Col("Detail", "Detail", width=60, align="left", color=MUTED),
        ], row)
        row = add_note(ws, row + 1, (
            "A day-vs-baseline test cannot see a level shift: once tNPS steps down, the "
            "baseline follows it within a week and every day afterwards reads 'normal'. "
            "CUSUM accumulates small persistent deviations, which is the shape a real "
            "service regression makes."), 8)

    # ── 2. FIRING COMPLIANCE ────────────────────────────────────────────────
    ws = new_sheet("Firing Compliance", "Was the survey fired within the rules?")
    sheet_title(ws, "Firing Compliance - dispatch rules", 11, period)
    row = 4
    ws.cell(row=row, column=1, value="Rules in force").font = _font(11, True, INK)
    row += 1
    header_row(ws, row, ["Rule", "Applied value", "", ""])
    for i, width in enumerate([40, 56, 14, 14], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    row += 1
    for label, value in cfg.firing.describe():
        put(ws, row, 1, label, align="left", bold=True)
        put(ws, row, 2, value, align="left", color=SLATE)
        put(ws, row, 3, "")
        put(ws, row, 4, "")
        row += 1
    row += 1
    row = write_table(ws, res.compliance, comp_cols, row)
    row = add_note(ws, row + 1, (
        f"tNPS over compliant fires only: {t['tnps_compliant']:.1f}. "
        f"tNPS over non-compliant fires: "
        f"{t['tnps_non_compliant']:.1f}. A large gap means the misfires are not a "
        "neutral operational nuisance - they are pulling the reported score."), 11)
    row = add_image(ws, png_hourly, row)
    row = add_image(ws, png_comp_daily, row)

    ws2 = new_sheet("Compliance by Day", "Daily violation counts per rule")
    sheet_title(ws2, "Firing violations by day", 10, period)
    day_cols = [
        Col("Date", "Survey Date", FMT_DATE, 13, is_date=True),
        Col("Fires", "Fires", FMT_INT, 11),
        Col("Answered", "Answered", FMT_INT, 11),
        Col("Compliant", "Compliant", FMT_INT, 12),
        Col("Compliance %", "Compliance %", FMT_PCT, 14, bold=True),
        *[Col(r, r, FMT_INT, 18, pos_bad=True) for r in RULE_ORDER],
        Col("Violations", "Violations", FMT_INT, 12, bold=True, pos_bad=True),
    ]
    write_table(ws2, res.compliance_daily, day_cols, 4)

    if not res.compliance_by_queue.empty:
        ws3 = new_sheet("Compliance by Queue", "Where the misfires concentrate")
        sheet_title(ws3, "Firing violations by agent queue", 9, period)
        write_table(ws3, res.compliance_by_queue.head(200), [
            Col(cfg.columns.agent_queue, cfg.columns.agent_queue, width=34, align="left"),
            Col("Fires", "Fires", FMT_INT, 11),
            Col("Compliant", "Compliant", FMT_INT, 12),
            Col("Compliance %", "Compliance %", FMT_PCT, 14),
            *[Col(r, r, FMT_INT, 18, pos_bad=True) for r in RULE_ORDER],
            Col("Violation %", "Violation %", FMT_PCT, 13, bold=True, pos_bad=True),
        ], 4)

    # ── 3. OVER-SURVEYED CUSTOMERS ──────────────────────────────────────────
    if not res.over_surveyed.empty:
        ws = new_sheet("Over-Surveyed Customers",
                       "Subscribers the campaign hit hardest")
        sheet_title(ws, "Customers contacted outside the rules", 12, period)
        row = write_table(ws, res.over_surveyed, [
            Col("Subscriber", "Subscriber", width=18, align="left", bold=True),
            Col("Fires", "Fires", FMT_INT, 10, pos_bad=True),
            Col("Answered", "Answered", FMT_INT, 11),
            Col("Cycles", "Cycles", FMT_INT, 10),
            Col("Max trials in a cycle", "Max_Trials_In_A_Cycle", FMT_INT, 16, pos_bad=True),
            Col("Cooldown breaches", "Cooldown_Breaches", FMT_INT, 16, pos_bad=True),
            Col("Trial-cap breaches", "Trial_Breaches", FMT_INT, 16, pos_bad=True),
            Col("Window breaches", "Window_Breaches", FMT_INT, 15, pos_bad=True),
            Col("Shortest gap (days)", "Min_Gap_Days", FMT_DEC1, 16),
            Col("First fire", "First_Fire", FMT_DATETIME, 16, is_date=True),
            Col("Last fire", "Last_Fire", FMT_DATETIME, 16, is_date=True),
            Col("Violations", "Violations", FMT_INT, 11, bold=True, pos_bad=True),
        ], 4)
        add_note(ws, row + 1, (
            "Shortest gap is the smallest number of days between one customer's answer "
            "and the next survey fired at them. Anything under the cooldown is a "
            "customer who answered and was called again anyway - the most direct "
            "evidence that the dispatch rules are not being enforced."), 12)

    # ── 4. TRIAL EFFECTIVENESS ──────────────────────────────────────────────
    if not res.trials.empty:
        ws = new_sheet("Trial Effectiveness", "Does attempt 2 or 3 actually convert?")
        sheet_title(ws, "Response by attempt number", 9, period)
        row = write_table(ws, res.trials.assign(Attempt=res.trials["Attempt"].astype(str)), [
            Col("Attempt", "Attempt", width=18, align="left", bold=True),
            Col("Fires", "Fires", FMT_INT, 12),
            Col("Answered", "Answered", FMT_INT, 12),
            Col("Response rate", "Response Rate", FMT_PCT, 14, bold=True),
            Col("RR CI Lo", "RR CI Lo", FMT_PCT, 11, color=MUTED),
            Col("RR CI Hi", "RR CI Hi", FMT_PCT, 11, color=MUTED),
            Col("Detractors", "Detractors", FMT_INT, 12, color=RED),
            Col("Detractor rate", "Detractor Rate", FMT_PCT, 14),
            Col("tNPS", "tNPS", FMT_DEC1, 11),
        ], 4)
        row = add_note(ws, row + 1, (
            f"The cap is {cfg.firing.max_trials_per_cycle} attempts per cycle. If a later "
            "attempt converts at a fraction of the first and its respondents score no "
            "differently, the extra call is buying noise and annoyance; if it converts "
            "well, the cap is costing coverage. Intervals are Wilson, so a thin attempt "
            "band shows as a wide bar rather than a false precision."), 9)
        add_image(ws, png_trials, row)

    # ── 5. DAILY VOLUME ─────────────────────────────────────────────────────
    ws = new_sheet("Daily Survey Volume", "Fired / answered per day vs baseline")
    sheet_title(ws, "Surveys fired by day", 13, period)
    row = write_table(ws, res.daily, [
        Col("Date", "Survey Date", FMT_DATE, 13, is_date=True),
        Col("Day", "Day Name", width=7),
        Col("Fires", "Fires", FMT_INT, 10, bold=True),
        Col("Answered", "Answered", FMT_INT, 11),
        Col("Completed", "Completed", FMT_INT, 11),
        Col("Response %", "Response Rate", FMT_PCT, 12),
        Col("Compliance %", "Compliance Rate", FMT_PCT, 13),
        Col("Baseline", "Fires Base", FMT_DEC1, 12, color=MUTED),
        Col("vs Baseline", "Fires vs Base", FMT_VAR, 12, neg_red=True),
        Col("vs Base %", "Fires vs Base %", FMT_VARP, 12, neg_red=True),
        Col("Z", "Fires Z", FMT_DEC1, 9),
        Col("t crit", "Fires T Crit", FMT_DEC1, 9, color=MUTED),
        Col("Flag", "Fires Flag", width=22, flag=True),
    ], 4)
    row = add_note(ws, row + 1, (
        f"Baseline mode: {cfg.analysis.baseline_mode}. A same-weekday baseline compares "
        "Monday with Mondays. A plain 7-day mean is weekday-NEUTRAL, so on a normal "
        "weekly cycle it marks every weekend 'below average' and inflates the rolling "
        "SD so far that a real weekday outage stops being significant. Days with no "
        "records are shown but excluded from every baseline - an absence of data is not "
        "an observation of zero. The Z column is tested against the t critical value "
        "for the number of observations actually behind the baseline, not a flat 1.96."), 13)
    row = add_image(ws, png_volume, row)
    add_image(ws, png_weekday, row)

    # ── 6. DAILY DETRACTORS ─────────────────────────────────────────────────
    ws = new_sheet("Daily Detractors", "Detractor rate over ANSWERED, with a spike test")
    sheet_title(ws, "Detractors by day - over answered, not completed", 13, period)
    row = write_table(ws, res.daily, [
        Col("Date", "Survey Date", FMT_DATE, 13, is_date=True),
        Col("Day", "Day Name", width=7),
        Col("Answered", "Answered", FMT_INT, 11),
        Col("Detractors", "Detractors", FMT_INT, 11, bold=True, color=RED),
        Col("Detractor %", "Detractor Rate", FMT_PCT, 12, bold=True),
        Col("CI Lo", "Det CI Lo", FMT_PCT, 10, color=MUTED),
        Col("CI Hi", "Det CI Hi", FMT_PCT, 10, color=MUTED),
        Col("Baseline %", "Det Rate Base", FMT_PCT, 12, color=MUTED),
        Col("vs Baseline", "Det Rate vs Base", FMT_VARP, 12, pos_bad=True),
        Col("Z", "Det Z", FMT_DEC1, 9),
        Col("p", "Det p", FMT_P, 9),
        Col("q (FDR)", "Det q", FMT_P, 10, color=MUTED),
        Col("Flag", "Det Flag", width=22, flag=True),
    ], 4)
    add_note(ws, row + 1, (
        "The baseline pools the prior comparable days (sum of detractors / sum of "
        "answered), never the mean of daily rates - pooling weights each day by its own "
        "volume, which is what a proportion test requires. CI is Wilson, reliable at "
        "small n. q is the Benjamini-Hochberg false-discovery rate across all days: at "
        "alpha 0.05 over a 30-day month, roughly one 'significant' day is expected from "
        "noise alone, and the q column is what keeps that in view."), 13)

    # ── 7. DAILY tNPS ───────────────────────────────────────────────────────
    ws = new_sheet("Daily tNPS", "tNPS per day vs baseline, with 95% CI and MDE")
    sheet_title(ws, "tNPS by day", 15, period)
    row = write_table(ws, res.daily, [
        Col("Date", "Survey Date", FMT_DATE, 13, is_date=True),
        Col("Day", "Day Name", width=7),
        Col("Answered", "Answered", FMT_INT, 11),
        Col("Promoters", "Promoters", FMT_INT, 11),
        Col("Passives", "Passives", FMT_INT, 10),
        Col("Detractors", "Detractors", FMT_INT, 11, color=RED),
        Col("tNPS", "tNPS", FMT_DEC1, 10, bold=True),
        Col("CI Lo", "tNPS CI Lo", FMT_DEC1, 10, color=MUTED),
        Col("CI Hi", "tNPS CI Hi", FMT_DEC1, 10, color=MUTED),
        Col("Baseline", "tNPS Base", FMT_DEC1, 12, color=MUTED),
        Col("vs Baseline", "tNPS vs Base", FMT_VAR, 12, neg_red=True),
        Col("MDE (pts)", "tNPS MDE", FMT_DEC1, 11, color=MUTED),
        Col("Z", "tNPS Z", FMT_DEC1, 9),
        Col("p", "tNPS p", FMT_P, 9),
        Col("Flag", "tNPS Flag", width=22, flag=True),
    ], 4)
    row = add_note(ws, row + 1, (
        "tNPS is a MEAN of a score in {+1, 0, -1}, so its exact variance is "
        "(p_prom + p_det) - (p_prom - p_det)^2 and the CI uses that, not a percentage "
        "approximation. Below n=50 the proportions carry add-1/2 smoothing, so a day "
        "where everyone happened to be a promoter still shows honest uncertainty "
        "instead of a zero-width interval. MDE is the smallest gap this day's sample "
        "could have detected: where the flag says 'not significant' and the MDE is "
        "large, the day was too small to tell, which is not the same as fine."), 15)
    add_image(ws, png_tnps, row)

    # ── 8. DAY IMPACT ON THE MONTH ──────────────────────────────────────────
    if not res.day_impact.empty:
        ws = new_sheet("Day Impact on Month", "Which days moved the monthly tNPS")
        sheet_title(ws, "Day-level impact on monthly tNPS", 13, period)
        row = write_table(ws, res.day_impact, [
            Col("Month", "Month", width=12, align="left"),
            Col("Date", "Survey Date", FMT_DATE, 13, is_date=True),
            Col("Day", "Day Name", width=7),
            Col("Answered", "Answered", FMT_INT, 11),
            Col("Weight", "Weight", FMT_PCT, 10),
            Col("Day tNPS", "tNPS", FMT_DEC1, 11),
            Col("Month tNPS", "Month tNPS", FMT_DEC1, 12, color=MUTED),
            Col("Gap", "Gap vs Total", FMT_VAR, 11, neg_red=True),
            Col("Impact (pts)", "Impact (pts)", FMT_VAR, 13, bold=True, neg_red=True),
            Col("tNPS Excl.", "tNPS Excl.", FMT_DEC1, 12, color=MUTED),
            Col("LOO (pts)", "LOO (pts)", FMT_VAR, 12),
            Col("Det excess", "Detractor Excess", FMT_DEC1, 12, pos_bad=True),
            Col("Significance", "Significance", width=26, align="left", color=MUTED),
        ], 4)
        add_image(ws, png_day_impact, row + 1)

    # ── 9. SHORT CODE PARETO ────────────────────────────────────────────────
    ws = new_sheet("Short Codes by Detractors", "Detractor count per code + Pareto")
    sheet_title(ws, "SHORT_CODE - detractor count and Pareto", 14, period)
    row = write_table(ws, res.sc_det, [
        Col("Short Code", "SHORT_CODE", width=14, align="left", bold=True),
        Col("Topic / Reason", "Topic / Reason", width=26, align="left"),
        Col("Category", "Category", width=18, align="left"),
        Col("Owner", "Owner", width=14, align="left"),
        Col("Answered", "Answered", FMT_INT, 11),
        Col("Detractors", "Detractors", FMT_INT, 11, bold=True, color=RED),
        Col("Detractor %", "Detractor Rate", FMT_PCT, 12),
        Col("CI Lo", "Rate CI Lo", FMT_PCT, 10, color=MUTED),
        Col("CI Hi", "Rate CI Hi", FMT_PCT, 10, color=MUTED),
        Col("tNPS", "tNPS", FMT_DEC1, 10),
        Col("Share of det.", "Share of Detractors", FMT_PCT, 13),
        Col("Cumulative %", "Cumulative %", FMT_PCT, 13, bold=True),
        Col("Severe %", "Severe %", FMT_PCT, 11),
        Col("Pareto", "Pareto", width=12),
    ], 4)
    pareto_n = int((res.sc_det["Cumulative %"] <= cfg.analysis.pareto_target).sum()) + 1
    row = add_note(ws, row + 1, (
        f"{min(pareto_n, len(res.sc_det))} of {len(res.sc_det)} short codes carry "
        f"{cfg.analysis.pareto_target:.0%} of all detractors. Detractor % divides by that "
        "code's own answered base, so a small code with a high rate stays visible - check "
        "the Wilson interval before acting on it."), 14)
    add_image(ws, png_pareto, row)

    # ── 10. IMPACT SHEETS ───────────────────────────────────────────────────
    impact_note = (
        "Impact (pts) = Weight x Gap vs Total - additive, closes on zero, feeds a "
        "waterfall. LOO (pts) = period tNPS without this group minus period tNPS - the "
        "counterfactual. LOO = -Impact / (1 - Weight), so the ranking is identical; use "
        "Impact to apportion the gap and LOO to brief an executive. Significance is "
        "FDR-corrected across every group in this table, because a hundred tests at "
        "alpha 0.05 produce about five false positives on their own.")

    def impact_sheet(name: str, purpose: str, title: str, table: pd.DataFrame,
                     label_col: str, png=None):
        if table.empty:
            return
        ws = new_sheet(name, purpose)
        sheet_title(ws, title, 14, period)
        total = {label_col: "TOTAL (Impact must close on 0)",
                 "Answered": int(table["Answered"].sum()),
                 "Weight": float(table["Weight"].sum()),
                 "Detractors": int(table["Detractors"].sum()),
                 "Impact (pts)": float(table["Impact (pts)"].sum())}
        row = write_table(ws, table, [
            Col(label_col, label_col, width=36, align="left", bold=True),
            Col("Answered", "Answered", FMT_INT, 11),
            Col("Weight", "Weight", FMT_PCT, 10),
            Col("Detractors", "Detractors", FMT_INT, 11, color=RED),
            Col("Detractor %", "Detractor Rate", FMT_PCT, 12),
            Col("tNPS", "tNPS", FMT_DEC1, 10),
            Col("Gap vs Total", "Gap vs Total", FMT_VAR, 12, neg_red=True),
            Col("Impact (pts)", "Impact (pts)", FMT_VAR, 13, bold=True, neg_red=True),
            Col("tNPS Excl.", "tNPS Excl.", FMT_DEC1, 12, color=MUTED),
            Col("LOO (pts)", "LOO (pts)", FMT_VAR, 12),
            Col("Det excess", "Detractor Excess", FMT_DEC1, 12, pos_bad=True),
            Col("p", "p-value", FMT_P, 9, color=MUTED),
            Col("q (FDR)", "q-value", FMT_P, 10, color=MUTED),
            Col("Significance", "Significance", width=26, align="left", color=MUTED),
        ], 4, total_row=total)
        row = add_note(ws, row + 1, impact_note, 14)
        add_image(ws, png, row)

    impact_sheet("SC tNPS Impact", "Which SHORT_CODEs drive period tNPS",
                 "SHORT_CODE impact on tNPS", res.sc_impact, "Short Code", png_sc)
    impact_sheet("Topic tNPS Impact", "Same engine at Topic grain",
                 "Topic impact on tNPS", res.topic_impact, "Topic", png_topic)
    impact_sheet("Type tNPS Impact", "SR vs Activity",
                 "Request Type impact on tNPS", res.type_impact, "Request Type")
    impact_sheet("Segment tNPS Impact", "Same engine at segment grain",
                 "Segment impact on tNPS", res.seg_impact, "Segment")

    # ── 11. MoM BRIDGE ──────────────────────────────────────────────────────
    if not res.bridge.empty:
        ws = new_sheet("MoM Bridge", "Why tNPS moved: mix vs rate")
        sheet_title(ws, f"tNPS bridge {res.bridge.attrs.get('prev')} -> "
                        f"{res.bridge.attrs.get('now')}", 10, period)
        row = kpi_block(ws, 4, [
            (f"tNPS {res.bridge.attrs.get('prev')}", res.bridge.attrs.get("tnps_prev"),
             FMT_DEC1, INK),
            (f"tNPS {res.bridge.attrs.get('now')}", res.bridge.attrs.get("tnps_now"),
             FMT_DEC1, INK),
            ("Change", res.bridge.attrs.get("tnps_now", 0) -
             res.bridge.attrs.get("tnps_prev", 0), FMT_VAR,
             RED if res.bridge.attrs.get("tnps_now", 0) <
             res.bridge.attrs.get("tnps_prev", 0) else POS),
            ("of which MIX", float(res.bridge["Mix (pts)"].sum()), FMT_VAR, GRAPHITE),
            ("of which RATE", float(res.bridge["Rate (pts)"].sum()), FMT_VAR, GRAPHITE),
        ])
        total = {"SC Label": "TOTAL",
                 "Mix (pts)": float(res.bridge["Mix (pts)"].sum()),
                 "Rate (pts)": float(res.bridge["Rate (pts)"].sum()),
                 "Total (pts)": float(res.bridge["Total (pts)"].sum())}
        row = write_table(ws, res.bridge, [
            Col("Short Code", "SC Label", width=36, align="left", bold=True),
            Col("Answered prev", "Answered Prev", FMT_INT, 13),
            Col("Answered now", "Answered Now", FMT_INT, 13),
            Col("Weight prev", "Weight Prev", FMT_PCT, 12),
            Col("Weight now", "Weight Now", FMT_PCT, 12),
            Col("Weight change", "Weight Change", FMT_VARP, 13),
            Col("tNPS prev", "tNPS Prev", FMT_DEC1, 11),
            Col("tNPS now", "tNPS Now", FMT_DEC1, 11),
            Col("tNPS change", "tNPS Change", FMT_VAR, 12, neg_red=True),
            Col("Mix (pts)", "Mix (pts)", FMT_VAR, 12, neg_red=True),
            Col("Rate (pts)", "Rate (pts)", FMT_VAR, 12, neg_red=True),
            Col("Total (pts)", "Total (pts)", FMT_VAR, 12, bold=True, neg_red=True),
        ], row, total_row=total)
        row = add_note(ws, row + 1, (
            "MIX is the tNPS change caused purely by volume moving between codes - the "
            "codes performed the same, but more answers came from the weaker ones. RATE "
            "is the change caused by codes genuinely performing differently. A bare "
            "month-over-month delta cannot separate the two, and they call for opposite "
            "responses: fix the routing, or fix the service. Mix and Rate use the "
            "symmetric split, so together they equal the whole change with no residual "
            "term; the Laspeyres components are available in the frame for anyone "
            "reconciling against an older bridge."), 12)
        add_image(ws, png_bridge, row)

    # ── 12. POCKETS ─────────────────────────────────────────────────────────
    if not res.pockets.empty:
        ws = new_sheet("Pockets (SC x Segment)", "Problems a one-way view dilutes")
        sheet_title(ws, "Two-way pockets - short code inside segment", 11, period)
        row = write_table(ws, res.pockets, [
            Col("Short Code", "SC Label", width=34, align="left", bold=True),
            Col("Segment", cfg.columns.segment_col, width=24, align="left"),
            Col("Answered", "Answered", FMT_INT, 11),
            Col("Weight", "Weight", FMT_PCT, 10),
            Col("Detractors", "Detractors", FMT_INT, 11, color=RED),
            Col("Detractor %", "Detractor Rate", FMT_PCT, 12),
            Col("tNPS", "tNPS", FMT_DEC1, 10),
            Col("Overall tNPS", "Overall tNPS", FMT_DEC1, 12, color=MUTED),
            Col("Gap", "Gap vs Total", FMT_VAR, 11, neg_red=True),
            Col("Impact (pts)", "Impact (pts)", FMT_VAR, 13, bold=True, neg_red=True),
            Col("Significance", "Significance", width=26, align="left", color=MUTED),
        ], 4)
        add_note(ws, row + 1, (
            f"Only cells with at least {cfg.analysis.pocket_min_n} answers are shown. A "
            "problem confined to one short code inside one segment is averaged away in "
            "both the by-code and the by-segment tables; crossing them is the only view "
            "that shows it."), 11)

    # ── 13. SC x DAY DRIVERS ────────────────────────────────────────────────
    if not res.sc_day.empty:
        ws = new_sheet("SC x Day Drivers", f"Top {cfg.analysis.top_n_per_day} drags per day")
        sheet_title(ws, "What dragged each day", 11, period)
        row = write_table(ws, res.sc_day, [
            Col("Date", "Survey Date", FMT_DATE, 13, is_date=True),
            Col("Rank", "Rank", FMT_INT, 8),
            Col("Short Code", "Short Code", width=34, align="left"),
            Col("Answered", "Answered", FMT_INT, 11),
            Col("Weight", "Weight", FMT_PCT, 10),
            Col("Code tNPS", "tNPS", FMT_DEC1, 11),
            Col("Day tNPS", "Day tNPS", FMT_DEC1, 11, color=MUTED),
            Col("Gap", "Gap vs Total", FMT_VAR, 11, neg_red=True),
            Col("Impact (pts)", "Impact (pts)", FMT_VAR, 13, bold=True, neg_red=True),
            Col("LOO (pts)", "LOO (pts)", FMT_VAR, 12),
            Col("Significance", "Significance", width=26, align="left", color=MUTED),
        ], 4)
        add_note(ws, row + 1, (
            "Impact here is measured against THAT DAY's tNPS, not the period's - the "
            f"question is what pulled the day away from itself. Days under "
            f"{cfg.analysis.min_n_day} answers are skipped: at that volume one response "
            "can reorder the ranking."), 11)

    # ── 14. MONTHLY ─────────────────────────────────────────────────────────
    ws = new_sheet("Monthly", "Month roll-up of every headline metric")
    sheet_title(ws, "Monthly summary", 10, period)
    write_table(ws, res.monthly, [
        Col("Month", "Month", width=14, align="left", bold=True),
        Col("Fires", "Fires", FMT_INT, 12),
        Col("Answered", "Answered", FMT_INT, 12),
        Col("Response %", "Response Rate", FMT_PCT, 12),
        Col("Promoters", "Promoters", FMT_INT, 12),
        Col("Passives", "Passives", FMT_INT, 12),
        Col("Detractors", "Detractors", FMT_INT, 12, color=RED),
        Col("Detractor %", "Detractor Rate", FMT_PCT, 12),
        Col("tNPS", "tNPS", FMT_DEC1, 11, bold=True),
        Col("MoM", "tNPS MoM", FMT_VAR, 11, neg_red=True),
    ], 4)

    # ── 15. FACT TABLE ──────────────────────────────────────────────────────
    _write_fact_sheet(wb, res, new_sheet)

    # ── 16. RUN INFO & DATA QUALITY ─────────────────────────────────────────
    _write_run_info(wb, res, new_sheet, period, run_ts)

    # ── 17. GLOSSARY ────────────────────────────────────────────────────────
    _write_glossary(wb, res, new_sheet, period)

    # ── 0. CONTENTS, built last and moved to the front ──────────────────────
    ws = wb.create_sheet("Contents", 0)
    ws.sheet_properties.tabColor = RED
    sheet_title(ws, "Contents", 3, period)
    for i, width in enumerate([6, 32, 78], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    header_row(ws, 4, ["#", "Sheet", "What it answers"])
    row = 5
    for i, (name, purpose) in enumerate(sheets, 1):
        put(ws, row, 1, i)
        cell = ws.cell(row=row, column=2, value=name)
        cell.font = _font(10, True, "0563C1")
        cell.hyperlink = f"#'{name}'!A1"
        cell.border = _BORDER
        cell.alignment = Alignment(horizontal="left", vertical="center")
        put(ws, row, 3, purpose, align="left", color=SLATE)
        row += 1

    return _save(wb, out_path)


def _write_fact_sheet(wb, res: Results, new_sheet) -> None:
    """Row-level answered surveys as an Excel Table.

    Written with append() and a table style rather than a Font object per cell:
    at 100k rows x 25 columns the per-cell styling path builds 2.5M style objects
    and turns a report into a coffee break.
    """
    cfg = res.cfg
    wanted = ["Survey Date", "Fire_TS", "Fire Hour", "Day Name", "ISO Week",
              "Month Name", "Request Type", "SR Digits", cfg.columns.short_code,
              "Topic / Reason", "Category", "Owner", cfg.columns.agent_queue,
              *cfg.columns.map_levels, "NPS Category", "Detractor Severity",
              cfg.columns.score, "Is Detractor", "Is Passive", "Is Promoter",
              "Is Completed", "Trial_Index", "Days_Since_Response", "Violations",
              "Is_Compliant"]
    cols = [c for c in wanted if c in res.base.columns]
    fact = res.base[cols].copy()
    limit = 1_000_000
    truncated = len(fact) > limit
    if truncated:
        fact = fact.head(limit)
    fact = fact.rename(columns={cfg.columns.score: "TNPS Score",
                                "Fire_TS": "Fired At"})

    ws = new_sheet("Fact (Answered)",
                   "Row-level answered surveys - Insert > PivotTable / Slicer",
                   tab=MUTED)
    ws.sheet_view.showGridLines = False
    ws.append([str(c) for c in fact.columns])
    for cell in ws[1]:
        cell.font = _font(9, True, WHITE)
        cell.fill = _fill(INK)
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Dates stay REAL dates. Writing them as text is what stops a PivotTable
    # grouping by month or filtering a date range.
    date_cols = {i for i, c in enumerate(fact.columns, 1)
                 if c in ("Survey Date", "Fired At")}
    for record in fact.itertuples(index=False, name=None):
        ws.append([
            (None if (isinstance(v, float) and pd.isna(v))
             else v.to_pydatetime() if isinstance(v, pd.Timestamp)
             else int(v) if isinstance(v, np.integer)
             else float(v) if isinstance(v, np.floating)
             else bool(v) if isinstance(v, np.bool_)
             else v)
            for v in record])
    for idx in date_cols:
        letter = get_column_letter(idx)
        fmt = FMT_DATETIME if fact.columns[idx - 1] == "Fired At" else FMT_DATE
        for cell in ws[letter][1:]:
            cell.number_format = fmt
    if len(fact):
        ref = f"A1:{get_column_letter(len(fact.columns))}{len(fact) + 1}"
        table = Table(displayName="FactAnswered", ref=ref)
        table.tableStyleInfo = TableStyleInfo(name="TableStyleLight1",
                                              showRowStripes=True)
        ws.add_table(table)
    for i in range(1, len(fact.columns) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14
    ws.freeze_panes = "A2"
    if truncated:
        res.notes.add("fact sheet truncated",
                      f"{limit:,} rows written; Excel cannot hold more")


def _write_run_info(wb, res: Results, new_sheet, period: str,
                    run_ts: dt.datetime) -> None:
    """Files, settings, and every check that must pass before the numbers are used."""
    cfg, t = res.cfg, res.totals
    ws = new_sheet("Run Info & DQ", "Files, settings, rules and data-quality warnings",
                   tab=MUTED)
    sheet_title(ws, "Run info & data quality", 4, period)
    for i, width in enumerate([46, 44, 16, 46], 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    row = 4
    ws.cell(row=row, column=1, value="Files loaded").font = _font(11, True, INK)
    row += 1
    header_row(ws, row, ["File", "Rows", "", ""])
    row += 1
    for name, count in res.files_loaded:
        put(ws, row, 1, name, align="left")
        put(ws, row, 2, count, FMT_INT)
        put(ws, row, 3, ""); put(ws, row, 4, "")
        row += 1
    row += 1

    ws.cell(row=row, column=1, value="Settings in force").font = _font(11, True, INK)
    row += 1
    header_row(ws, row, ["Setting", "Value", "", ""])
    row += 1
    settings: list[tuple[str, Any]] = [
        ("Analysis base", "Answered (Q1 answered) - NOT completed surveys"),
        ("Baseline mode", cfg.analysis.baseline_mode),
        ("Baseline window", f"{cfg.analysis.same_weekday_window} same-weekday obs"
         if cfg.analysis.baseline_mode == "same_weekday"
         else f"{cfg.analysis.roll_window} calendar days"),
        ("Zero-record days", "shown, and excluded from every baseline"),
        ("Significance level", cfg.analysis.alpha),
        ("Multiple-comparison control",
         "Benjamini-Hochberg FDR" if cfg.analysis.fdr_correction else "none"),
        ("Min answered/day for a daily test", cfg.analysis.min_n_day),
        ("Min answered for a group impact", cfg.analysis.min_n_group),
        ("Variance floor (add-1/2 below n)", cfg.analysis.variance_floor_n),
        ("Subscriber column", res.df.attrs.get("subscriber_col") or "not found"),
        ("SR column", res.df.attrs.get("sr_col") or "not found"),
        ("Request Type rule",
         f"{cfg.request_type.rule} > {cfg.request_type.threshold} -> "
         f"{cfg.request_type.label_hi}, else {cfg.request_type.label_lo}"),
        ("Pareto target", f"{cfg.analysis.pareto_target:.0%}"),
        ("Script version", SCRIPT_VERSION),
        ("Source checksum", f"sha256:{res.inputs.get('digest', '')}"),
        ("Run at", run_ts.strftime("%Y-%m-%d %H:%M")),
    ]
    for label, value in [*cfg.firing.describe(), *settings]:
        put(ws, row, 1, label, align="left")
        put(ws, row, 2, str(value), align="left", color=SLATE)
        put(ws, row, 3, ""); put(ws, row, 4, "")
        row += 1
    row += 1

    ws.cell(row=row, column=1, value="Data quality").font = _font(11, True, INK)
    row += 1
    header_row(ws, row, ["Check", "Result", "Severity", "Action"])
    row += 1
    attrs = res.df.attrs
    unmatched_codes = attrs.get("unmatched_codes", [])
    checks: list[tuple[str, Any, str, str]] = [
        ("Rows with an unreadable date", attrs.get("dropped_no_date", 0),
         "high" if attrs.get("dropped_no_date") else "ok", "dropped before analysis"),
        ("Q1 score outside 0-10", attrs.get("out_of_range", 0),
         "high" if attrs.get("out_of_range") else "ok",
         "excluded from the base AND from the NPS category"),
        ("Duplicate records across files", attrs.get("duplicate_records", 0),
         "high" if attrs.get("duplicate_records") else "ok",
         "overlapping exports double-count - check the file list above"),
        ("Rows whose queue is not in the mapping", attrs.get("unmatched_queue_rows", 0),
         "high" if attrs.get("unmatched_queue_rows") else "ok", "shown as 'Unmapped'"),
        ("SHORT_CODEs missing from the mapping", len(unmatched_codes),
         "medium" if unmatched_codes else "ok", "add them to the mapping file"),
        ("Calendar days with zero records", len(res.missing_days),
         "medium" if res.missing_days else "ok",
         "shown in the daily tables, excluded from every baseline"),
        ("Fires breaking a dispatch rule", t["fires"] - t["compliant"],
         "high" if (t["fires"] - t["compliant"]) else "ok",
         "see the Firing Compliance sheet"),
        ("Answered but never completed", t["answered_not_completed"],
         "info" if t["has_completion_flag"] else "n/a",
         "included here - a completed-only base would drop them"
         if t["has_completion_flag"] else "no completion flag in the export"),
        ("Detractors answered but not completed", t["hidden_detractors"],
         "info" if t["has_completion_flag"] else "n/a",
         f"{t['hidden_detractor_share']:.1%} of all detractors"
         if t["has_completion_flag"] else "no completion flag in the export"),
    ]
    for check, result, severity, action in checks:
        fill = RED_TINT if severity == "high" else (
            GOLD_TINT if severity == "medium" else None)
        colour = RED if severity == "high" else (GOLD_DARK if severity == "medium" else MUTED)
        put(ws, row, 1, check, align="left")
        put(ws, row, 2, result, FMT_INT)
        put(ws, row, 3, severity, fill=fill, color=colour)
        put(ws, row, 4, action, align="left", color=MUTED)
        row += 1

    if res.notes.entries:
        row += 1
        ws.cell(row=row, column=1, value="Load notes").font = _font(11, True, INK)
        row += 1
        header_row(ws, row, ["What happened", "Detail", "", ""])
        row += 1
        for category, message in res.notes.entries:
            put(ws, row, 1, category, align="left")
            put(ws, row, 2, message, align="left", color=SLATE)
            put(ws, row, 3, ""); put(ws, row, 4, "")
            row += 1

    if unmatched_codes:
        row += 1
        ws.cell(row=row, column=1,
                value="Unmatched SHORT_CODEs (first 40)").font = _font(10, True, INK)
        row += 1
        put(ws, row, 1, ", ".join(map(str, unmatched_codes[:40])), align="left",
            color=SLATE)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        row += 2
    unmapped_queues = attrs.get("unmatched_queues", [])
    if unmapped_queues:
        ws.cell(row=row, column=1,
                value="Top unmapped agent queues").font = _font(10, True, INK)
        row += 1
        header_row(ws, row, ["Agent Queue", "Rows", "", ""])
        row += 1
        for queue, count in unmapped_queues:
            put(ws, row, 1, queue, align="left")
            put(ws, row, 2, count, FMT_INT)
            put(ws, row, 3, ""); put(ws, row, 4, "")
            row += 1
        row += 1
    if res.missing_days:
        ws.cell(row=row, column=1, value="Days with zero records").font = _font(10, True, INK)
        row += 1
        put(ws, row, 1, ", ".join(pd.Timestamp(d).strftime("%d-%b")
                                  for d in res.missing_days[:40]),
            align="left", color=SLATE)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)


def _write_glossary(wb, res: Results, new_sheet, period: str) -> None:
    cfg = res.cfg
    ws = new_sheet("Glossary", "Every metric with its exact formula", tab=MUTED)
    sheet_title(ws, "Glossary - definitions and formulas", 3, period)
    for i, width in enumerate([28, 66, 46], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    header_row(ws, 4, ["Metric", "Definition", "Formula"])
    row = 5
    entries = [
        ("Fire", "One raw row = one outbound IVR record. The widest base available.",
         "COUNT(rows)"),
        ("Answered", "Q1 was answered. Completion is NOT required. This is the "
         "denominator for every detractor and tNPS figure here.",
         "COUNT(score IS NOT NULL)"),
        ("Response Rate", "Share of fired surveys that answered Q1.", "Answered / Fires"),
        ("Detractor", "Answered with a score of 0-6.", "0 <= score <= 6"),
        ("tNPS", "Net score on the answered base, -100..+100.",
         "100 x (Promoters - Detractors) / Answered"),
        ("Rule 1 - cooldown",
         f"A customer who ANSWERED must not be surveyed again within "
         f"{cfg.firing.response_cooldown_days:g} days.",
         "days since that subscriber's last answer >= cooldown"),
        ("Rule 2 - trial cap",
         f"A customer who does not answer gets at most "
         f"{cfg.firing.max_trials_per_cycle} attempts. A cycle ends on an answer "
         f"or after a gap of {cfg.firing.trial_cycle_gap_hours}h.",
         "attempt index within cycle <= cap"),
        ("Rule 3 - firing window",
         f"Surveys fire only between {cfg.firing.window_label()}.",
         "window_start <= fire time <= window_end"),
        ("Trial Index", "Which attempt this fire is inside its cycle.",
         "cumulative count within (subscriber, cycle)"),
        ("Baseline", "The comparable prior days, EXCLUDING the day being tested. "
         "Same-weekday by default: a 7-day mean is weekday-neutral, so it flags "
         "every weekend and hides real weekday outages.",
         "mean / pooled sum over prior same-weekday days"),
        ("BELOW - significant", f"Below baseline AND surviving the test at "
         f"alpha = {cfg.analysis.alpha}, after FDR correction.",
         "q < alpha AND value < baseline"),
        ("MDE", "The smallest tNPS gap this day's sample could have detected. "
         "A large MDE with a 'not significant' flag means too small to tell.",
         "(z_a + z_b) x 100 x sqrt(Var/n)"),
        ("q-value", "Benjamini-Hochberg false discovery rate. With ~100 tests at "
         "alpha 0.05, about five 'significant' results are expected by chance; q "
         "is the share of the flagged set expected to be false.",
         "BH step-up on the p-values"),
        ("Wilson CI", "Interval for a proportion that stays valid at small n.",
         "see Wilson (1927)"),
        ("tNPS variance", "tNPS is a mean of a score in {+1, 0, -1}, so its "
         "variance is exact; below n=50 add-1/2 smoothing keeps a degenerate "
         "group from reporting zero uncertainty.",
         "(p_prom + p_det) - (p_prom - p_det)^2"),
        ("Weight (w)", "The group's share of answered surveys.", "n_group / n_total"),
        ("Impact (pts)", "The group's additive contribution to overall tNPS. The "
         "column sums to zero - that is the closure check.",
         "w x (tNPS_group - tNPS_total)"),
        ("LOO (pts)", "The counterfactual: overall tNPS if the group did not exist.",
         "tNPS_excluding_group - tNPS_total"),
        ("Impact vs LOO", "Algebraically linked, so they rank identically. Impact "
         "apportions the existing gap; LOO answers 'what if this went away'.",
         "LOO = -Impact / (1 - w)"),
        ("Mix (MoM bridge)", "tNPS change caused purely by volume moving between "
         "groups - the groups performed the same. Symmetric split, so Mix + Rate "
         "equals the whole change with no residual to explain.",
         "SUM dw x (tNPS_prev + tNPS_now) / 2"),
        ("Rate (MoM bridge)", "tNPS change caused by groups genuinely performing "
         "differently.", "SUM (w_prev + w_now) / 2 x d tNPS"),
        ("CUSUM changepoint", "A sustained level shift. Day-vs-baseline cannot see "
         "one, because the baseline follows the new level within a week.",
         "cumulative sum of deviations vs +/- h"),
        ("Detractor Excess", "Detractors beyond what the overall rate predicts for "
         "a group of this size.", "Detractors - Answered x overall rate"),
        ("Pareto", f"Codes inside the cumulative {cfg.analysis.pareto_target:.0%} of "
         "all detractors.", "cumulative share <= target"),
    ]
    for metric, definition, formula in entries:
        put(ws, row, 1, metric, bold=True, align="left")
        put(ws, row, 2, definition, align="left")
        put(ws, row, 3, formula, align="left", color=SLATE)
        ws.row_dimensions[row].height = 30
        row += 1
    ws.freeze_panes = "A5"


def _save(wb, path: Path) -> Path:
    """Save, stepping aside when the target is open in Excel."""
    path = Path(path)
    try:
        wb.save(path)
        return path
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{dt.datetime.now():%H%M%S}{path.suffix}")
        print(f"    ! '{path.name}' is open in Excel - saving as '{alt.name}'")
        wb.save(alt)
        return alt


# ══════════════════════════════════════════════════════════════════════════════
# COMMAND LINE
# ══════════════════════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="tNPS Impact Analysis " + SCRIPT_VERSION,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", nargs="+", type=Path, help="Survey data Excel file(s)")
    p.add_argument("--queue-map", type=Path, help="Agent Queue mapping file")
    p.add_argument("--sc-map", type=Path, help="SHORT_CODE mapping file")
    p.add_argument("--out", type=Path, help="Output .xlsx path")
    p.add_argument("--config", type=Path, help="JSON file overriding any config field")
    p.add_argument("--dump-config", type=Path,
                   help="Write the settings in force to a JSON file and exit")
    # Firing rules, the settings most likely to be tuned from the command line.
    p.add_argument("--cooldown-days", type=float,
                   help="Days a customer is left alone after answering")
    p.add_argument("--max-trials", type=int,
                   help="Attempts allowed when the customer does not answer")
    p.add_argument("--fire-window", help='Allowed firing window, e.g. "10:00-21:00"')
    p.add_argument("--trial-gap-hours", type=float,
                   help="Gap that starts a new trial cycle")
    p.add_argument("--exclude-non-compliant", action="store_true",
                   help="Drop rule-breaking records from the tNPS base")
    # Analysis
    p.add_argument("--baseline", choices=("same_weekday", "trailing"),
                   help="Baseline mode (default same_weekday)")
    p.add_argument("--roll", type=int, help="Trailing window in days (trailing mode)")
    p.add_argument("--alpha", type=float, help="Significance level")
    p.add_argument("--no-fdr", action="store_true",
                   help="Report raw p-values without FDR correction")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute everything and report, but write no workbook")
    return p


def config_from_args(args: argparse.Namespace) -> Config:
    """Base config, then the JSON file, then the command line."""
    cfg = Config.from_file(args.config) if args.config else Config()
    firing, analysis = asdict(cfg.firing), asdict(cfg.analysis)

    if args.cooldown_days is not None:
        firing["response_cooldown_days"] = args.cooldown_days
    if args.max_trials is not None:
        if args.max_trials < 1:
            raise InputError("--max-trials must be at least 1")
        firing["max_trials_per_cycle"] = args.max_trials
    if args.trial_gap_hours is not None:
        firing["trial_cycle_gap_hours"] = args.trial_gap_hours
    if args.exclude_non_compliant:
        firing["exclude_non_compliant_from_base"] = True
    if args.fire_window:
        if "-" not in args.fire_window:
            raise InputError('--fire-window looks like "10:00-21:00"')
        start, end = args.fire_window.split("-", 1)
        _parse_time(start), _parse_time(end)      # validate now, fail early
        firing["window_start"], firing["window_end"] = start.strip(), end.strip()

    if args.baseline:
        analysis["baseline_mode"] = args.baseline
    if args.roll is not None:
        if args.roll < 2:
            raise InputError("--roll must be at least 2 days")
        analysis["roll_window"] = args.roll
    if args.alpha is not None:
        if not 0 < args.alpha < 1:
            raise InputError("--alpha must be between 0 and 1")
        analysis["alpha"] = args.alpha
    if args.no_fdr:
        analysis["fdr_correction"] = False

    return Config(firing=FiringRules(**firing), analysis=Analysis(**analysis),
                  columns=cfg.columns, request_type=cfg.request_type)


def pick_files_interactively() -> tuple[list[Path], Path | None, Path | None]:
    """Three-step file picker, for the double-click workflow."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise InputError(
            "tkinter is unavailable, so the file picker cannot open. Use the CLI:\n"
            "  python tnps_impact_analysis.py --data f1.xlsx --queue-map QM.xlsx"
        ) from exc

    def ask(title: str, multiple: bool = False):
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        types = [("Excel files", "*.xlsx *.xls *.xlsm"), ("All files", "*.*")]
        result = (filedialog.askopenfilenames(title=title, initialdir=BASE_FOLDER,
                                              filetypes=types) if multiple else
                  filedialog.askopenfilename(title=title, initialdir=BASE_FOLDER,
                                             filetypes=types))
        root.destroy()
        return result

    data = ask("STEP 1 of 3 - SURVEY DATA file(s) (Ctrl+Click for multiple)", True)
    if not data:
        raise InputError("No survey files selected.")
    queue = ask("STEP 2 of 3 - AGENT QUEUE MAPPING (required)")
    sc = ask("STEP 3 of 3 - SHORT_CODE MAPPING (Cancel to auto-generate)")
    return ([Path(p) for p in data], Path(queue) if queue else None,
            Path(sc) if sc else None)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_ts = dt.datetime.now()

    try:
        cfg = config_from_args(args)
    except InputError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    if args.dump_config:
        Path(args.dump_config).write_text(cfg.to_json())
        print(f"Settings written to {args.dump_config}")
        return 0

    print("=" * 78)
    print(f"  tNPS IMPACT ANALYSIS {SCRIPT_VERSION}   |   {run_ts:%d-%b-%Y %H:%M}")
    print("=" * 78)
    print(f"  Firing rules : cooldown {cfg.firing.response_cooldown_days:g}d | "
          f"max {cfg.firing.max_trials_per_cycle} trials | "
          f"window {cfg.firing.window_start}-{cfg.firing.window_end}")
    print(f"  Baseline     : {cfg.analysis.baseline_mode} | alpha "
          f"{cfg.analysis.alpha} | FDR "
          f"{'on' if cfg.analysis.fdr_correction else 'off'}")

    try:
        if args.data:
            data_paths = [p for p in args.data if Path(p).exists()]
            for missing in [p for p in args.data if not Path(p).exists()]:
                print(f"    x Not found: {missing}", file=sys.stderr)
            if not data_paths:
                raise InputError("None of the --data files exist.")
            queue_map, sc_map = args.queue_map, args.sc_map
        else:
            data_paths, queue_map, sc_map = pick_files_interactively()

        res = analyse(data_paths, queue_map, sc_map, cfg)
    except InputError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    t = res.totals
    if args.dry_run:
        print("\nDry run - nothing written.")
        print(f"  Fires {t['fires']:,} | Answered {t['answered']:,} | "
              f"tNPS {t['tnps']:.1f} | compliance {t['compliance_rate']:.1%}")
        return 0

    out = Path(args.out) if args.out else Path(
        BASE_FOLDER) / f"TNPS_Impact_Report_{run_ts:%Y%m%d_%H%M}.xlsx"
    final = build_workbook(res, out, run_ts)

    print("\n" + "=" * 78)
    print(f"  DONE -> {final}")
    print(f"  Fires {t['fires']:,} | Answered {t['answered']:,} | "
          f"Detractors {t['detractors']:,} | tNPS {t['tnps']:.1f}")
    print(f"  Firing compliance {t['compliance_rate']:.1%} "
          f"({t['fires'] - t['compliant']:,} violations)")
    if pd.notna(t["tnps_non_compliant"]):
        print(f"  tNPS compliant {t['tnps_compliant']:.1f} vs non-compliant "
              f"{t['tnps_non_compliant']:.1f}")
    if not res.changepoints.empty:
        cp = res.changepoints.iloc[0]
        print(f"  Level shift: {cp['Direction']} {cp['Shift (pts)']:+.1f} pts on "
              f"{pd.Timestamp(cp['Survey Date']):%d-%b}")
    if not res.sc_impact.empty:
        worst = res.sc_impact.nsmallest(1, "Impact (pts)").iloc[0]
        print(f"  Biggest drag: {worst['Short Code']} "
              f"({worst['Impact (pts)']:+.2f} pts, LOO {worst['LOO (pts)']:+.2f})")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
