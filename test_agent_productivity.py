"""Tests for agent_productivity.

These lock down the numbers people are appraised on. Every test that names a
version ("the old ... turned X into Y") is a regression guard for a defect that
actually shipped, so the comment is the reason the test exists.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

import agent_productivity as ap
from agent_productivity import CONFIG, Config, InputError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def micro_row(**overrides: object) -> dict:
    """One interaction row with sane defaults; override what the test is about."""
    row = {
        "Queue": "Consumer Care",
        "Identifier": "C1",
        "Call 1 - Trial 0": 1,
        "Interval": "9:00",
        "Login ID": "E1",
        "SPV": "Export SPV",
        "INTERACTION_ID": "I1",
        "START_TS_TIME": dt.datetime(2026, 2, 2, 9, 0, 0),
        "END_TS_TIME": dt.datetime(2026, 2, 2, 9, 5, 0),
        "Handle Time": "00:01:00",
        "Total Duration (Fmt)": "00:02:00",
        "Customer Handle Time (Fmt)": "00:01:00",
        "Customer Dial Time (Fmt)": 10,
        "Customer Hold Time (Fmt)": "00:00:10",
        "Customer Wrap Time (Fmt)": "00:00:00",
        "QUEUE_TIME": 5,
    }
    row.update(overrides)
    return row


def mapping_frame(**overrides: object) -> pd.DataFrame:
    row = {
        "Login ID": "E1", "Agent": "Ann Lee", "Team": "Alpha",
        "Business Type": "Consumer", "SPV": "Mapped SPV",
    }
    row.update(overrides)
    return pd.DataFrame([row])


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("01:02:03", 3723.0),
    ("02:30", 150.0),
    ("  00:00:45  ", 45.0),
    ("90", 90.0),
    (dt.timedelta(minutes=2, seconds=5), 125.0),
    (pd.Timedelta(seconds=61), 61.0),
    (dt.time(1, 0, 30), 3630.0),
    (125, 125.0),
])
def test_to_seconds_shapes(value, expected):
    assert ap.to_seconds(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["", "   ", "nan", "none", "-", None, float("nan"), "banana"])
def test_to_seconds_unparsable(value):
    assert np.isnan(ap.to_seconds(value))


def test_day_fraction_only_when_the_column_says_so():
    # Excel writes a time cell as a fraction of a day: 0.5 means 12 hours.
    assert ap.to_seconds(0.5, day_fraction=True) == pytest.approx(43200.0)
    # The same 0.5 in a seconds column is half a second, not half a day.
    assert ap.to_seconds(0.5, day_fraction=False) == pytest.approx(0.5)


def test_column_decides_day_fraction_not_the_cell():
    """The old per-cell guess turned a 0.5 second duration into 12 hours."""
    excel_times = pd.DataFrame({"d": [0.25, 0.5, 0.125]})
    seconds = pd.DataFrame({"d": [0.5, 120, 45]})
    assert ap.column_is_day_fraction(excel_times["d"]) is True
    assert ap.column_is_day_fraction(seconds["d"]) is False
    assert ap.seconds_series(excel_times, "d").iloc[1] == pytest.approx(43200.0)
    assert ap.seconds_series(seconds, "d").iloc[0] == pytest.approx(0.5)


def test_seconds_series_missing_column_is_zero():
    out = ap.seconds_series(pd.DataFrame({"a": [1, 2]}), "nope")
    assert out.tolist() == [0.0, 0.0]


def test_fmt_hms():
    assert ap.fmt_hms(3723) == "01:02:03"
    assert ap.fmt_hms(0) == "00:00:00"
    assert ap.fmt_hms(float("nan")) == ""


def test_interval_minutes_sorts_naturally():
    """A plain string sort puts "9:00" after "10:00"."""
    labels = ["10:00", "9:00", "13:30", "8:00"]
    assert sorted(labels, key=ap.interval_minutes) == ["8:00", "9:00", "10:00", "13:30"]
    assert ap.interval_minutes("rubbish") == float("inf")


# ---------------------------------------------------------------------------
# Business type inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("queue,expected", [
    # Regression: bare "ent"/"nt" substrings matched all of these.
    ("Retention", "Other"),
    ("Payments", "Other"),
    ("Contact Centre", "Other"),
    ("Internal Support", "Other"),
    ("Account Management", "Other"),
    # Genuine matches, including the abbreviations, on a word boundary.
    ("ENT Care", "Enterprise"),
    ("Enterprise Sales", "Enterprise"),
    ("B2B Desk", "Enterprise"),
    ("Consumer Care", "Consumer"),
    ("CONS Hotline", "Consumer"),
    ("Internet ADSL", "Internet"),
    ("Fibre Support", "Internet"),
    ("NON_TELECOM Desk", "Non Telecom"),
    ("NT Partners", "Non Telecom"),
    ("", "Other"),
    (None, "Other"),
])
def test_business_type_from_queue(queue, expected):
    assert ap.business_type_from_queue(queue, CONFIG) == expected


def test_threshold_and_target_lookup_is_punctuation_insensitive():
    cfg = Config()
    assert cfg.threshold_for("Consumer") == 59
    assert cfg.threshold_for("CONSUMER") == 59
    assert cfg.target_for("non_telecom") == cfg.target_for("Non Telecom") == 0.33
    # Anything unrecognised falls back, and says so.
    assert cfg.target_for("Enterprise - SME") == cfg.utilisation_target
    assert cfg.has_explicit_target("Enterprise - SME") is False
    assert cfg.has_explicit_target("ENTERPRISE") is True


# ---------------------------------------------------------------------------
# prepare()
# ---------------------------------------------------------------------------


def test_mapping_spv_wins_over_the_export_column():
    """Regression: 'SPV' exists in both files, so a merge suffix hid the mapping's."""
    frame = ap.prepare(pd.DataFrame([micro_row()]), mapping_frame())
    assert frame["SPV_Final"].iat[0] == "Mapped SPV"


def test_unmapped_agent_falls_back_without_losing_the_row():
    frame = ap.prepare(pd.DataFrame([micro_row(**{"Login ID": "E999"})]), mapping_frame())
    assert frame["Agent"].iat[0] == "E999"
    assert frame["Team"].iat[0] == "Unmapped"
    assert frame["SPV_Final"].iat[0] == "Export SPV"   # export is the fallback
    assert bool(frame["Is_Mapped"].iat[0]) is False
    assert frame["Business_Type"].iat[0] == "Consumer"  # inferred from the queue


@pytest.mark.parametrize("handle,business,expected", [
    ("00:00:59", "Consumer", 0),    # strictly greater than 59
    ("00:01:00", "Consumer", 1),
    ("00:00:49", "Enterprise", 0),  # strictly greater than 49
    ("00:00:50", "Enterprise", 1),
])
def test_reached_flag_boundary(handle, business, expected):
    frame = ap.prepare(
        pd.DataFrame([micro_row(**{"Customer Handle Time (Fmt)": handle})]),
        mapping_frame(**{"Business Type": business}),
    )
    assert int(frame["Reached_Flag"].iat[0]) == expected


def test_call_flag_is_clipped_to_zero_or_one():
    rows = [micro_row(**{"Call 1 - Trial 0": v}) for v in (1, 0, 2, "x")]
    frame = ap.prepare(pd.DataFrame(rows), mapping_frame())
    assert frame["Is_Call"].tolist() == [1, 0, 1, 0]
    assert frame["Is_Trial"].tolist() == [0, 1, 0, 1]


def test_acw_prefers_measured_wrap_time():
    rows = [
        micro_row(**{"Customer Wrap Time (Fmt)": "00:02:00"}),   # measured
        micro_row(**{"Customer Wrap Time (Fmt)": "00:00:00"}),   # assumed
    ]
    frame = ap.prepare(pd.DataFrame(rows), mapping_frame())
    assert frame["ACW_Sec"].tolist() == [120.0, 180.0]
    assert frame["ACW_Source"].tolist() == ["Measured", "Assumed"]


def test_acw_can_be_forced_back_to_the_assumption():
    cfg = ap.dataclasses.replace(CONFIG, use_measured_acw=False)
    frame = ap.prepare(
        pd.DataFrame([micro_row(**{"Customer Wrap Time (Fmt)": "00:02:00"})]),
        mapping_frame(), cfg,
    )
    assert frame["ACW_Sec"].iat[0] == pytest.approx(180.0)


def test_blank_queue_survives_preparation():
    rows = [micro_row(Queue=""), micro_row(Queue=np.nan)]
    frame = ap.prepare(pd.DataFrame(rows), mapping_frame())
    assert len(frame) == 2
    assert frame["Business_Type"].tolist() == ["Consumer", "Consumer"]  # from the mapping


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------


def test_utilisation_arithmetic_is_exact():
    rows = [micro_row(), micro_row(**{"START_TS_TIME": dt.datetime(2026, 2, 2, 10, 0)})]
    frame = ap.prepare(pd.DataFrame(rows), mapping_frame())
    day = ap.agent_day(frame)
    # Productive = dial (10 + 10) + total duration (120 + 120)
    assert day["Productive_Sec"].iat[0] == pytest.approx(260.0)
    assert day["Utilisation_Pure"].iat[0] == pytest.approx(260.0 / (7.5 * 3600))


def test_aht_means_the_same_thing_on_every_sheet():
    """Regression: agent sheets divided by calls, roll-ups by interactions."""
    rows = [micro_row(), micro_row(**{"Call 1 - Trial 0": 0}), micro_row()]
    frame = ap.prepare(pd.DataFrame(rows), mapping_frame())
    summary = ap.agent_summary(ap.agent_day(frame), frame)
    queue = ap.by_dimension(frame, "Queue")
    interval = ap.interval_profile(frame)

    assert summary["AHT_Sec"].iat[0] == pytest.approx(queue["AHT_Sec"].iat[0])
    assert summary["AHT_Sec"].iat[0] == pytest.approx(interval["AHT_Sec"].iat[0])
    # 3 rows of 60s handle over 2 answered calls.
    assert summary["AHT_Sec"].iat[0] == pytest.approx(180.0 / 2)
    # The per-interaction figure is still available, under its own name.
    assert queue["Avg_Handle_Per_Interaction_Sec"].iat[0] == pytest.approx(180.0 / 3)


def test_utilisation_target_only_on_business_type_rollups():
    """A queue has no utilisation target; printing the fallback implies it does."""
    frame = ap.prepare(pd.DataFrame([micro_row()]), mapping_frame())
    assert "Utilisation_Target" not in ap.by_dimension(frame, "Queue").columns
    assert "Utilisation_Target" not in ap.by_dimension(frame, "Team").columns
    assert "Utilisation_Target" in ap.by_dimension(frame, "Business_Type").columns


def test_unique_long_calls_counts_distinct_identifiers():
    rows = [
        micro_row(Identifier="C1"),                     # same call, two rows
        micro_row(Identifier="C1"),
        micro_row(Identifier="C2"),
        micro_row(Identifier="C3", **{"Customer Handle Time (Fmt)": "00:00:05"}),  # not reached
    ]
    frame = ap.prepare(pd.DataFrame(rows), mapping_frame())
    summary = ap.agent_summary(ap.agent_day(frame), frame)
    assert int(summary["Reached_Calls"].iat[0]) == 3
    assert int(summary["Unique_Long_Calls"].iat[0]) == 2


def test_agent_interval_matrix_keys_on_login_not_display_name():
    """Two agents can share a display name; they must stay two rows."""
    rows = [micro_row(**{"Login ID": "E1"}), micro_row(**{"Login ID": "E2"})]
    mapping = pd.DataFrame([
        {"Login ID": "E1", "Agent": "Same Name", "Team": "A", "Business Type": "Consumer", "SPV": "S"},
        {"Login ID": "E2", "Agent": "Same Name", "Team": "B", "Business Type": "Consumer", "SPV": "S"},
    ])
    matrix = ap.agent_interval_matrix(ap.prepare(pd.DataFrame(rows), mapping))
    assert len(matrix) == 2
    assert set(matrix["Agent_Key"]) == {"E1", "E2"}


# ---------------------------------------------------------------------------
# Targets and outliers
# ---------------------------------------------------------------------------


def test_apply_targets_verdicts():
    out = pd.DataFrame({
        "Business_Type": ["Consumer", "Consumer", "Internet"],
        "Utilisation_Pure": [0.40, 0.20, np.nan],
    })
    out = ap.apply_targets(out, CONFIG)
    assert out["Target"].tolist() == [0.35, 0.35, 0.30]
    assert out["Met_Target"].tolist() == ["Met", "Below", ""]
    assert out["Vs_Target"].iloc[0] == pytest.approx(0.05)


def test_apply_targets_scoped_drops_the_verdict():
    """A slice contribution cannot answer "did this agent meet their target"."""
    out = pd.DataFrame({
        "Business_Type": ["Consumer"],
        "Utilisation_Contribution": [0.14],
        "Vs_Target": [999.0],      # stale values from an earlier pass
        "Met_Target": ["Met"],
    })
    out = ap.apply_targets(out, CONFIG, scoped=True)
    assert "Met_Target" not in out.columns
    assert "Vs_Target" not in out.columns
    assert out["Share_of_Target_%"].iat[0] == pytest.approx(0.14 / 0.35)


def base_summary(**overrides) -> pd.DataFrame:
    frame = pd.DataFrame({
        "Agent": ["A", "B", "C"],
        "Team": ["T", "T", "T"],
        "Business_Type": ["Consumer"] * 3,
        "Active_Days": [10, 10, 10],
        "Interactions": [100, 100, 100],
        "Reached_Calls": [70, 70, 70],
        "Utilisation_Pure": [0.50, 0.50, 0.50],
        "Utilisation_With_ACW": [0.60, 0.60, 0.60],
        "Reach_Rate": [0.7, 0.7, 0.7],
        "AHT_Sec": [100.0, 100.0, 100.0],
        "AHT": ["00:01:40"] * 3,
        "Calls_Per_Day": [10.0, 10.0, 10.0],
        "Target": [0.35] * 3,
        "Vs_Target": [0.15] * 3,
        "Met_Target": ["Met"] * 3,
    })
    for key, value in overrides.items():
        frame[key] = value
    return frame


def test_outliers_survive_zero_and_nan_variance():
    """Regression: `std in (0, np.nan)` never catches a NaN std."""
    assert ap.outliers(base_summary(), CONFIG).empty              # zero variance
    assert ap.outliers(base_summary(AHT_Sec=np.nan), CONFIG).empty  # NaN std


def test_outliers_flag_a_genuine_low_utilisation():
    frame = base_summary(Utilisation_Pure=[0.50, 0.50, 0.10], Met_Target=["Met", "Met", "Below"])
    watch = ap.outliers(frame, CONFIG)
    assert list(watch["Agent"]) == ["C"]
    assert "Below utilisation target (35%)" in watch["Flags"].iat[0]


def test_scoped_outliers_do_not_flag_everyone():
    """A single queue's contribution is structurally below a whole-agent target."""
    frame = base_summary().rename(columns={"Utilisation_Pure": "Utilisation_Contribution"})
    frame["Utilisation_Contribution"] = [0.10, 0.11, 0.12]
    assert ap.outliers(frame, CONFIG, scoped=True).empty


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_align_headers_renames_and_records():
    notes = ap.Notes()
    frame = pd.DataFrame(columns=["login_id", "Queue", "CUSTOMER HANDLE TIME (FMT)"])
    out = ap.align_headers(frame, ap.expected_micro_columns(CONFIG), notes)
    assert "Login ID" in out.columns
    assert "Customer Handle Time (Fmt)" in out.columns
    assert len(notes) == 2


def test_align_headers_refuses_to_create_duplicate_columns():
    notes = ap.Notes()
    frame = pd.DataFrame(columns=["login_id", "LOGIN ID"])
    out = ap.align_headers(frame, ["Login ID"], notes)
    assert list(out.columns).count("Login ID") == 1
    assert any("clash" in c for c, _ in notes.entries)


def test_load_mapping_finds_the_right_tab_and_dedupes(tmp_path):
    path = tmp_path / "Mapping.xlsx"
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        pd.DataFrame({"Read me": ["not the mapping"]}).to_excel(
            writer, sheet_name="Instructions", index=False)
        pd.DataFrame([
            {"Login_ID": "E1", "Agent": "First Wins", "Team": "A",
             "Business Type": "Consumer", "SPV": "S"},
            {"Login_ID": "E1", "Agent": "Duplicate", "Team": "Z",
             "Business Type": "Enterprise", "SPV": "S"},
            {"Login_ID": None, "Agent": None, "Team": None,
             "Business Type": None, "SPV": None},
        ]).to_excel(writer, sheet_name="Agent Mapping", index=False)
    notes = ap.Notes()
    mapping = ap.load_mapping(path, None, CONFIG, notes)
    assert len(mapping) == 1
    assert mapping["Agent"].iat[0] == "First Wins"
    assert any("mapping tab" in c for c, _ in notes.entries)


def test_load_mapping_without_the_key_column_explains_itself(tmp_path):
    path = tmp_path / "Mapping.xlsx"
    pd.DataFrame({"Something Else": ["x"]}).to_excel(path, index=False, engine="xlsxwriter")
    with pytest.raises(InputError) as excinfo:
        ap.load_mapping(path, None, CONFIG)
    assert "Login ID" in str(excinfo.value)
    assert "--mapping-sheet" in str(excinfo.value)


def test_parse_overrides():
    assert ap.parse_overrides("Consumer=0.35, Internet=0.3", float, "targets") == {
        "Consumer": 0.35, "Internet": 0.3}
    with pytest.raises(InputError):
        ap.parse_overrides("Consumer", float, "targets")


def test_unique_path_never_overwrites_another_slice(tmp_path):
    """Punctuation is flattened, so distinct queues can reduce to one name."""
    used: set[str] = set()
    first = ap.unique_path(tmp_path, "Report", "Consumer/Cairo", used)
    second = ap.unique_path(tmp_path, "Report", "Consumer Cairo", used)
    assert first.name == "Report_Consumer_Cairo.xlsx"
    assert second.name == "Report_Consumer_Cairo_2.xlsx"


def test_unique_path_handles_truncation_collisions(tmp_path):
    """Names are cut at 40 characters; two queues can agree up to there."""
    used: set[str] = set()
    long_a = "Enterprise Support Desk Cairo Region Alpha North"
    long_b = "Enterprise Support Desk Cairo Region Alpha South"
    first = ap.unique_path(tmp_path, "Report", long_a, used)
    second = ap.unique_path(tmp_path, "Report", long_b, used)
    assert first != second
    assert second.name.endswith("_2.xlsx")


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.fixture
def workbook(tmp_path):
    """A small two-queue month, with one agent working both queues."""
    rows = []
    for login, queue in (("E1", "Consumer Care"), ("E2", "Enterprise Sales")):
        for day in (2, 3, 4):
            for hour in range(5):
                start = dt.datetime(2026, 2, day, 9 + hour, 0)
                rows.append(micro_row(**{
                    "Login ID": login, "Queue": queue,
                    "Identifier": f"C{login}{day}{hour}",
                    "INTERACTION_ID": f"I{login}{day}{hour}",
                    "START_TS_TIME": start,
                    "END_TS_TIME": start + dt.timedelta(minutes=3),
                    "Interval": f"{9 + hour}:00",
                }))
    # E3 splits their month across both queues plus one blank-queue row, so the
    # slice files only add back up to the master if none of that is dropped.
    for day, queue in ((2, "Consumer Care"), (3, "Enterprise Sales"), (4, "")):
        start = dt.datetime(2026, 2, day, 11, 0)
        rows.append(micro_row(**{
            "Login ID": "E3", "Queue": queue,
            "Identifier": f"CE3{day}", "INTERACTION_ID": f"IE3{day}",
            "START_TS_TIME": start, "END_TS_TIME": start + dt.timedelta(minutes=3),
            "Interval": "11:00",
        }))

    micro_path = tmp_path / "micro.xlsx"
    pd.DataFrame(rows).to_excel(micro_path, index=False, engine="xlsxwriter")

    mapping_path = tmp_path / "Agent Mapping.xlsx"
    with pd.ExcelWriter(mapping_path, engine="xlsxwriter") as writer:
        pd.DataFrame({"Read me": ["skip this tab"]}).to_excel(
            writer, sheet_name="Instructions", index=False)
        pd.DataFrame([
            {"Login_ID": "E1", "Agent": "Ann", "Team": "Alpha",
             "Business Type": "Consumer", "SPV": "Sam"},
            {"Login_ID": "E2", "Agent": "Ben", "Team": "Beta",
             "Business Type": "Enterprise", "SPV": "Sam"},
            {"Login_ID": "E3", "Agent": "Cara", "Team": "Alpha",
             "Business Type": "Consumer", "SPV": "Sam"},
        ]).to_excel(writer, sheet_name="Agent Mapping", index=False)
        pd.DataFrame([
            {"Queue": "Consumer Care", "Queue Group": "Care", "Channel": "Voice"},
        ]).to_excel(writer, sheet_name="Queue Mapping", index=False)
    return micro_path, mapping_path, tmp_path


def test_run_writes_master_and_one_file_per_slice(workbook):
    micro, mapping, folder = workbook
    result = ap.run(micro, mapping, folder / "Report.xlsx", split_by="Queue")
    assert result.files["MASTER"].exists()
    # Consumer Care, Enterprise Sales and the blank-queue rows.
    assert set(result.files) == {"MASTER", "Consumer Care", "Enterprise Sales", ap.BLANK_SLICE}
    for path in result.files.values():
        assert path.exists() and path.stat().st_size > 0


def test_no_row_is_lost_when_the_split_column_is_blank(workbook):
    """Regression: dropna() on the split column made blank-queue rows vanish."""
    micro, mapping, folder = workbook
    result = ap.run(micro, mapping, folder / "Report.xlsx", split_by="Queue")
    rec = result.reconciliation
    assert rec["ok"] is True
    assert rec["interactions_slices"] == rec["interactions_master"] == 33


def test_slice_contributions_sum_to_the_master_utilisation(workbook):
    """The whole point of the slice denominator: the parts add up."""
    micro, mapping, folder = workbook
    result = ap.run(micro, mapping, folder / "Report.xlsx", split_by="Queue")

    master = result.master["Agent Summary"].set_index("Agent_Key")["Utilisation_Pure"]
    totals: dict[str, float] = {}
    for label, path in result.files.items():
        if label == "MASTER":
            continue
        sheet = pd.read_excel(path, sheet_name="Agent Summary")
        for _, row in sheet.iterrows():
            totals[row["Agent_Key"]] = totals.get(row["Agent_Key"], 0.0) + \
                row["Utilisation_Contribution"]

    assert set(totals) == set(master.index)
    for agent, total in totals.items():
        assert total == pytest.approx(master[agent], abs=1e-9)


def test_slice_files_carry_no_stale_verdict(workbook):
    """Regression: Met_Target kept its whole-month value after the rescale."""
    micro, mapping, folder = workbook
    result = ap.run(micro, mapping, folder / "Report.xlsx", split_by="Queue")
    sheet = pd.read_excel(result.files["Consumer Care"], sheet_name="Agent Summary")
    assert "Met_Target" not in sheet.columns
    assert "Vs_Target" not in sheet.columns
    assert "Utilisation_Pure" not in sheet.columns
    assert "Utilisation_Contribution" in sheet.columns
    assert "Share_of_Agent_%" in sheet.columns


def test_split_is_refused_under_the_span_basis(workbook):
    """The slice denominator is scheduled hours; span is not divisible that way."""
    micro, mapping, folder = workbook
    cfg = ap.dataclasses.replace(CONFIG, utilisation_basis="span")
    with pytest.raises(InputError) as excinfo:
        ap.run(micro, mapping, folder / "Report.xlsx", cfg=cfg, split_by="Queue")
    assert "utilisation-basis shift" in str(excinfo.value)


def test_split_on_an_unknown_column_lists_the_real_ones(workbook):
    micro, mapping, folder = workbook
    with pytest.raises(InputError) as excinfo:
        ap.run(micro, mapping, folder / "Report.xlsx", split_by="Nope")
    assert "Available split columns" in str(excinfo.value)


def test_dry_run_writes_nothing(workbook):
    micro, mapping, folder = workbook
    result = ap.run(micro, mapping, folder / "Report.xlsx", split_by="Queue", dry_run=True)
    assert not any(path.exists() for path in result.files.values())
    assert not result.master["Agent Summary"].empty


def test_master_workbook_has_every_expected_sheet(workbook):
    micro, mapping, folder = workbook
    out = folder / "Report.xlsx"
    ap.run(micro, mapping, out, split_by=None)
    sheets = pd.ExcelFile(out).sheet_names
    for expected in ("Config", "Agent Summary", "Agent x Day", "Interval Profile",
                     "Daily Trend", "By Queue", "By Team", "By Business Type",
                     "By SPV", "By Queue Group", "Data Quality"):
        assert expected in sheets
    # Raw second columns exist for the arithmetic but never reach the reader.
    summary = pd.read_excel(out, sheet_name="Agent Summary")
    assert not (set(summary.columns) & ap.DROP_HELPERS)


def test_lookup_gaps_are_reported_not_hidden(workbook):
    """Only Consumer Care is in the Queue Mapping sheet."""
    micro, mapping, folder = workbook
    result = ap.run(micro, mapping, folder / "Report.xlsx", split_by=None)
    quality = result.master["Data Quality"]
    assert quality["Check"].str.contains("Load note").any()
    assert quality["Detail"].str.contains("Unmapped").any()
    groups = result.master["By Queue Group"]
    assert set(groups["Queue_Group"]) == {"Care", "Unmapped"}


def test_cli_end_to_end(workbook, capsys):
    micro, mapping, folder = workbook
    code = ap.main([
        str(micro), "--mapping", str(mapping), "--out", str(folder / "CLI.xlsx"),
        "--targets", "Consumer=0.10,Enterprise=0.09", "--thresholds", "Consumer=30",
        "--split-by", "Team",
    ])
    assert code == 0
    printed = capsys.readouterr().out
    assert "Reconciliation       : OK" in printed
    assert "Consumer 10%" in printed
    assert (folder / "CLI.xlsx").exists()


def test_fail_on_unmapped_gates_the_run(workbook):
    micro, mapping, folder = workbook
    # Every agent is mapped here, so the gate passes at 0...
    assert ap.main([str(micro), "--mapping", str(mapping),
                    "--out", str(folder / "Gate.xlsx"), "--no-split",
                    "--fail-on-unmapped", "0"]) == 0
    # ...and fails once the mapping is taken away.
    assert ap.main([str(micro), "--out", str(folder / "Gate2.xlsx"), "--no-split",
                    "--fail-on-unmapped", "0"]) == 2


def test_missing_input_reports_cleanly(tmp_path, capsys):
    assert ap.main([str(tmp_path / "nope.xlsx"), "--no-split"]) == 1
    assert "Input not found" in capsys.readouterr().err
