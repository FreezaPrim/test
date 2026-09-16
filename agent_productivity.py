"""Call centre agent productivity, utilisation and performance analysis.

Reads the Micro interactions export plus an agent mapping sheet, applies the
business rules for reached ("long") calls, and produces a multi-sheet Excel
report covering agent, day, interval, queue, team and outlier views.

Usage
-----
    python agent_productivity.py FEB2026.xlsx --mapping "Agent Mapping.xlsx" \
        --out Agent_Productivity_FEB2026.xlsx

All business rules live in the CONFIG section below, and every one of them can
also be overridden on the command line::

    --thresholds "Consumer=59,Enterprise=49"   reached-call thresholds, seconds
    --targets    "Consumer=0.35,Internet=0.30" utilisation targets
    --shift-hours 7.5  --acw-minutes 3  --target 0.30

Nothing is hard-coded downstream: change a rule and every sheet follows. The
Config sheet in the output records exactly which rules produced the numbers,
along with the input file and its checksum.

Extra mapping sheets (queue groupings, contact reasons, rate plan families)
are registered in the LOOKUPS list. Add one entry per sheet; the join, the
de-duplication and the unmapped fallback are handled for you.

Definitions used consistently across every sheet
------------------------------------------------
    Reached call   Customer handle time strictly greater than the threshold
                   for that row's business type.
    AHT            Handle seconds / answered calls. Never per interaction --
                   the per-interaction figure is reported separately as
                   Avg_Handle_Per_Interaction.
    Utilisation    (Customer Dial Time + Total Duration) / scheduled seconds.
    ACW            Measured wrap time where the export carries it, falling
                   back to the flat per-reached-call assumption.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

SCRIPT_VERSION: Final[str] = "5.0"

# ---------------------------------------------------------------------------
# CONFIG - every business rule is centralised here
# ---------------------------------------------------------------------------


def _norm_key(value: object) -> str:
    """Reduce a label to a comparable form: lowercase, letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


@dataclass(frozen=True)
class Config:
    """Business rules and column bindings for the analysis."""

    # Reached / long-call thresholds in SECONDS, applied strictly greater than,
    # measured on the customer handle time column.
    reached_thresholds: dict[str, int] = field(
        default_factory=lambda: {
            "Consumer": 59,
            "Enterprise": 49,
        }
    )
    default_reached_threshold: int = 49

    # Shift and after-call-work model.
    shift_hours: float = 7.5
    acw_minutes_per_reached_call: float = 3.0
    # Prefer the export's measured wrap time over the flat assumption above.
    use_measured_acw: bool = True

    # Utilisation denominator: "shift" (fixed shift length) or "span"
    # (observed first-call to last-call window).
    utilisation_basis: str = "shift"
    # Utilisation targets per business type. The default below covers anything
    # not listed here.
    utilisation_targets: dict[str, float] = field(
        default_factory=lambda: {
            "Consumer": 0.35,
            "Enterprise": 0.33,
            "Non Telecom": 0.33,
            "Internet": 0.30,
        }
    )
    utilisation_target: float = 0.30

    # Outlier sensitivity (z-score on the agent summary).
    outlier_z: float = 2.0

    # Minimum interactions for an agent-day to be treated as a worked day.
    min_interactions_active_day: int = 1

    # --- Column bindings (rename here if the export headers change) ---------
    col_queue: str = "Queue"
    col_identifier: str = "Identifier"
    col_call_flag: str = "Call 1 - Trial 0"
    col_interval: str = "Interval"
    col_day: str = "Day"
    col_month: str = "Month"
    col_login: str = "Login ID"
    col_spv: str = "SPV"
    col_interaction_id: str = "INTERACTION_ID"
    col_start: str = "START_TS_TIME"
    col_end: str = "END_TS_TIME"
    col_segment: str = "Segment"
    col_handle_time: str = "Handle Time"
    col_total_duration: str = "Total Duration (Fmt)"
    col_customer_handle: str = "Customer Handle Time (Fmt)"
    col_customer_dial: str = "Customer Dial Time (Fmt)"
    col_customer_hold: str = "Customer Hold Time (Fmt)"
    col_customer_wrap: str = "Customer Wrap Time (Fmt)"
    col_queue_time: str = "QUEUE_TIME"

    # --- Mapping sheet bindings --------------------------------------------
    map_key: str = "Login ID"
    map_agent_name: str = "Agent"
    map_team: str = "Team"
    map_business_type: str = "Business Type"
    map_spv: str = "SPV"

    # Fallback: if the mapping has no Business Type for an agent, infer it from
    # the queue name. These are REGULAR EXPRESSIONS matched against the queue
    # name with punctuation flattened to spaces, so short abbreviations can be
    # anchored with \b. Never use a bare substring like "ent" here: it matches
    # Retention, Payments and Contact Centre.
    queue_business_type_patterns: dict[str, str] = field(
        default_factory=lambda: {
            r"consumer|\bcons\b|\bb2c\b": "Consumer",
            r"enterprise|\bent\b|\bsme\b|\bb2b\b|\bcorporate\b": "Enterprise",
            r"internet|\badsl\b|fib(?:er|re)|\bftth\b": "Internet",
            r"non\s?telecom|\bnt\b": "Non Telecom",
        }
    )
    default_business_type: str = "Other"

    def threshold_for(self, business_type: object) -> int:
        """Return the reached-call threshold in seconds for a business type.

        Matching is case and punctuation insensitive, so ``NON_TELECOM`` and
        ``Non Telecom`` resolve alike.
        """
        key = _norm_key(business_type)
        for name, value in self.reached_thresholds.items():
            if _norm_key(name) == key:
                return value
        return self.default_reached_threshold

    def target_for(self, label: object) -> float:
        """Return the utilisation target for a business type.

        Anything unrecognised falls back to ``utilisation_target``. Use
        :meth:`has_explicit_target` to tell a real match from that fallback --
        the Data Quality sheet reports the difference rather than letting a
        silent 30% skew a report.
        """
        key = _norm_key(label)
        for name, value in self.utilisation_targets.items():
            if _norm_key(name) == key:
                return value
        return self.utilisation_target

    def has_explicit_target(self, label: object) -> bool:
        """True when ``label`` is named in ``utilisation_targets``."""
        key = _norm_key(label)
        return any(_norm_key(name) == key for name in self.utilisation_targets)

    def has_explicit_threshold(self, label: object) -> bool:
        """True when ``label`` is named in ``reached_thresholds``."""
        key = _norm_key(label)
        return any(_norm_key(name) == key for name in self.reached_thresholds)


CONFIG: Final[Config] = Config()

# ---------------------------------------------------------------------------
# DEFAULTS - used when the script is started with no command line arguments
# (the Run button in VS Code, or a double click). Edit the paths below and the
# Run button works. Relative names are resolved against this file's own folder,
# so the script does not care what the working directory is. Passing arguments
# on the command line always overrides these.
# ---------------------------------------------------------------------------

DEFAULT_MICRO: Final[str] = "FEB2026.xlsx"
DEFAULT_MAPPING: Final[str] = "Agent Mapping.xlsx"
DEFAULT_OUT: Final[str] = "Agent_Productivity.xlsx"

# Column to break out into separate files, one per distinct value, alongside
# the master workbook. Set to None for a single combined file.
DEFAULT_SPLIT_BY: Final[str | None] = "Queue"

# Label given to rows whose split column is blank, so they land in a file of
# their own instead of disappearing from the slices.
BLANK_SLICE: Final[str] = "(blank)"

# e& palette used for the Excel output
BRAND_RED: Final[str] = "E00800"
BRAND_INK: Final[str] = "17181C"
BRAND_GRAPHITE: Final[str] = "3A3D45"
BRAND_LINE: Final[str] = "E6E7EA"
BRAND_SURFACE: Final[str] = "F7F7F8"
BRAND_POSITIVE: Final[str] = "1E9E62"
BRAND_WARNING: Final[str] = "C9971F"
BRAND_GOLD: Final[str] = "B4892B"


class InputError(Exception):
    """A problem with the input files that the operator has to resolve.

    Raised by the loaders and turned into a readable message by ``main`` --
    the module stays importable from a notebook instead of calling SystemExit
    from library code.
    """


class Notes:
    """Collects everything the loaders did quietly, for the Data Quality sheet.

    Header renames, auto-detected tabs and skipped lookups are all reasonable
    defaults, and all invisible the month a number looks wrong. Recording them
    costs nothing and turns "why is this column empty" into a one-line answer.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def add(self, category: str, message: str) -> None:
        self.entries.append((category, message))

    def rows(self) -> list[dict[str, Any]]:
        return [
            {"Check": f"Load note - {category}", "Value": "", "Detail": message}
            for category, message in self.entries
        ]

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(frozen=True)
class RunInfo:
    """Provenance written into the Config sheet."""

    micro_path: Path | None = None
    micro_rows: int = 0
    micro_sha: str = ""
    mapping_path: Path | None = None
    mapping_rows: int = 0


@dataclass(frozen=True)
class SliceReference:
    """Whole-period figures a per-slice file measures itself against."""

    interactions: pd.Series  # Agent_Key -> interactions across the whole period
    active_days: pd.Series   # Agent_Key -> active days across the whole period


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

_HMS_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:(\d+)\s*:\s*)?(\d{1,2})\s*:\s*(\d{1,2}(?:\.\d+)?)\s*$"
)

_INTERVAL_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(\d{1,2})\s*[:.]?\s*(\d{2})?")


def to_seconds(value: Any, *, day_fraction: bool = False) -> float:
    """Convert a duration of any common export shape into seconds.

    Handles ``hh:mm:ss`` / ``mm:ss`` strings, ``timedelta``, ``time``, Excel
    day-fractions and plain numeric seconds. Returns ``nan`` when unparsable.

    ``day_fraction`` says whether a bare number below 1 is an Excel time cell
    (a fraction of a day) or a genuine sub-second duration. That is a fact
    about the column, not the cell, so :func:`seconds_series` decides it once
    per column and passes it down. Guessing per cell turns 0.5 seconds into
    12 hours.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return float("nan")
    if value is pd.NaT:
        return float("nan")
    if isinstance(value, pd.Timedelta):
        return float(value.total_seconds())
    if isinstance(value, dt.timedelta):
        return float(value.total_seconds())
    if isinstance(value, dt.datetime):
        return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
    if isinstance(value, dt.time):
        return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
    if isinstance(value, (int, float, np.integer, np.floating)):
        num = float(value)
        if np.isnan(num):
            return float("nan")
        return num * 86400.0 if day_fraction and 0 < num < 1 else num
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "nat", "-"}:
        return float("nan")
    match = _HMS_RE.match(text)
    if match:
        hours = float(match.group(1) or 0)
        return hours * 3600 + float(match.group(2)) * 60 + float(match.group(3))
    try:
        return float(text)
    except ValueError:
        return float("nan")


def column_is_day_fraction(raw: pd.Series) -> bool:
    """True when a duration column looks like Excel time cells.

    Excel writes a time-formatted cell as a fraction of a day, so every value
    sits strictly between 0 and 1. A column holding plain seconds will almost
    always contain something >= 1. Deciding this once per column is the only
    honest way to read a bare ``0.5``.
    """
    numeric = pd.to_numeric(raw, errors="coerce").dropna()
    numeric = numeric[numeric != 0]
    if numeric.empty:
        return False
    return bool((numeric > 0).all() and (numeric < 1).all())


def seconds_series(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a duration column as float seconds, or zeros when absent."""
    if column not in frame.columns:
        return pd.Series(np.zeros(len(frame)), index=frame.index, dtype="float64")
    raw = frame[column]
    day_fraction = column_is_day_fraction(raw)
    return raw.map(lambda v: to_seconds(v, day_fraction=day_fraction)).astype("float64")


def fmt_hms(seconds: float) -> str:
    """Format seconds as ``hh:mm:ss`` for report display."""
    if pd.isna(seconds):
        return ""
    total = int(round(float(seconds)))
    sign = "-" if total < 0 else ""
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def interval_minutes(label: object) -> float:
    """Minutes past midnight for an interval label, for natural sorting.

    ``"9:00"`` must sort before ``"10:00"``; a plain string sort puts it after.
    Unparsable labels sort last.
    """
    match = _INTERVAL_RE.match(str(label))
    if not match:
        return float("inf")
    return float(match.group(1)) * 60 + float(match.group(2) or 0)


def as_text(series: pd.Series) -> pd.Series:
    """Strip a key column to text without inventing the string "nan".

    ``astype(str)`` turns a missing value into "nan" on pandas 2 and keeps it
    missing on pandas 3. Neither behaviour is relied on here: missing stays
    missing, everything else becomes stripped text.
    """
    return series.where(series.isna(), series.astype(str).str.strip())


def _nan_series(index: pd.Index) -> pd.Series:
    """An all-NaN object column, used where a source column is missing."""
    return pd.Series([np.nan] * len(index), index=index, dtype="object")


def _share(series: pd.Series) -> pd.Series:
    """Each row's share of the column total, NaN when the total is zero."""
    total = float(pd.to_numeric(series, errors="coerce").sum())
    if not total:
        return pd.Series(np.nan, index=series.index, dtype="float64")
    return pd.to_numeric(series, errors="coerce").astype("float64") / total


def _nz(series: pd.Series) -> pd.Series:
    """Coerce to float and turn zeros into NaN, for safe division."""
    numeric = pd.to_numeric(series, errors="coerce").astype("float64")
    return numeric.where(numeric != 0)


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def expected_micro_columns(cfg: Config = CONFIG) -> list[str]:
    """Every export column the analysis binds to, in Config order."""
    return [v for k, v in vars(cfg).items() if k.startswith("col_")]


def _normalise(name: object) -> str:
    """Reduce a header to a comparable form: lowercase, letters and digits only.

    ``Login ID``, ``Login_ID``, ``login id `` and ``LoginID`` all collapse to
    ``loginid``, so a header spelled differently between exports still matches.
    """
    return _norm_key(name)


def align_headers(
    frame: pd.DataFrame,
    expected: list[str],
    notes: Notes | None = None,
    source: str = "",
) -> pd.DataFrame:
    """Rename columns that match an expected header apart from spelling.

    An exact match is never touched. Only headers differing by case, spacing,
    underscores or punctuation are renamed to the expected spelling, and every
    rename is recorded in ``notes`` so the change is visible in the report.

    Two columns that normalise to the same expected header would collide into
    one name, so only the first is renamed and the clash is reported.
    """
    present = set(frame.columns)
    wanted = {_normalise(name): name for name in expected if name not in present}
    if not wanted:
        return frame

    renames: dict[Any, str] = {}
    claimed: set[str] = set()
    for col in frame.columns:
        key = _normalise(col)
        target = wanted.get(key)
        if target is None:
            continue
        if target in claimed:
            if notes is not None:
                notes.add(
                    "header clash",
                    f"{source}'{col}' also matches '{target}'; left as is to "
                    "avoid two columns with the same name",
                )
            continue
        claimed.add(target)
        renames[col] = target

    if not renames:
        return frame
    if notes is not None:
        for old, new in renames.items():
            notes.add("header renamed", f"{source}'{old}' -> '{new}'")
    return frame.rename(columns=renames)


def _read_table(path: Path, sheet: str | int = 0) -> pd.DataFrame:
    """Read a CSV or one sheet of a workbook with every value left as text."""
    if Path(path).suffix.lower() in {".csv", ".txt"}:
        return pd.read_csv(path, dtype=object)
    return pd.read_excel(path, sheet_name=sheet, dtype=object)


def load_micro(
    path: Path,
    sheet: str | int = 0,
    cfg: Config = CONFIG,
    notes: Notes | None = None,
) -> pd.DataFrame:
    """Load the Micro interactions export.

    Headers are matched tolerantly: a column spelled ``Login_ID`` or
    ``login id`` is renamed to the expected ``Login ID`` so the export does not
    have to be identical between months. Every rename is recorded.
    """
    try:
        frame = _read_table(Path(path), sheet)
    except FileNotFoundError as exc:
        raise InputError(f"Input not found: {path}") from exc
    except ValueError as exc:
        raise InputError(f"Could not read {path}: {exc}") from exc
    frame.columns = [str(c).strip() for c in frame.columns]
    frame = align_headers(frame, expected_micro_columns(cfg), notes, "export: ")

    missing = [c for c in (cfg.col_login, cfg.col_customer_handle) if c not in frame.columns]
    if missing and notes is not None:
        notes.add(
            "export column missing",
            f"{', '.join(missing)} - downstream figures fall back to their defaults",
        )
    return frame


def load_mapping(
    path: Path | None,
    sheet: str | int | None = None,
    cfg: Config = CONFIG,
    notes: Notes | None = None,
) -> pd.DataFrame:
    """Load the agent mapping sheet, or an empty frame when not supplied.

    When no sheet is named, every sheet in the workbook is scanned and the
    first one carrying the key column is used. A workbook whose first tab is an
    instructions or lookup page therefore still loads correctly.
    """
    expected = [cfg.map_key, cfg.map_agent_name, cfg.map_team,
                cfg.map_business_type, cfg.map_spv]

    if path is None:
        return pd.DataFrame(columns=expected)

    path = Path(path)
    if path.suffix.lower() in {".csv", ".txt"}:
        frame = pd.read_csv(path, dtype=object)
        frame.columns = [str(c).strip() for c in frame.columns]
        frame = align_headers(frame, expected, notes, "mapping: ")
        if cfg.map_key not in frame.columns:
            raise InputError(
                f"Could not find the column '{cfg.map_key}' in {path}.\n"
                f"Columns found: {', '.join(map(str, frame.columns))}"
            )
    else:
        book = pd.ExcelFile(path)
        # Prefer sheets that look like the mapping, then fall back to the rest.
        named = [s for s in book.sheet_names if "mapping" in str(s).lower()]
        if sheet is not None and sheet != 0:
            candidates: list[Any] = [sheet]
        else:
            candidates = named + [s for s in book.sheet_names if s not in named]

        frame = None
        chosen: Any = None
        inspected: list[str] = []
        for candidate in candidates:
            trial = book.parse(candidate, dtype=object)
            trial.columns = [str(c).strip() for c in trial.columns]
            trial = align_headers(trial, expected, notes, f"mapping tab '{candidate}': ")
            preview = ", ".join(map(str, trial.columns[:8])) or "(no columns)"
            inspected.append(f"  {candidate}: {preview}")
            if cfg.map_key in trial.columns:
                frame, chosen = trial, candidate
                break

        if frame is None:
            raise InputError(
                f"Could not find the column '{cfg.map_key}' in any sheet of {path}.\n"
                "Sheets inspected and their first columns:\n"
                + "\n".join(inspected)
                + f"\n\nFix one of these: rename the column to '{cfg.map_key}', "
                'point at the right tab with --mapping-sheet "Agent Mapping", '
                "or change map_key in the Config section of this script."
            )
        if notes is not None:
            notes.add("mapping tab", f"read from '{chosen}' in {path.name}")

    before = len(frame)
    frame = frame.dropna(how="all")
    frame[cfg.map_key] = as_text(frame[cfg.map_key]).fillna("")
    frame = frame[frame[cfg.map_key].ne("") & frame[cfg.map_key].str.lower().ne("nan")]
    deduped = frame.drop_duplicates(subset=[cfg.map_key], keep="first")
    if notes is not None:
        dropped = before - len(deduped)
        if dropped:
            notes.add(
                "mapping rows dropped",
                f"{dropped} blank or duplicate {cfg.map_key} row(s); first wins",
            )
    return deduped


def file_digest(path: Path | None) -> str:
    """Short SHA-256 of an input file, so a workbook can be traced to it."""
    if path is None or not Path(path).exists():
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:12]


# ---------------------------------------------------------------------------
# Extra lookup sheets
#
# The agent mapping above is joined on the agent key. Anything else you want to
# enrich the rows with -- queue groupings, contact reasons, rate plan families --
# goes in the LOOKUPS list below. Add one entry per sheet; nothing else changes.
# A sheet that is not present in the workbook is skipped and noted.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lookup:
    """An extra mapping sheet joined onto the interaction rows."""

    sheet: str                  # sheet name inside the mapping workbook
    left_key: str               # column in the interaction frame
    right_key: str              # key column in the mapping sheet
    columns: dict[str, str]     # {column in the sheet: name to use downstream}
    default: str = "Unmapped"   # value for rows the sheet does not cover


LOOKUPS: list[Lookup] = [
    Lookup(
        sheet="Queue Mapping",
        left_key=CONFIG.col_queue,
        right_key="Queue",
        columns={"Queue Group": "Queue_Group", "Channel": "Channel"},
    ),
    # Lookup(
    #     sheet="Reason Mapping",
    #     left_key="Segment",
    #     right_key="Segment",
    #     columns={"Reason Category": "Reason_Category"},
    # ),
]


def apply_lookups(
    frame: pd.DataFrame,
    mapping_path: Path | None,
    lookups: list[Lookup] | None = None,
    notes: Notes | None = None,
) -> pd.DataFrame:
    """Join every configured lookup sheet onto the interaction frame.

    Args:
        frame: Prepared interaction rows.
        mapping_path: Workbook holding the lookup sheets.
        lookups: Lookup definitions; defaults to the module-level LOOKUPS.
        notes: Collector for anything skipped, surfaced in Data Quality.

    Returns:
        The frame with one new column per entry in each lookup's ``columns``.
        Lookups whose sheet or key is missing are skipped and noted.
    """
    active = LOOKUPS if lookups is None else lookups
    if not active:
        return frame
    if mapping_path is None or not Path(mapping_path).exists():
        if notes is not None:
            notes.add("lookups skipped", "no mapping workbook supplied")
        return frame
    if Path(mapping_path).suffix.lower() in {".csv", ".txt"}:
        if notes is not None:
            notes.add("lookups skipped", "mapping is a CSV, which holds no extra sheets")
        return frame

    frame = frame.copy()
    book = pd.ExcelFile(mapping_path)
    available = set(book.sheet_names)
    for lk in active:
        if lk.sheet not in available:
            if notes is not None:
                notes.add("lookup skipped", f"'{lk.sheet}' sheet not in the workbook")
            continue
        if lk.left_key not in frame.columns:
            if notes is not None:
                notes.add("lookup skipped", f"'{lk.sheet}': no '{lk.left_key}' column in the export")
            continue
        table = book.parse(lk.sheet, dtype=object)
        table.columns = [str(c).strip() for c in table.columns]
        if lk.right_key not in table.columns:
            if notes is not None:
                notes.add("lookup skipped", f"'{lk.sheet}': no '{lk.right_key}' key column")
            continue
        keep = [lk.right_key] + [c for c in lk.columns if c in table.columns]
        table = table[keep].drop_duplicates(subset=[lk.right_key], keep="first")
        table[lk.right_key] = table[lk.right_key].astype(str).str.strip()
        table = table.rename(columns={lk.right_key: lk.left_key, **lk.columns})

        # Never let a lookup quietly overwrite a column that already exists.
        clashes = [c for c in lk.columns.values() if c in frame.columns]
        if clashes:
            if notes is not None:
                notes.add("lookup skipped", f"'{lk.sheet}': {', '.join(clashes)} already present")
            continue

        frame[lk.left_key] = as_text(frame[lk.left_key])
        frame = frame.merge(table, on=lk.left_key, how="left")
        for target in lk.columns.values():
            if target in frame.columns:
                unmatched = int(frame[target].isna().sum())
                frame[target] = frame[target].fillna(lk.default)
                if unmatched and notes is not None:
                    notes.add(
                        "lookup gaps",
                        f"'{lk.sheet}' -> {target}: {unmatched} row(s) set to '{lk.default}'",
                    )
    return frame


# ---------------------------------------------------------------------------
# Prepare
# ---------------------------------------------------------------------------


def business_type_from_queue(queue: Any, cfg: Config) -> str:
    """Infer a business type from the queue name (mapping fallback only).

    Patterns are regular expressions matched against the queue name with
    punctuation flattened to spaces, so ``\\bent\\b`` matches "ENT" and
    "Enterprise - ENT" but not Retention, Payments or Contact Centre.
    """
    text = re.sub(r"[^a-z0-9]+", " ", str(queue or "").lower()).strip()
    if not text:
        return cfg.default_business_type
    for pattern, label in cfg.queue_business_type_patterns.items():
        if re.search(pattern, text):
            return label
    return cfg.default_business_type


def prepare(micro: pd.DataFrame, mapping: pd.DataFrame, cfg: Config = CONFIG) -> pd.DataFrame:
    """Clean, join and flag the interaction rows.

    Adds: parsed durations in seconds, business type, reached flag, ACW
    seconds, calendar day and interval keys.
    """
    frame = micro.copy()

    # Agent key
    if cfg.col_login in frame.columns:
        frame["Agent_Key"] = as_text(frame[cfg.col_login]).fillna("Unknown").replace("", "Unknown")
    else:
        frame["Agent_Key"] = "Unknown"

    # Durations -> seconds
    frame["Handle_Sec"] = seconds_series(frame, cfg.col_handle_time)
    frame["Customer_Handle_Sec"] = seconds_series(frame, cfg.col_customer_handle)
    frame["Customer_Dial_Sec"] = seconds_series(frame, cfg.col_customer_dial)
    frame["Customer_Hold_Sec"] = seconds_series(frame, cfg.col_customer_hold)
    frame["Customer_Wrap_Sec"] = seconds_series(frame, cfg.col_customer_wrap)
    frame["Total_Duration_Sec"] = seconds_series(frame, cfg.col_total_duration)
    frame["Queue_Time_Sec"] = seconds_series(frame, cfg.col_queue_time)

    # Fall back to Handle Time if the customer handle column is missing/empty
    fell_back = frame["Customer_Handle_Sec"].isna() & frame["Handle_Sec"].notna()
    frame["Customer_Handle_Sec"] = frame["Customer_Handle_Sec"].fillna(frame["Handle_Sec"])
    frame["Handle_Fallback"] = fell_back.astype(int)

    # Timestamps
    for src, dest in ((cfg.col_start, "Start_TS"), (cfg.col_end, "End_TS")):
        if src in frame.columns:
            frame[dest] = pd.to_datetime(frame[src], errors="coerce")
        else:
            frame[dest] = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")

    # Calendar day: no night shifts, so the start timestamp defines the day.
    if frame["Start_TS"].notna().any():
        frame["Date"] = frame["Start_TS"].dt.date
    elif cfg.col_day in frame.columns:
        frame["Date"] = frame[cfg.col_day]
    else:
        frame["Date"] = pd.NaT

    # Interval: use the export's own interval, else derive the hour
    if cfg.col_interval in frame.columns and frame[cfg.col_interval].notna().any():
        frame["Interval_Key"] = frame[cfg.col_interval].astype(str).str.strip()
    else:
        frame["Interval_Key"] = frame["Start_TS"].dt.hour.map(
            lambda h: "" if pd.isna(h) else f"{int(h):02d}:00"
        )

    # Mapping join. Every mapping column is prefixed first: the export carries
    # its own SPV (and may one day carry Agent or Team), and a merge suffix
    # would silently park the mapping's value in a column nothing ever reads.
    rename = {
        cfg.map_key: "Agent_Key",
        cfg.map_agent_name: "Map_Agent",
        cfg.map_team: "Map_Team",
        cfg.map_business_type: "Map_Business_Type",
        cfg.map_spv: "Map_SPV",
    }
    map_cols = [c for c in rename if c in mapping.columns]
    if map_cols and cfg.map_key in mapping.columns:
        frame = frame.merge(
            mapping[map_cols].rename(columns=rename), on="Agent_Key", how="left"
        )

    mapped_agent = frame.get("Map_Agent", _nan_series(frame.index))
    mapped_team = frame.get("Map_Team", _nan_series(frame.index))
    mapped_spv = frame.get("Map_SPV", _nan_series(frame.index))
    mapped_bt = frame.get("Map_Business_Type", _nan_series(frame.index))

    frame["Agent"] = mapped_agent.fillna(frame["Agent_Key"])
    frame["Team"] = mapped_team.fillna("Unmapped")
    frame["SPV_Final"] = mapped_spv
    if cfg.col_spv in frame.columns:
        frame["SPV_Final"] = frame["SPV_Final"].fillna(frame[cfg.col_spv])
    frame["SPV_Final"] = frame["SPV_Final"].fillna("Unknown")

    # Business type: mapping wins, queue name is the fallback. The queue rule
    # runs over distinct queue names, not every row.
    queues = as_text(frame.get(cfg.col_queue, _nan_series(frame.index))).fillna("")
    resolved = {q: business_type_from_queue(q, cfg) for q in queues.unique()}
    queue_bt = queues.map(resolved)
    frame["Business_Type"] = mapped_bt.replace("", np.nan).fillna(queue_bt)
    frame["Is_Mapped"] = mapped_bt.notna() & mapped_bt.ne("")

    # The row-level business type drives the threshold, because the queue is the
    # only per-row signal when an agent is missing from the mapping. But an agent
    # must stay a single row in the agent-level reports, so resolve one dominant
    # business type per agent and group on that. With a complete mapping the two
    # are identical; without one, this stops an agent being split across queues.
    dominant = frame.groupby("Agent_Key")["Business_Type"].agg(
        lambda s: s.mode().iat[0] if not s.mode().empty else cfg.default_business_type
    )
    frame["Agent_Business_Type"] = frame["Agent_Key"].map(dominant)

    # Reached / long call: strictly greater than the per-business-type threshold
    frame["Threshold_Sec"] = frame["Business_Type"].map(cfg.threshold_for).astype("float64")
    frame["Reached_Flag"] = (
        frame["Customer_Handle_Sec"] > frame["Threshold_Sec"]
    ).astype(int)

    # Trial vs answered call, when the export carries the flag
    if cfg.col_call_flag in frame.columns:
        flag = pd.to_numeric(frame[cfg.col_call_flag], errors="coerce").fillna(0)
        frame["Is_Call"] = flag.clip(0, 1).astype(int)
    else:
        frame["Is_Call"] = (frame["Customer_Handle_Sec"] > 0).astype(int)
    frame["Is_Trial"] = 1 - frame["Is_Call"]

    # After call work. Measured wrap time beats an assumption, so it is used
    # wherever the export carries one; the flat per-reached-call figure fills
    # the gaps. Which source each row used is reported in Data Quality.
    assumed_acw = frame["Reached_Flag"] * cfg.acw_minutes_per_reached_call * 60.0
    measured_acw = frame["Customer_Wrap_Sec"].fillna(0.0)
    if cfg.use_measured_acw:
        has_measured = measured_acw > 0
        frame["ACW_Sec"] = measured_acw.where(has_measured, assumed_acw)
        frame["ACW_Source"] = np.where(has_measured, "Measured", "Assumed")
    else:
        frame["ACW_Sec"] = assumed_acw
        frame["ACW_Source"] = "Assumed"

    # Productive time components (Dial + Total Duration are non-overlapping;
    # the Data Quality sheet tests that claim rather than trusting it)
    frame["Productive_Sec"] = frame["Customer_Dial_Sec"].fillna(0) + frame[
        "Total_Duration_Sec"
    ].fillna(0)
    frame["Productive_ACW_Sec"] = frame["Productive_Sec"] + frame["ACW_Sec"]

    return frame


# ---------------------------------------------------------------------------
# Shared metric definitions
#
# Every rate in the report is computed here and nowhere else. AHT drifting to a
# different denominator on different sheets is what these helpers exist to
# prevent: one name, one definition, every tab.
# ---------------------------------------------------------------------------


def _denominator_seconds(span_sec: pd.Series, cfg: Config) -> pd.Series:
    """Utilisation denominator per the configured basis."""
    if cfg.utilisation_basis == "span":
        return _nz(span_sec)
    return pd.Series(cfg.shift_hours * 3600.0, index=span_sec.index)


def add_rate_metrics(
    out: pd.DataFrame,
    *,
    handle: str = "Handle_Sec",
    calls: str = "Calls",
    interactions: str = "Interactions",
    reached: str = "Reached_Calls",
) -> pd.DataFrame:
    """Add reach rate and the two handle-time averages.

    AHT is always handle seconds over ANSWERED CALLS. The per-interaction
    figure is genuinely useful on queue and interval roll-ups, so it is
    reported too -- under its own name, never as "AHT".
    """
    out["Reach_Rate"] = out[reached] / _nz(out[interactions])
    out["AHT_Sec"] = out[handle] / _nz(out[calls])
    out["AHT"] = out["AHT_Sec"].map(fmt_hms)
    out["Avg_Handle_Per_Interaction_Sec"] = out[handle] / _nz(out[interactions])
    out["Avg_Handle_Per_Interaction"] = out["Avg_Handle_Per_Interaction_Sec"].map(fmt_hms)
    return out


def apply_targets(out: pd.DataFrame, cfg: Config, *, scoped: bool = False) -> pd.DataFrame:
    """Attach the target columns. The only place they are ever set.

    ``Target``, ``Vs_Target`` and ``Met_Target`` have to move together with
    whatever utilisation they describe. Setting them in one place means a
    recomputed utilisation can never leave a stale verdict behind.

    On a scoped (per-slice) file the utilisation is a CONTRIBUTION to the
    agent's month, so "met the target" is not a question that slice can answer.
    ``Share_of_Target_%`` -- how much of the target this slice delivered -- is.
    """
    if out.empty:
        return out
    util = "Utilisation_Contribution" if scoped else "Utilisation_Pure"
    out["Target"] = out["Business_Type"].map(cfg.target_for)
    if scoped:
        out["Share_of_Target_%"] = out[util] / _nz(out["Target"])
        out = out.drop(columns=["Vs_Target", "Met_Target"], errors="ignore")
    else:
        out["Vs_Target"] = out[util] - out["Target"]
        out["Met_Target"] = np.where(
            out[util].isna(), "",
            np.where(out[util] >= out["Target"], "Met", "Below"),
        )
    return out


def unique_long_calls(frame: pd.DataFrame, group_cols: list[str], cfg: Config) -> pd.DataFrame | None:
    """Distinct reached-call identifiers per group, or None when unavailable."""
    if cfg.col_identifier not in frame.columns:
        return None
    reached = frame[frame["Reached_Flag"].eq(1)]
    if reached.empty:
        return None
    return (
        reached.groupby(group_cols, dropna=False)[cfg.col_identifier]
        .nunique()
        .rename("Unique_Long_Calls")
        .reset_index()
    )


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------


def agent_day(frame: pd.DataFrame, cfg: Config = CONFIG, *, scoped: bool = False) -> pd.DataFrame:
    """Per agent, per day: volumes, working window and utilisation.

    ``scoped`` marks a per-slice file, where the shift belongs to the agent's
    whole month rather than to this slice, so the utilisation and target
    columns are left off instead of comparing a slice against a full shift.
    """
    keys = ["Agent_Key", "Agent", "Team", "Agent_Business_Type", "Date"]
    grouped = frame.groupby(keys, dropna=False)

    out = grouped.agg(
        Interactions=("Agent_Key", "size"),
        Calls=("Is_Call", "sum"),
        Trials=("Is_Trial", "sum"),
        Reached_Calls=("Reached_Flag", "sum"),
        Handle_Sec=("Customer_Handle_Sec", "sum"),
        Hold_Sec=("Customer_Hold_Sec", "sum"),
        Dial_Sec=("Customer_Dial_Sec", "sum"),
        Wrap_Sec=("Customer_Wrap_Sec", "sum"),
        Total_Duration_Sec=("Total_Duration_Sec", "sum"),
        ACW_Sec=("ACW_Sec", "sum"),
        Productive_Sec=("Productive_Sec", "sum"),
        Productive_ACW_Sec=("Productive_ACW_Sec", "sum"),
        First_Call=("Start_TS", "min"),
        Last_Call_End=("End_TS", "max"),
        Last_Call_Start=("Start_TS", "max"),
    ).reset_index()

    # Fall back to start timestamps when the export has no end timestamp.
    out["Last_Call"] = out["Last_Call_End"].fillna(out["Last_Call_Start"])
    out = out.drop(columns=["Last_Call_End", "Last_Call_Start"])

    uniq = unique_long_calls(frame, ["Agent_Key", "Date"], cfg)
    if uniq is not None:
        out = out.merge(uniq, on=["Agent_Key", "Date"], how="left")
        out["Unique_Long_Calls"] = out["Unique_Long_Calls"].fillna(0).astype(int)

    span = (out["Last_Call"] - out["First_Call"]).dt.total_seconds()
    out["Span_Sec"] = span.clip(lower=0)
    out["Span_Hours"] = out["Span_Sec"] / 3600.0
    out["Shift_Hours"] = cfg.shift_hours
    out["Occupancy"] = out["Handle_Sec"] / _nz(out["Span_Sec"])

    out = add_rate_metrics(out)

    # Busiest interval of the day
    peak = (
        frame.groupby(["Agent_Key", "Date", "Interval_Key"], dropna=False)
        .size()
        .reset_index(name="n")
        .sort_values("n", ascending=False)
        .drop_duplicates(subset=["Agent_Key", "Date"])
        .rename(columns={"Interval_Key": "Peak_Interval", "n": "Peak_Interval_Calls"})
    )
    out = out.merge(peak, on=["Agent_Key", "Date"], how="left")

    out = out.rename(columns={"Agent_Business_Type": "Business_Type"})

    if not scoped:
        denom = _denominator_seconds(out["Span_Sec"], cfg)
        out["Utilisation_Pure"] = out["Productive_Sec"] / denom
        out["Utilisation_With_ACW"] = out["Productive_ACW_Sec"] / denom
        out = apply_targets(out, cfg)

    out["Handle_Time"] = out["Handle_Sec"].map(fmt_hms)
    out["Productive_Time"] = out["Productive_Sec"].map(fmt_hms)
    out["ACW_Time"] = out["ACW_Sec"].map(fmt_hms)
    return out.sort_values(["Agent", "Date"])


def agent_summary(day_frame: pd.DataFrame, frame: pd.DataFrame, cfg: Config = CONFIG) -> pd.DataFrame:
    """Per agent roll-up across the whole period."""
    active = day_frame[day_frame["Interactions"] >= cfg.min_interactions_active_day]

    out = active.groupby(["Agent_Key", "Agent", "Team", "Business_Type"], dropna=False).agg(
        Active_Days=("Date", "nunique"),
        Interactions=("Interactions", "sum"),
        Calls=("Calls", "sum"),
        Trials=("Trials", "sum"),
        Reached_Calls=("Reached_Calls", "sum"),
        Handle_Sec=("Handle_Sec", "sum"),
        Hold_Sec=("Hold_Sec", "sum"),
        Dial_Sec=("Dial_Sec", "sum"),
        Wrap_Sec=("Wrap_Sec", "sum"),
        ACW_Sec=("ACW_Sec", "sum"),
        Productive_Sec=("Productive_Sec", "sum"),
        Productive_ACW_Sec=("Productive_ACW_Sec", "sum"),
        Span_Sec=("Span_Sec", "sum"),
    ).reset_index()

    # Distinct long calls across the whole period, not the sum of daily distincts
    uniq = unique_long_calls(frame, ["Agent_Key"], cfg)
    if uniq is not None:
        out = out.merge(uniq, on="Agent_Key", how="left")
        out["Unique_Long_Calls"] = out["Unique_Long_Calls"].fillna(0).astype(int)

    out["Scheduled_Sec"] = out["Active_Days"] * cfg.shift_hours * 3600.0
    denom = out["Scheduled_Sec"] if cfg.utilisation_basis == "shift" else _nz(out["Span_Sec"])

    out["Hours_Worked"] = out["Span_Sec"] / 3600.0
    out["Scheduled_Hours"] = out["Scheduled_Sec"] / 3600.0
    out["Utilisation_Pure"] = out["Productive_Sec"] / _nz(denom)
    out["Utilisation_With_ACW"] = out["Productive_ACW_Sec"] / _nz(denom)
    out["Occupancy"] = out["Handle_Sec"] / _nz(out["Span_Sec"])
    out = add_rate_metrics(out)
    out["Calls_Per_Day"] = out["Interactions"] / _nz(out["Active_Days"])
    out["Reached_Per_Day"] = out["Reached_Calls"] / _nz(out["Active_Days"])

    # Target follows the agent's business type, not one number for everyone.
    out = apply_targets(out, cfg)

    peak = (
        frame.groupby(["Agent_Key", "Interval_Key"], dropna=False)
        .size()
        .reset_index(name="n")
        .sort_values("n", ascending=False)
        .drop_duplicates(subset=["Agent_Key"])
        .rename(columns={"Interval_Key": "Peak_Interval"})[["Agent_Key", "Peak_Interval"]]
    )
    out = out.merge(peak, on="Agent_Key", how="left")

    return out.sort_values("Utilisation_Pure", ascending=False)


def by_dimension(frame: pd.DataFrame, dimension: str, cfg: Config = CONFIG) -> pd.DataFrame:
    """Generic roll-up by queue, team, business type or interval."""
    if dimension not in frame.columns:
        return pd.DataFrame()
    out = frame.groupby(dimension, dropna=False).agg(
        Interactions=("Agent_Key", "size"),
        Agents=("Agent_Key", "nunique"),
        Calls=("Is_Call", "sum"),
        Reached_Calls=("Reached_Flag", "sum"),
        Handle_Sec=("Customer_Handle_Sec", "sum"),
        Productive_Sec=("Productive_Sec", "sum"),
    ).reset_index()
    out = add_rate_metrics(out)
    out["Share_%"] = _share(out["Interactions"])
    # A utilisation target belongs to a business type. Printing one beside every
    # queue or team would show the fallback rate as though it were their target.
    if dimension in ("Business_Type", "Agent_Business_Type"):
        out["Utilisation_Target"] = out[dimension].map(cfg.target_for)
    return out.sort_values("Interactions", ascending=False)


def cross_tab(frame: pd.DataFrame, rows: str, cols: str) -> pd.DataFrame:
    """Interaction counts for one dimension against another, with totals."""
    if rows not in frame.columns or cols not in frame.columns:
        return pd.DataFrame()
    table = pd.crosstab(frame[rows], frame[cols])
    if table.empty:
        return pd.DataFrame()
    table["Total"] = table.sum(axis=1)
    return table.reset_index().sort_values("Total", ascending=False)


def daily_trend(day_frame: pd.DataFrame, cfg: Config = CONFIG) -> pd.DataFrame:
    """Whole-centre trend by calendar day."""
    out = day_frame.groupby("Date", dropna=False).agg(
        Agents=("Agent_Key", "nunique"),
        Interactions=("Interactions", "sum"),
        Calls=("Calls", "sum"),
        Reached_Calls=("Reached_Calls", "sum"),
        Productive_Sec=("Productive_Sec", "sum"),
        Handle_Sec=("Handle_Sec", "sum"),
    ).reset_index()
    out = add_rate_metrics(out)
    out["Utilisation_Pure"] = out["Productive_Sec"] / _nz(
        out["Agents"] * cfg.shift_hours * 3600.0
    )
    out["Calls_Per_Agent"] = out["Interactions"] / _nz(out["Agents"])
    return out.sort_values("Date")


def interval_profile(frame: pd.DataFrame) -> pd.DataFrame:
    """Volume and reach profile across the day."""
    out = frame.groupby("Interval_Key", dropna=False).agg(
        Interactions=("Agent_Key", "size"),
        Calls=("Is_Call", "sum"),
        Reached_Calls=("Reached_Flag", "sum"),
        Agents=("Agent_Key", "nunique"),
        Handle_Sec=("Customer_Handle_Sec", "sum"),
    ).reset_index()
    out = add_rate_metrics(out)
    out["Share_%"] = _share(out["Interactions"])
    # "9:00" must not sort after "10:00".
    return out.sort_values("Interval_Key", key=lambda s: s.map(interval_minutes))


def agent_interval_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Agent x interval interaction counts (heat map source).

    Keyed on Agent_Key as well as the display name, so two agents who happen to
    share a name stay two rows.
    """
    matrix = pd.crosstab([frame["Agent_Key"], frame["Agent"]], frame["Interval_Key"])
    if matrix.empty:
        return pd.DataFrame()
    matrix = matrix[sorted(matrix.columns, key=interval_minutes)]
    matrix["Total"] = matrix.sum(axis=1)
    return matrix.reset_index().sort_values("Total", ascending=False)


def outliers(summary: pd.DataFrame, cfg: Config = CONFIG, *, scoped: bool = False) -> pd.DataFrame:
    """Flag agents that sit outside the expected performance band.

    On a scoped (per-slice) file the below-target test is suppressed: one
    queue's contribution is structurally below a whole-agent target, so it
    would flag everyone and say nothing. The z-score tests still compare
    like with like inside the slice and are kept.
    """
    if summary.empty:
        return pd.DataFrame()
    out = summary.copy()
    util = "Utilisation_Contribution" if scoped else "Utilisation_Pure"
    metrics = [m for m in (util, "Reach_Rate", "AHT_Sec", "Calls_Per_Day") if m in out.columns]

    for metric in metrics:
        values = pd.to_numeric(out[metric], errors="coerce").astype("float64")
        std = values.std(ddof=0)
        # np.float64("nan") is neither identical nor equal to np.nan, so a
        # membership test against (0, np.nan) silently lets NaN through.
        if not np.isfinite(std) or std == 0:
            out[f"z_{metric}"] = 0.0
        else:
            out[f"z_{metric}"] = (values - values.mean()) / std

    z_util = f"z_{util}"
    flags: list[str] = []
    for _, row in out.iterrows():
        reasons: list[str] = []
        if not scoped:
            target = row.get("Target", cfg.utilisation_target)
            if pd.notna(row.get(util)) and pd.notna(target) and row[util] < target:
                reasons.append(f"Below utilisation target ({target:.0%})")
        if _z(row, z_util) < -cfg.outlier_z:
            reasons.append("Utilisation outlier (low)")
        if _z(row, z_util) > cfg.outlier_z:
            reasons.append("Utilisation outlier (high)")
        if _z(row, "z_AHT_Sec") > cfg.outlier_z:
            reasons.append("AHT outlier (high)")
        if _z(row, "z_AHT_Sec") < -cfg.outlier_z:
            reasons.append("AHT outlier (low)")
        if _z(row, "z_Reach_Rate") < -cfg.outlier_z:
            reasons.append("Reach rate outlier (low)")
        if _z(row, "z_Calls_Per_Day") < -cfg.outlier_z:
            reasons.append("Volume outlier (low)")
        flags.append(" | ".join(reasons))
    out["Flags"] = flags

    cols = [
        "Agent", "Team", "Business_Type", "Active_Days", "Days_On_This_Slice",
        "Interactions", "Reached_Calls", "Reach_Rate", "AHT", util,
        "Utilisation_With_ACW", "Utilisation_Contribution_With_ACW",
        "Target", "Share_of_Target_%", "Vs_Target", "Met_Target", "Flags",
    ]
    watch = out[out["Flags"].ne("")][[c for c in cols if c in out.columns]]
    return watch.sort_values(util) if util in watch.columns else watch


def _z(row: pd.Series, name: str) -> float:
    """A z-score from a summary row, treating missing or NaN as neutral."""
    value = row.get(name, 0.0)
    return 0.0 if pd.isna(value) else float(value)


def data_quality(
    frame: pd.DataFrame,
    mapping: pd.DataFrame,
    cfg: Config = CONFIG,
    day_frame: pd.DataFrame | None = None,
    notes: Notes | None = None,
) -> pd.DataFrame:
    """Checks that must pass before the numbers are trusted."""
    total = len(frame)
    unmapped = sorted(frame.loc[~frame["Is_Mapped"], "Agent_Key"].dropna().unique().tolist())

    # The headline utilisation rests on Dial and Total Duration not overlapping.
    # Test that claim rather than trusting the comment that asserts it.
    components = (
        frame["Customer_Handle_Sec"].fillna(0)
        + frame["Customer_Hold_Sec"].fillna(0)
        + frame["Customer_Wrap_Sec"].fillna(0)
    )
    short_total = int((frame["Total_Duration_Sec"].fillna(0) + 1 < components).sum())

    measured = int((frame["ACW_Source"] == "Measured").sum())
    assumed_on_reached = int(
        ((frame["ACW_Source"] == "Assumed") & frame["Reached_Flag"].eq(1)).sum()
    )

    seen_types = [t for t in frame["Business_Type"].dropna().unique()]
    fallback_target = sorted(t for t in seen_types if not cfg.has_explicit_target(t))
    fallback_threshold = sorted(t for t in seen_types if not cfg.has_explicit_threshold(t))

    reversed_ts = int((frame["End_TS"] < frame["Start_TS"]).sum())

    checks: list[dict[str, Any]] = [
        {"Check": "Rows loaded", "Value": total, "Detail": ""},
        {"Check": "Distinct agents", "Value": frame["Agent_Key"].nunique(), "Detail": ""},
        {"Check": "Distinct days", "Value": frame["Date"].nunique(), "Detail": ""},
        {"Check": "Agents in the mapping file", "Value": len(mapping), "Detail": ""},
        {
            "Check": "Agents missing from mapping",
            "Value": len(unmapped),
            "Detail": ", ".join(map(str, unmapped[:40])),
        },
        {
            "Check": "Rows with unparsable customer handle time",
            "Value": int(frame["Customer_Handle_Sec"].isna().sum()),
            "Detail": "Check the duration format in the export",
        },
        {
            "Check": "Rows where customer handle time fell back to Handle Time",
            "Value": int(frame["Handle_Fallback"].sum()),
            "Detail": f"'{cfg.col_customer_handle}' was blank or unparsable on these rows",
        },
        {
            "Check": "Rows with no start timestamp",
            "Value": int(frame["Start_TS"].isna().sum()),
            "Detail": "Day and interval fall back to the export columns",
        },
        {
            "Check": "Rows where the end precedes the start",
            "Value": reversed_ts,
            "Detail": "Working span is floored at zero for these",
        },
        {
            "Check": "Duplicate interaction IDs",
            "Value": int(frame[cfg.col_interaction_id].duplicated().sum())
            if cfg.col_interaction_id in frame.columns else 0,
            "Detail": "",
        },
        {
            "Check": "Rows where productive time exceeds the shift",
            "Value": int((frame["Productive_Sec"] > cfg.shift_hours * 3600).sum()),
            "Detail": "Single interaction longer than a full shift",
        },
        {
            "Check": "Total Duration shorter than handle + hold + wrap",
            "Value": short_total,
            "Detail": "Tests the assumption behind Productive time; "
                      "a large count means the utilisation definition needs review",
        },
        {
            "Check": "ACW rows using measured wrap time",
            "Value": measured,
            "Detail": f"{measured / total:.1%} of rows" if total else "",
        },
        {
            "Check": "Reached calls using the assumed ACW",
            "Value": assumed_on_reached,
            "Detail": f"{cfg.acw_minutes_per_reached_call} min assumed where wrap time is absent",
        },
        {
            "Check": "Business types resolved from the queue (not the mapping)",
            "Value": int((~frame["Is_Mapped"]).sum()),
            "Detail": "Mapping is the authoritative source",
        },
        {
            "Check": "Business types using the fallback utilisation target",
            "Value": len(fallback_target),
            "Detail": (", ".join(map(str, fallback_target[:20])) + f" -> {cfg.utilisation_target:.0%}")
            if fallback_target else "",
        },
        {
            "Check": "Business types using the fallback reached threshold",
            "Value": len(fallback_threshold),
            "Detail": (", ".join(map(str, fallback_threshold[:20])) + f" -> > {cfg.default_reached_threshold} sec")
            if fallback_threshold else "",
        },
    ]

    if day_frame is not None and not day_frame.empty:
        over_span = int((day_frame["Productive_Sec"] > day_frame["Span_Sec"].where(
            day_frame["Span_Sec"] > 0)).sum())
        checks.append({
            "Check": "Agent-days where productive time exceeds the working span",
            "Value": over_span,
            "Detail": "Overlapping interactions, or a span that understates the day",
        })

    if notes is not None:
        checks.extend(notes.rows())
    return pd.DataFrame(checks)


def config_sheet(
    cfg: Config = CONFIG,
    scope: str = "All queues",
    scoped: bool = False,
    info: RunInfo | None = None,
) -> pd.DataFrame:
    """Human-readable record of the rules applied to this run."""
    info = info or RunInfo()
    acw_rule = (
        f"Measured wrap time where present, else {cfg.acw_minutes_per_reached_call} "
        "min per reached call"
        if cfg.use_measured_acw
        else f"{cfg.acw_minutes_per_reached_call} min per reached call"
    )
    rows: list[tuple[str, str]] = [
        ("Scope of this file", scope),
        *[
            (f"Reached threshold - {name}", f"> {value} sec")
            for name, value in cfg.reached_thresholds.items()
        ],
        ("Reached threshold - anything else", f"> {cfg.default_reached_threshold} sec"),
        ("Threshold measured on", cfg.col_customer_handle),
        ("Shift length", f"{cfg.shift_hours} hours"),
        ("After call work", acw_rule),
        ("Utilisation basis", cfg.utilisation_basis),
        ("Utilisation (pure)", "(Customer Dial Time + Total Duration) / scheduled seconds"),
        ("Utilisation (with ACW)", "(Customer Dial Time + Total Duration + ACW) / scheduled seconds"),
        ("AHT", "Handle time / answered calls (never per interaction)"),
        ("Avg_Handle_Per_Interaction", "Handle time / all interactions, trials included"),
        ("Reach rate", "Reached calls / all interactions"),
        ("Unique long calls", f"Distinct {cfg.col_identifier} where reached = 1"),
        *[
            (f"Utilisation target - {name}", f"{value:.0%}")
            for name, value in cfg.utilisation_targets.items()
        ],
        ("Utilisation target - anything else", f"{cfg.utilisation_target:.0%}"),
        ("Outlier sensitivity", f"z > {cfg.outlier_z}"),
        ("Business type source", "Agent mapping, queue name as fallback"),
        ("Active day", f"{cfg.min_interactions_active_day}+ interactions"),
        ("Source export", info.micro_path.name if info.micro_path else ""),
        ("Source export rows", f"{info.micro_rows:,}" if info.micro_rows else ""),
        ("Source export checksum", f"sha256:{info.micro_sha}" if info.micro_sha else ""),
        ("Agent mapping", info.mapping_path.name if info.mapping_path else "none (queue fallback)"),
        ("Script version", SCRIPT_VERSION),
        ("Run date", dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]
    if scoped:
        rows.insert(1, (
            "Utilisation_Contribution",
            "Productive time ON THIS SLICE divided by the agent's FULL scheduled "
            "hours for the period. It is the contribution this slice makes, not "
            "the agent's total utilisation. Add the same agent across all slice "
            "files and you get the figure in the master file.",
        ))
        rows.insert(2, (
            "Share_of_Agent_%",
            "How much of that agent's total interactions this slice represents.",
        ))
        rows.insert(3, (
            "Share_of_Target_%",
            "How much of the agent's utilisation target this slice delivered. "
            "A met/missed verdict is not shown here: it is only answerable on "
            "the master file, where the whole shift is in scope.",
        ))
    return pd.DataFrame(rows, columns=["Rule", "Applied value"])


# ---------------------------------------------------------------------------
# Excel output
# ---------------------------------------------------------------------------

PERCENT_COLS: Final[set[str]] = {
    "Utilisation_Pure", "Utilisation_With_ACW", "Utilisation_Contribution",
    "Utilisation_Contribution_With_ACW", "Occupancy", "Reach_Rate", "Share_%",
    "Vs_Target", "Share_of_Agent_%", "Share_of_Target_%", "Target",
    "Utilisation_Target",
}
NUMBER_COLS: Final[set[str]] = {
    "Calls_Per_Day", "Reached_Per_Day", "Hours_Worked", "Scheduled_Hours",
    "Span_Hours", "Shift_Hours", "AHT_Sec", "Avg_Handle_Per_Interaction_Sec",
    "Calls_Per_Agent",
}
DROP_HELPERS: Final[set[str]] = {
    "Handle_Sec", "Hold_Sec", "Dial_Sec", "Wrap_Sec", "ACW_Sec", "Productive_Sec",
    "Productive_ACW_Sec", "Span_Sec", "Scheduled_Sec", "Total_Duration_Sec",
}


def _presentable(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop the raw second columns that only exist to feed the arithmetic."""
    return frame.drop(columns=[c for c in frame.columns if c in DROP_HELPERS], errors="ignore")


def write_excel(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    """Write all analyses to a single formatted workbook.

    Prefers xlsxwriter, which sets a column's width and number format in one
    call; the openpyxl path has to touch every cell, which dominates the run
    time once the output is split into a file per queue.
    """
    try:
        import xlsxwriter  # noqa: F401
    except ImportError:
        _write_openpyxl(sheets, path)
        return
    _write_xlsxwriter(sheets, path)


def _write_xlsxwriter(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    with pd.ExcelWriter(
        path, engine="xlsxwriter",
        datetime_format="yyyy-mm-dd hh:mm", date_format="yyyy-mm-dd",
    ) as writer:
        book = writer.book
        header_fmt = book.add_format({
            "bold": True, "font_color": "#FFFFFF", "bg_color": f"#{BRAND_RED}",
            "font_name": "Calibri", "font_size": 10, "align": "center",
            "valign": "vcenter", "text_wrap": True, "bottom": 1,
            "border_color": f"#{BRAND_LINE}",
        })
        body = {"font_name": "Calibri", "font_size": 10, "font_color": f"#{BRAND_INK}"}
        base_fmt = book.add_format(body)
        pct_fmt = book.add_format({**body, "num_format": "0.0%"})
        num_fmt = book.add_format({**body, "num_format": "0.00"})

        for name, frame in sheets.items():
            if frame is None or frame.empty:
                continue
            clean = _presentable(frame)
            sheet_name = name[:31]
            clean.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes(1, 0)
            worksheet.set_row(0, 28)
            for idx, header in enumerate(clean.columns):
                worksheet.write(0, idx, str(header), header_fmt)
                fmt = pct_fmt if header in PERCENT_COLS else (
                    num_fmt if header in NUMBER_COLS else base_fmt)
                width = max(12, min(28, len(str(header)) + 4))
                worksheet.set_column(idx, idx, width, fmt)
            if len(clean) and len(clean.columns):
                worksheet.autofilter(0, 0, len(clean), len(clean.columns) - 1)


def _write_openpyxl(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            if frame is None or frame.empty:
                continue
            _presentable(frame).to_excel(writer, sheet_name=name[:31], index=False)

        workbook = writer.book
        header_fill = PatternFill("solid", fgColor=BRAND_RED)
        header_font = Font(color="FFFFFF", bold=True, size=10, name="Calibri")
        body_font = Font(color=BRAND_INK, size=10, name="Calibri")
        border = Border(bottom=Side(style="thin", color=BRAND_LINE))

        for sheet in workbook.worksheets:
            sheet.freeze_panes = "A2"
            headers = [c.value for c in sheet[1]]
            for cell in sheet[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = border
            sheet.row_dimensions[1].height = 28

            for idx, header in enumerate(headers, start=1):
                letter = get_column_letter(idx)
                sheet.column_dimensions[letter].width = max(12, min(28, len(str(header)) + 4))
                number_format = None
                if header in PERCENT_COLS:
                    number_format = "0.0%"
                elif header in NUMBER_COLS:
                    number_format = "0.00"
                for row in range(2, sheet.max_row + 1):
                    cell = sheet.cell(row=row, column=idx)
                    cell.font = body_font
                    if number_format:
                        cell.number_format = number_format

            if sheet.max_row > 1:
                sheet.auto_filter.ref = (
                    f"A1:{get_column_letter(sheet.max_column)}{sheet.max_row}"
                )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Everything a caller (or the CLI) needs after a run."""

    files: dict[str, Path]
    master: dict[str, pd.DataFrame]
    reconciliation: dict[str, Any]
    unmapped_agents: int
    notes: Notes


def build_sheets(
    frame: pd.DataFrame,
    mapping: pd.DataFrame,
    cfg: Config = CONFIG,
    scope: str = "All queues",
    notes: Notes | None = None,
    info: RunInfo | None = None,
    reference: SliceReference | None = None,
    day_frame: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """Build the full sheet set for one slice of the data.

    Args:
        frame: Interaction rows for this slice.
        mapping: Agent mapping, used by the data quality checks.
        cfg: Business rules.
        scope: Label written into the Config sheet.
        notes: Load notes, surfaced in Data Quality.
        info: Provenance for the Config sheet.
        reference: Whole-period figures. When given, this is a per-slice file:
            each agent's utilisation is expressed as the CONTRIBUTION this
            slice made to their month, using the agent's full scheduled hours
            as the denominator. Charging the slice only the days it appears on
            would inflate it and the slices would no longer add up to the
            master.

    Returns:
        Sheet name to dataframe. Empty sheets are dropped by the writer.
    """
    scoped = reference is not None
    if day_frame is None:
        day_frame = agent_day(frame, cfg, scoped=scoped)
    summary = agent_summary(day_frame, frame, cfg)

    if scoped and not summary.empty:
        total_days = summary["Agent_Key"].map(reference.active_days)
        total_interactions = summary["Agent_Key"].map(reference.interactions)
        denom_sec = _nz(total_days * cfg.shift_hours * 3600.0)

        summary["Share_of_Agent_%"] = summary["Interactions"] / _nz(total_interactions)
        summary["Scheduled_Hours"] = denom_sec / 3600.0
        summary["Utilisation_Contribution"] = summary["Productive_Sec"] / denom_sec
        summary["Utilisation_Contribution_With_ACW"] = summary["Productive_ACW_Sec"] / denom_sec
        summary = summary.drop(columns=["Utilisation_Pure", "Utilisation_With_ACW"])
        summary = summary.rename(columns={"Active_Days": "Days_On_This_Slice"})
        # Recomputing the utilisation means the target columns have to be
        # rebuilt from it, never left behind at their whole-month values.
        summary = apply_targets(summary, cfg, scoped=True)
        summary = summary.sort_values("Utilisation_Contribution", ascending=False)

    return {
        "Config": config_sheet(cfg, scope, scoped=scoped, info=info),
        "Agent Summary": summary,
        "Agent x Day": day_frame,
        "Agent x Interval": agent_interval_matrix(frame),
        "Interval Profile": interval_profile(frame),
        "Daily Trend": daily_trend(day_frame, cfg),
        "By Queue": by_dimension(frame, cfg.col_queue, cfg),
        "By Team": by_dimension(frame, "Team", cfg),
        "By Business Type": by_dimension(frame, "Business_Type", cfg),
        "By SPV": by_dimension(frame, "SPV_Final", cfg),
        "Queue x Team": cross_tab(frame, cfg.col_queue, "Team"),
        # Lookup-driven sheets. Each is skipped automatically when the matching
        # lookup sheet is absent, so this list is safe to extend.
        "By Queue Group": by_dimension(frame, "Queue_Group", cfg),
        "By Channel": by_dimension(frame, "Channel", cfg),
        "Watchlist": outliers(summary, cfg, scoped=scoped),
        "Data Quality": data_quality(frame, mapping, cfg, day_frame, notes),
    }


def safe_name(value: object, limit: int = 40) -> str:
    """Turn a queue or team name into something safe for a file name."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_")
    return (text or "Unknown")[:limit]


def unique_path(folder: Path, stem: str, label: object, used: set[str]) -> Path:
    """A file path for one slice that cannot collide with an earlier slice.

    ``safe_name`` flattens punctuation and truncates, so "Consumer - Cairo" and
    "Consumer_Cairo" reduce to the same name. Silently overwriting one slice
    with another is the worst possible outcome, so collisions get a suffix.
    """
    base = f"{stem}_{safe_name(label)}"
    candidate, suffix = base, 2
    while candidate.lower() in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate.lower())
    return folder / f"{candidate}.xlsx"


def run(
    micro_path: Path,
    mapping_path: Path | None,
    out_path: Path,
    micro_sheet: str | int = 0,
    mapping_sheet: str | int | None = None,
    cfg: Config = CONFIG,
    split_by: str | None = None,
    dry_run: bool = False,
) -> RunResult:
    """Run the analysis and write one master workbook plus one file per slice.

    Args:
        split_by: Column to split on, e.g. ``Queue`` or ``Team``. When None
            only the master workbook is written.
        dry_run: Build everything and report, but write no files.

    Returns:
        The files written, the master sheets, and a reconciliation showing that
        the slices add back up to the master.
    """
    notes = Notes()
    micro = load_micro(micro_path, micro_sheet, cfg, notes)
    mapping = load_mapping(mapping_path, mapping_sheet, cfg, notes)
    info = RunInfo(
        micro_path=Path(micro_path),
        micro_rows=len(micro),
        micro_sha=file_digest(Path(micro_path)),
        mapping_path=Path(mapping_path) if mapping_path else None,
        mapping_rows=len(mapping),
    )

    frame = prepare(micro, mapping, cfg)
    frame = apply_lookups(frame, mapping_path, notes=notes)

    if split_by and cfg.utilisation_basis != "shift":
        # The slice denominator is the agent's scheduled hours. Under the span
        # basis the master uses something else entirely, so the slices would
        # not reconcile - better to refuse than to ship two definitions.
        raise InputError(
            f"--split-by needs --utilisation-basis shift (got '{cfg.utilisation_basis}').\n"
            "A slice file measures each agent's contribution against their "
            "scheduled hours; the observed span is not divisible that way."
        )

    master_day = agent_day(frame, cfg)
    master = build_sheets(frame, mapping, cfg, "All queues", notes, info, day_frame=master_day)

    files: dict[str, Path] = {}
    if not dry_run:
        write_excel(master, out_path)
    files["MASTER"] = out_path

    unmapped_agents = int(frame.loc[~frame["Is_Mapped"], "Agent_Key"].nunique())
    reconciliation: dict[str, Any] = {
        "interactions_master": len(frame),
        "interactions_slices": len(frame),
        "productive_master": float(frame["Productive_Sec"].sum()),
        "productive_slices": float(frame["Productive_Sec"].sum()),
        "slices": 0,
        "ok": True,
    }

    if not split_by:
        return RunResult(files, master, reconciliation, unmapped_agents, notes)

    if split_by not in frame.columns:
        available = ", ".join(
            c for c in (cfg.col_queue, "Team", "Business_Type", "SPV_Final", "Queue_Group")
            if c in frame.columns
        )
        raise InputError(
            f"Cannot split by '{split_by}' - the column is not in the data.\n"
            f"Available split columns: {available}"
        )

    # Rows with a blank split value get their own file rather than vanishing
    # from every slice, which would quietly break the reconciliation below.
    labels = frame[split_by].astype("object").where(frame[split_by].notna(), BLANK_SLICE)
    labels = labels.astype(str).str.strip().replace("", BLANK_SLICE)

    # The slice denominator has to use the same active-day rule as the master,
    # or the slices stop adding up whenever min_interactions_active_day > 1.
    master_active = master_day[master_day["Interactions"] >= cfg.min_interactions_active_day]
    reference = SliceReference(
        interactions=master_active.groupby("Agent_Key")["Interactions"].sum(),
        active_days=master_active.groupby("Agent_Key")["Date"].nunique(),
    )

    stem = out_path.with_suffix("").name
    folder = out_path.parent
    used: set[str] = set()
    sliced_rows = 0
    sliced_productive = 0.0

    for value in sorted(labels.unique(), key=str):
        slice_frame = frame[labels.eq(value)].copy()
        if slice_frame.empty:
            continue
        sheets = build_sheets(
            slice_frame, mapping, cfg, str(value), notes, info, reference=reference
        )
        path = unique_path(folder, stem, value, used)
        if not dry_run:
            write_excel(sheets, path)
        files[str(value)] = path
        sliced_rows += len(slice_frame)
        sliced_productive += float(slice_frame["Productive_Sec"].sum())

    reconciliation.update({
        "interactions_slices": sliced_rows,
        "productive_slices": sliced_productive,
        "slices": len(files) - 1,
        "ok": sliced_rows == len(frame)
        and abs(sliced_productive - float(frame["Productive_Sec"].sum())) < 1.0,
    })
    return RunResult(files, master, reconciliation, unmapped_agents, notes)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def parse_overrides(text: str, cast: Any, label: str) -> dict[str, Any]:
    """Parse ``"Consumer=0.35,Enterprise=0.33"`` into a dict."""
    out: dict[str, Any] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise InputError(f"--{label} expects Name=value pairs, got '{part}'")
        name, value = part.split("=", 1)
        try:
            out[name.strip()] = cast(value.strip())
        except ValueError as exc:
            raise InputError(f"--{label}: could not read '{part}' ({exc})") from exc
    if not out:
        raise InputError(f"--{label} was empty")
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "micro", type=Path, nargs="?", default=None,
        help=f"Micro interactions export (.xlsx or .csv). Defaults to {DEFAULT_MICRO}",
    )
    parser.add_argument("--mapping", type=Path, default=None, help="Agent mapping file")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--micro-sheet", default=0)
    parser.add_argument("--mapping-sheet", default=None,
                        help="Mapping tab name. Auto-detected when omitted.")
    parser.add_argument("--shift-hours", type=float, default=None)
    parser.add_argument("--acw-minutes", type=float, default=None)
    parser.add_argument("--no-measured-acw", action="store_true",
                        help="Ignore the export's wrap time and always assume the "
                             "flat per-reached-call figure.")
    parser.add_argument("--utilisation-basis", choices=("shift", "span"), default=None)
    parser.add_argument(
        "--target", type=float, default=None,
        help="Fallback utilisation target for business types that are not "
             "listed in Config, e.g. 0.30",
    )
    parser.add_argument(
        "--targets", default=None,
        help='Per business type utilisation targets, e.g. "Consumer=0.35,Internet=0.30". '
             "Replaces the whole set from Config.",
    )
    parser.add_argument(
        "--thresholds", default=None,
        help='Per business type reached-call thresholds in seconds, e.g. '
             '"Consumer=59,Enterprise=49". Replaces the whole set from Config.',
    )
    parser.add_argument(
        "--split-by", default=DEFAULT_SPLIT_BY,
        help="Column to write a separate file for, e.g. Queue, Team, "
             "Business_Type, SPV_Final. Use --no-split for a single file.",
    )
    parser.add_argument("--no-split", action="store_true",
                        help="Write only the master workbook.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build every sheet and report, but write no files.")
    parser.add_argument(
        "--fail-on-unmapped", type=int, default=None, metavar="N",
        help="Exit with status 2 when more than N agents are missing from the "
             "mapping. Turns the Data Quality tab into a gate for scheduled runs.",
    )
    return parser


def _beside_script(name: str) -> Path:
    """Resolve a bare file name against the folder holding this script."""
    return Path(__file__).resolve().parent / name


def resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path | None, Path]:
    """Fill in the defaults for any path the user did not supply."""
    micro = args.micro if args.micro is not None else _beside_script(DEFAULT_MICRO)
    if not micro.is_absolute() and not micro.exists():
        candidate = _beside_script(str(micro))
        if candidate.exists():
            micro = candidate

    mapping = args.mapping
    if mapping is None:
        default_mapping = _beside_script(DEFAULT_MAPPING)
        mapping = default_mapping if default_mapping.exists() else None
    elif not mapping.is_absolute() and not mapping.exists():
        candidate = _beside_script(str(mapping))
        mapping = candidate if candidate.exists() else mapping

    out = args.out if args.out is not None else _beside_script(DEFAULT_OUT)
    return micro, mapping, out


def config_from_args(args: argparse.Namespace) -> Config:
    """Apply every command line override to the Config."""
    overrides: dict[str, Any] = {}
    if args.shift_hours is not None:
        overrides["shift_hours"] = args.shift_hours
    if args.acw_minutes is not None:
        overrides["acw_minutes_per_reached_call"] = args.acw_minutes
    if args.no_measured_acw:
        overrides["use_measured_acw"] = False
    if args.utilisation_basis is not None:
        overrides["utilisation_basis"] = args.utilisation_basis
    if args.target is not None:
        overrides["utilisation_target"] = args.target
    if args.targets:
        overrides["utilisation_targets"] = parse_overrides(args.targets, float, "targets")
    if args.thresholds:
        overrides["reached_thresholds"] = parse_overrides(args.thresholds, int, "thresholds")
    return dataclasses.replace(CONFIG, **overrides) if overrides else CONFIG


def _report_missing_input(micro: Path) -> None:
    folder = Path(__file__).resolve().parent
    found = sorted(p.name for p in folder.glob("*.xls*") if not p.name.startswith("~$"))
    print(f"Input not found: {micro}", file=sys.stderr)
    print(f"Looked in: {folder}", file=sys.stderr)
    if found:
        print("\nSpreadsheets sitting next to the script:", file=sys.stderr)
        for name in found:
            print(f"  {name}", file=sys.stderr)
        print(
            "\nEither rename your export to match DEFAULT_MICRO at the top of "
            "this file, or pass it on the command line:\n"
            '  python agent_productivity.py "<your file>.xlsx"',
            file=sys.stderr,
        )
    else:
        print("No spreadsheets found in that folder.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    micro, mapping, out = resolve_inputs(args)

    if not micro.exists():
        _report_missing_input(micro)
        return 1

    try:
        cfg = config_from_args(args)
        split_by = None if args.no_split else args.split_by

        print(f"Micro export         : {micro}")
        print(f"Agent mapping        : {mapping if mapping else 'none (queue fallback)'}")
        print(f"Split by             : {split_by or 'none (single file)'}")
        print("Reached thresholds   : " + ", ".join(
            f"{k} >{v}s" for k, v in cfg.reached_thresholds.items())
            + f", other >{cfg.default_reached_threshold}s")
        print("Utilisation targets  : " + ", ".join(
            f"{k} {v:.0%}" for k, v in cfg.utilisation_targets.items())
            + f", other {cfg.utilisation_target:.0%}")

        result = run(micro, mapping, out, args.micro_sheet, args.mapping_sheet,
                     cfg, split_by, dry_run=args.dry_run)
    except InputError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    summary = result.master["Agent Summary"]
    quality = result.master["Data Quality"]
    print(f"\nAgents analysed      : {len(summary)}")
    if not summary.empty:
        print(f"Interactions         : {int(summary['Interactions'].sum()):,}")
        print(f"Reached calls        : {int(summary['Reached_Calls'].sum()):,}")
        print(f"Mean utilisation     : {summary['Utilisation_Pure'].mean():.1%}")
        print(f"Agents below target  : {int(summary['Met_Target'].eq('Below').sum())}")
    print(f"Agents unmapped      : {result.unmapped_agents}")
    print(f"Watchlist entries    : {len(result.master['Watchlist'])}")
    print(f"Load notes           : {len(result.notes)} (see the Data Quality sheet)")

    rec = result.reconciliation
    if rec["slices"]:
        status = "OK" if rec["ok"] else "MISMATCH"
        print(
            f"Reconciliation       : {status} - {rec['interactions_slices']:,} of "
            f"{rec['interactions_master']:,} interactions across {rec['slices']} slices"
        )

    if args.dry_run:
        print("\nDry run - nothing written. Sheets that would be produced:")
        for name, frame in result.master.items():
            if frame is not None and not frame.empty:
                print(f"  {name:<22} {len(frame):>6,} rows")
        return 0

    print(f"\nFiles written        : {len(result.files)}")
    for label, path in result.files.items():
        print(f"  {label:<28} {path.name}")

    if args.fail_on_unmapped is not None and result.unmapped_agents > args.fail_on_unmapped:
        missing = quality.loc[quality["Check"].eq("Agents missing from mapping"), "Detail"]
        print(
            f"\nFAILED: {result.unmapped_agents} agents are missing from the mapping "
            f"(limit {args.fail_on_unmapped}).",
            file=sys.stderr,
        )
        if len(missing):
            print(f"  {missing.iat[0]}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
