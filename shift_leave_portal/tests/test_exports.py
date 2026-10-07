"""CSV/Excel exports: columns, filters, injection protection, permissions, audit."""

from __future__ import annotations

import io
from datetime import timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from database.connection import session_scope
from database.models import AuditLog
from database.schemas import SubmissionFilters
from services import export_service
from services import submission_service as svc
from services.audit_service import AuditAction
from services.errors import PermissionDeniedError
from services.export_service import EXPORT_COLUMNS
from tests.conftest import leave_payload, local, outage_payload
from utils.datetime_utils import today_local


@pytest.fixture
def data(admin, advisor, other_advisor):
    with session_scope(write=True) as s:
        o1 = svc.create_outage(s, advisor, outage_payload(start=local(-2, 9), description="=HYPERLINK(\"http://x\") bad"))
        o2 = svc.create_outage(s, other_advisor, outage_payload(start=local(-1, 9), impact_level="high",
                                                                 ticket_reference="INC-999"))
        l1 = svc.create_leave(s, advisor, leave_payload(first_day_offset=10, days=2))
    with session_scope(write=True) as s:
        svc.change_status(s, admin, o2.id, {"new_status": "resolved", "admin_comment": "+fixed by IT"})
    return {"o1": o1, "o2": o2, "l1": l1}


def _df(admin, **filters):
    with session_scope() as s:
        return export_service.build_export_dataframe(s, admin, SubmissionFilters(**filters))


def test_export_has_required_columns(admin, data):
    df = _df(admin)
    assert list(df.columns) == EXPORT_COLUMNS
    assert len(df) == 3
    row = df[df["Submission ID"] == data["o2"].id].iloc[0]
    assert row["Status"] == "Resolved" and row["Reviewer"] == admin.full_name
    assert row["Team"] == "Support" and row["Impact level"] == "High"
    assert pd.notna(row["Reviewed date"]) and pd.notna(row["Created date"])


def test_export_datetimes_are_local_time(admin, data):
    df = _df(admin, submission_types=["outage"])
    starts = sorted(df["Start date/time"])
    assert starts[0].hour == 9 and starts[0].tzinfo is None


@pytest.mark.parametrize(
    "filters, expected",
    [
        ({"statuses": ["resolved"]}, {"o2"}),
        ({"submission_types": ["annual_leave"]}, {"l1"}),
        ({"impact_levels": ["high"]}, {"o2"}),
        ({"teams": ["Billing"]}, {"o1", "l1"}),
        ({"search": "INC-999"}, {"o2"}),
        ({"search": "EMP-002"}, {"o1", "l1"}),
        ({"date_from": "-2", "date_to": "-2"}, {"o1"}),
        ({"date_from": "9", "date_to": "30"}, {"l1"}),
    ],
)
def test_export_respects_filters(admin, advisor, data, filters, expected):
    resolved = {}
    for key, value in filters.items():
        if key.startswith("date_"):
            value = today_local() + timedelta(days=int(value))
        resolved[key] = value
    df = _df(admin, **resolved)
    assert set(df["Submission ID"]) == {data[k].id for k in expected}


def test_export_matches_admin_table(admin, data):
    filters = SubmissionFilters(submission_types=["outage"])
    with session_scope() as s:
        rows, total = svc.search_submissions(s, admin, filters, limit=None)
        df = export_service.build_export_dataframe(s, admin, filters)
    assert set(df["Submission ID"]) == {r.id for r in rows} and len(df) == total


def test_csv_export_neutralises_formulas(admin, data):
    with session_scope(write=True) as s:
        file = export_service.export_submissions(s, admin, SubmissionFilters(), "csv")
    assert file.mime == "text/csv" and file.filename.endswith(".csv") and file.row_count == 3
    df = pd.read_csv(io.BytesIO(file.data), encoding="utf-8-sig")
    assert list(df.columns) == EXPORT_COLUMNS
    descriptions = df["Description"].tolist()
    assert any(d.startswith("'=HYPERLINK") for d in descriptions)
    assert not any(str(d).startswith("=") for d in descriptions)
    assert "'+fixed by IT" in df["Admin comment"].tolist()


def test_excel_export_is_readable(admin, data):
    with session_scope(write=True) as s:
        file = export_service.export_submissions(s, admin, SubmissionFilters(statuses=["pending"]), "xlsx")
    df = pd.read_excel(io.BytesIO(file.data), engine="openpyxl")
    assert list(df.columns) == EXPORT_COLUMNS
    assert set(df["Submission ID"]) == {data["o1"].id, data["l1"].id}
    assert pd.api.types.is_datetime64_any_dtype(df["Start date/time"])


def test_export_is_audited(admin, data):
    with session_scope(write=True) as s:
        export_service.export_submissions(s, admin, SubmissionFilters(statuses=["pending"]), "csv")
    with session_scope() as s:
        log = s.scalars(select(AuditLog).where(AuditLog.action == AuditAction.DATA_EXPORTED)).one()
    assert log.actor_user_id == admin.id
    assert log.new_values["row_count"] == 2 and log.new_values["format"] == "csv"
    assert log.new_values["filters"]["statuses"] == ["pending"]


def test_advisor_cannot_export(advisor, data):
    with pytest.raises(PermissionDeniedError):
        with session_scope(write=True) as s:
            export_service.export_submissions(s, advisor, SubmissionFilters(), "xlsx")


def test_export_row_limit(admin, data, monkeypatch):
    import dataclasses

    from config.settings import get_settings
    from services.errors import ValidationError

    limited = dataclasses.replace(get_settings(), export_row_limit=2)
    monkeypatch.setattr(export_service, "get_settings", lambda: limited)
    with pytest.raises(ValidationError, match="narrow the filters"):
        _df(admin)
    assert len(_df(admin, statuses=["pending"])) == 2
