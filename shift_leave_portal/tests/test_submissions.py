"""Submission validation, overlap rules, status changes, audit and attachments."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from database.connection import session_scope
from database.models import AuditLog, Submission, User
from services import attachment_service
from services import submission_service as svc
from services.attachment_service import UploadedFileData
from services.audit_service import AuditAction
from services.errors import ConflictError, PermissionDeniedError, ValidationError
from tests.conftest import leave_payload, local, outage_payload
from utils.datetime_utils import combine_local, now_utc, to_local

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"0" * 64


def create_outage(actor, **kw):
    with session_scope(write=True) as s:
        return svc.create_outage(s, actor, outage_payload(**kw))


def create_leave(actor, **kw):
    with session_scope(write=True) as s:
        return svc.create_leave(s, actor, leave_payload(**kw))


def set_status(admin, sub_id, status, **kw):
    with session_scope(write=True) as s:
        return svc.change_status(s, admin, sub_id, {"new_status": status, **kw})


def audit_actions(entity_id=None):
    with session_scope() as s:
        stmt = select(AuditLog.action).order_by(AuditLog.id)
        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_type == "submission", AuditLog.entity_id == str(entity_id))
        return list(s.scalars(stmt))


# ----------------------------------------------------------------- validation
def test_create_valid_outage(advisor):
    view = create_outage(advisor)
    assert view.status == "pending" and view.submission_type == "outage"
    assert view.impact_level == "medium" and view.user_id == advisor.id
    assert view.start.tzinfo is not None
    assert audit_actions(view.id) == [AuditAction.SUBMISSION_CREATED]


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"impact_level": None}, "Impact level"),
        ({"description": "   "}, "Description"),
        ({"description": "too short"}, "at least 10"),
        ({"reason_category": "Made up"}, "listed options"),
        ({"outage_channel": "Carrier pigeon"}, "listed options"),
        ({"ticket_reference": "<script>"}, "Ticket reference"),
    ],
)
def test_outage_field_validation(advisor, overrides, message):
    with pytest.raises(ValidationError) as exc:
        create_outage(advisor, **overrides)
    assert any(message in d for d in exc.value.details + [exc.value.message])


def test_outage_end_must_be_after_start(advisor):
    start = local(-1, 10)
    with pytest.raises(ValidationError, match="after the start"):
        with session_scope(write=True) as s:
            svc.create_outage(s, advisor, outage_payload(start=start, end=start))
    with pytest.raises(ValidationError, match="after the start"):
        with session_scope(write=True) as s:
            svc.create_outage(s, advisor, outage_payload(start=start, end=start - timedelta(minutes=5)))


def test_outage_cannot_be_in_future_or_too_long_or_too_old(advisor):
    with pytest.raises(ValidationError, match="future"):
        create_outage(advisor, start=now_utc() + timedelta(hours=2))
    with pytest.raises(ValidationError, match="longer than"):
        create_outage(advisor, minutes=25 * 60)
    with pytest.raises(ValidationError, match="older than"):
        create_outage(advisor, start=local(-60, 10))


def test_naive_datetimes_are_rejected(advisor):
    naive = datetime(2026, 1, 1, 10, 0)
    with pytest.raises(ValidationError) as exc:
        with session_scope(write=True) as s:
            svc.create_outage(s, advisor, outage_payload(start=naive, end=naive + timedelta(hours=1)))
    assert all("timezone" in d for d in exc.value.details)


def test_text_is_sanitised(advisor):
    view = create_outage(advisor, description="  VPN\x00 dropped\x07 again and again  ", ticket_reference=" INC-1 ")
    assert view.description == "VPN dropped again and again"
    assert view.ticket_reference == "INC-1"


def test_leave_requires_reason_and_valid_dates(advisor):
    with pytest.raises(ValidationError):
        create_leave(advisor, reason_category="")
    with pytest.raises(ValidationError):
        create_leave(advisor, description="")
    with pytest.raises(ValidationError, match="after"):
        create_leave(advisor, days=0)
    with pytest.raises(ValidationError, match="longer than"):
        create_leave(advisor, days=61)
    with pytest.raises(ValidationError, match="in the past"):
        create_leave(advisor, first_day_offset=-40)


def test_datetimes_stored_in_utc_and_displayed_in_cairo(advisor):
    view = create_leave(advisor, first_day_offset=5, days=1)
    start_local = to_local(view.start)
    assert start_local.hour == 0 and str(start_local.tzinfo) == "Africa/Cairo"
    assert view.start.utcoffset() == timedelta(0)
    assert view.start == combine_local(start_local.date(), start_local.time())
    assert view.leave_days == 1


# ----------------------------------------------------------------- overlaps
def test_overlapping_leave_rejected(advisor):
    create_leave(advisor, first_day_offset=5, days=3)  # days 5,6,7
    with pytest.raises(ConflictError, match="overlaps"):
        create_leave(advisor, first_day_offset=7, days=2)
    with pytest.raises(ConflictError):
        create_leave(advisor, first_day_offset=4, days=5)  # fully covers


def test_adjacent_leave_is_allowed(advisor):
    create_leave(advisor, first_day_offset=5, days=3)
    create_leave(advisor, first_day_offset=8, days=1)  # starts exactly when the first ends


def test_overlap_is_per_user(advisor, other_advisor):
    create_leave(advisor, first_day_offset=5, days=3)
    create_leave(other_advisor, first_day_offset=5, days=3)


@pytest.mark.parametrize("inactive_status", ["cancelled", "rejected"])
def test_cancelled_and_rejected_leave_do_not_block(admin, advisor, inactive_status):
    first = create_leave(advisor, first_day_offset=5, days=3)
    set_status(admin, first.id, inactive_status)
    second = create_leave(advisor, first_day_offset=5, days=3)
    assert second.status == "pending"


def test_partial_day_leave_overlaps_full_day(advisor):
    create_leave(advisor, first_day_offset=5, days=1)
    with pytest.raises(ConflictError):
        create_leave(advisor, all_day=False, start=local(5, 9), end=local(5, 12))
    create_leave(advisor, all_day=False, start=local(6, 9), end=local(6, 12))


def test_overlapping_outage_rejected_unless_allowed_by_admin(admin, advisor, make_user):
    create_outage(advisor, start=local(-1, 10), minutes=60)
    with pytest.raises(ConflictError, match="overlaps"):
        create_outage(advisor, start=local(-1, 10, 30), minutes=60)
    # Admin explicitly allows overlapping outages for this advisor.
    with session_scope(write=True) as s:
        u = s.get(User, advisor.id)
        svc_user = {"full_name": u.full_name, "employee_id": u.employee_id, "email": u.email,
                    "role": "advisor", "team": u.team, "allow_overlapping_outages": True}
        from services import user_service

        user_service.update_user(s, admin, advisor.id, svc_user)
    view = create_outage(advisor, start=local(-1, 10, 30), minutes=60)
    assert view.overlap_override is True


def test_outage_and_leave_do_not_block_each_other(advisor):
    create_outage(advisor, start=local(-1, 10))
    create_leave(advisor, all_day=False, start=local(-1, 9), end=local(-1, 17))


# ----------------------------------------------------------------- edit / cancel
def test_advisor_can_edit_and_cancel_pending(advisor):
    view = create_outage(advisor)
    with session_scope(write=True) as s:
        updated = svc.update_own_submission(s, advisor, view.id, outage_payload(impact_level="high"))
    assert updated.impact_level == "high"
    with session_scope(write=True) as s:
        cancelled = svc.cancel_submission(s, advisor, view.id, "Logged by mistake")
    assert cancelled.status == "cancelled"
    actions = audit_actions(view.id)
    assert actions == [AuditAction.SUBMISSION_CREATED, AuditAction.SUBMISSION_UPDATED, AuditAction.SUBMISSION_CANCELLED]


def test_edit_does_not_conflict_with_itself(advisor):
    view = create_leave(advisor, first_day_offset=5, days=3)
    with session_scope(write=True) as s:
        svc.update_own_submission(s, advisor, view.id, leave_payload(first_day_offset=6, days=3))


def test_advisor_cannot_edit_or_cancel_processed_records(admin, advisor):
    view = create_leave(advisor)
    set_status(admin, view.id, "approved")
    with pytest.raises(PermissionDeniedError):
        with session_scope(write=True) as s:
            svc.update_own_submission(s, advisor, view.id, leave_payload(days=1))
    with pytest.raises(PermissionDeniedError):
        with session_scope(write=True) as s:
            svc.cancel_submission(s, advisor, view.id)


# ----------------------------------------------------------------- status changes
def test_approve_sets_reviewer_and_audits(admin, advisor):
    view = create_leave(advisor)
    approved = set_status(admin, view.id, "approved", admin_comment="Enjoy!")
    assert approved.status == "approved"
    assert approved.reviewer_name == admin.full_name
    assert approved.reviewed_at is not None and approved.admin_comment == "Enjoy!"
    with session_scope() as s:
        log = s.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.SUBMISSION_STATUS_CHANGED)
        ).one()
    assert log.actor_user_id == admin.id
    assert log.old_values["status"] == "pending" and log.new_values["status"] == "approved"


def test_every_status_change_is_audited(admin, advisor):
    view = create_outage(advisor)
    for status in ("approved", "resolved", "pending", "rejected", "pending", "cancelled"):
        set_status(admin, view.id, status)
    assert audit_actions(view.id).count(AuditAction.SUBMISSION_STATUS_CHANGED) == 6


def test_invalid_transitions_rejected(admin, advisor):
    leave = create_leave(advisor)
    with pytest.raises(ConflictError):
        set_status(admin, leave.id, "resolved")  # resolved is for outages only
    set_status(admin, leave.id, "rejected")
    with pytest.raises(ConflictError):
        set_status(admin, leave.id, "approved")  # must reopen first
    with pytest.raises(ConflictError, match="already"):
        set_status(admin, leave.id, "rejected")


def test_reopen_rechecks_overlap(admin, advisor):
    first = create_leave(advisor, first_day_offset=5, days=2)
    set_status(admin, first.id, "cancelled")
    create_leave(advisor, first_day_offset=5, days=2)
    with pytest.raises(ConflictError, match="overlaps"):
        set_status(admin, first.id, "pending")


def test_reopen_clears_reviewer(admin, advisor):
    view = create_outage(advisor)
    set_status(admin, view.id, "approved")
    reopened = set_status(admin, view.id, "pending")
    assert reopened.reviewer_name is None and reopened.reviewed_at is None


def test_admin_update_and_delete(admin, advisor):
    view = create_outage(advisor)
    with session_scope(write=True) as s:
        updated = svc.admin_update_submission(
            s, admin, view.id, {"impact_level": "low", "manager_comment": "Checked with IT"}
        )
    assert updated.impact_level == "low" and updated.manager_comment == "Checked with IT"
    with session_scope(write=True) as s:
        svc.delete_submission(s, admin, view.id)
    with session_scope() as s:
        assert s.get(Submission, view.id) is None
    assert audit_actions(view.id)[-1] == AuditAction.SUBMISSION_DELETED


# ----------------------------------------------------------------- attachments
def test_attachments_stored_validated_and_removed(app_env, admin, advisor):
    with session_scope(write=True) as s:
        view = svc.create_outage(
            s, advisor, outage_payload(),
            [UploadedFileData("screen shot.png", PNG), UploadedFileData("../../etc/report.pdf", PDF)],
        )
    names = sorted(a.filename for a in view.attachments)
    assert names == ["report.pdf", "screen shot.png"]
    files = list((app_env / "uploads").iterdir())
    assert len(files) == 2 and all(f.stat().st_size > 0 for f in files)

    with session_scope(write=True) as s:
        filename, mime, content = attachment_service.get_attachment_content(s, advisor, view.attachments[0].id)
    assert content in (PNG, PDF)

    with session_scope(write=True) as s:
        svc.delete_submission(s, admin, view.id)
    assert list((app_env / "uploads").iterdir()) == []


@pytest.mark.parametrize(
    "upload, message",
    [
        (UploadedFileData("evil.png", b"MZ\x90\x00 not really a png"), "not an allowed"),
        (UploadedFileData("image.exe", PNG), "not an allowed"),
        (UploadedFileData("empty.pdf", b""), "empty"),
        (UploadedFileData("huge.png", PNG + b"0" * (1024 * 1024 + 1)), "larger than"),
    ],
)
def test_bad_attachments_rejected(advisor, upload, message):
    with pytest.raises(ValidationError, match=message):
        with session_scope(write=True) as s:
            svc.create_outage(s, advisor, outage_payload(), [upload])


def test_files_removed_when_transaction_rolls_back(app_env, advisor):
    with pytest.raises(RuntimeError):
        with session_scope(write=True) as s:
            svc.create_outage(s, advisor, outage_payload(), [UploadedFileData("a.png", PNG)])
            raise RuntimeError("simulated failure after the file was written")
    uploads = app_env / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []
    with session_scope() as s:
        assert s.scalar(select(Submission.id)) is None


def test_utc_type_rejects_naive_values(advisor):
    from sqlalchemy.exc import StatementError

    with pytest.raises(StatementError, match="timezone-aware"):
        with session_scope(write=True) as s:
            s.add(Submission(user_id=advisor.id, submission_type="outage", status="pending",
                             start_datetime=datetime(2026, 1, 1), end_datetime=datetime(2026, 1, 2),
                             reason_category="Other", description="x" * 20, impact_level="low"))
            s.flush()


def test_database_constraints_enforced(advisor):
    from sqlalchemy.exc import IntegrityError

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(IntegrityError):
        with session_scope(write=True) as s:
            s.add(Submission(user_id=advisor.id, submission_type="outage", status="pending",
                             start_datetime=start, end_datetime=start + timedelta(hours=1),
                             reason_category="Other", description="no impact level"))
            s.flush()
    with pytest.raises(IntegrityError):
        with session_scope(write=True) as s:
            s.add(Submission(user_id=advisor.id, submission_type="annual_leave", status="bogus",
                             start_datetime=start, end_datetime=start + timedelta(hours=1),
                             reason_category="Vacation", description="bad status"))
            s.flush()
