"""Outage and annual-leave submissions: creation, editing, review and queries.

All business rules (validation, overlap prevention, status transitions and
authorisation) are enforced here, independent of the UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, aliased, contains_eager, selectinload

from config.settings import get_settings
from database.models import (
    ACTIVE_STATUSES,
    Submission,
    SubmissionStatus,
    SubmissionType,
    User,
)
from database.schemas import (
    AdminSubmissionUpdate,
    AuthUser,
    LeaveInput,
    OutageInput,
    StatusChange,
    SubmissionFilters,
    friendly_errors,
)
from services import attachment_service, audit_service
from services.attachment_service import AttachmentView, UploadedFileData
from services.audit_service import AuditAction
from services.errors import ConflictError, NotFoundError, ValidationError
from utils import validators
from utils.datetime_utils import (
    format_datetime,
    local_day_bounds_utc,
    now_utc,
    to_local,
    today_local,
)
from utils.permissions import (
    is_admin,
    require_admin,
    require_advisor,
    require_modify_submission,
    require_view_submission,
)

logger = logging.getLogger(__name__)

# Small tolerance for clock differences when rejecting "future" outages.
FUTURE_TOLERANCE = timedelta(minutes=5)
LEAVE_MAX_FUTURE_DAYS = 366

# Allowed admin status transitions per submission type.
_COMMON_TRANSITIONS: dict[SubmissionStatus, set[SubmissionStatus]] = {
    SubmissionStatus.PENDING: {SubmissionStatus.APPROVED, SubmissionStatus.REJECTED, SubmissionStatus.CANCELLED},
    SubmissionStatus.APPROVED: {SubmissionStatus.REJECTED, SubmissionStatus.CANCELLED, SubmissionStatus.PENDING},
    SubmissionStatus.REJECTED: {SubmissionStatus.PENDING},
    SubmissionStatus.CANCELLED: {SubmissionStatus.PENDING},
    SubmissionStatus.RESOLVED: {SubmissionStatus.PENDING},
}
REVIEW_STATUSES = frozenset({SubmissionStatus.APPROVED, SubmissionStatus.REJECTED, SubmissionStatus.RESOLVED})

SORT_OPTIONS: dict[str, Any] = {
    "start_desc": (Submission.start_datetime.desc(), Submission.id.desc()),
    "start_asc": (Submission.start_datetime.asc(), Submission.id.asc()),
    "created_desc": (Submission.created_at.desc(), Submission.id.desc()),
    "created_asc": (Submission.created_at.asc(), Submission.id.asc()),
}


def allowed_transitions(submission_type: str, current: str) -> list[SubmissionStatus]:
    """Return the statuses an admin may move a submission to."""
    current_status = SubmissionStatus(current)
    targets = set(_COMMON_TRANSITIONS.get(current_status, set()))
    if submission_type == SubmissionType.OUTAGE.value and current_status in {
        SubmissionStatus.PENDING,
        SubmissionStatus.APPROVED,
    }:
        targets.add(SubmissionStatus.RESOLVED)
    order = list(SubmissionStatus)
    return sorted(targets, key=order.index)


# --------------------------------------------------------------------------- #
# Read models
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SubmissionView:
    """Read model of a submission joined with its owner and reviewer."""

    id: int
    user_id: int
    username: str
    full_name: str
    employee_id: str
    team: str | None
    submission_type: str
    status: str
    start: datetime
    end: datetime
    all_day: bool
    reason_category: str
    description: str
    notes: str | None
    impact_level: str | None
    outage_channel: str | None
    ticket_reference: str | None
    manager_comment: str | None
    admin_comment: str | None
    overlap_override: bool
    reviewer_name: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentView] = field(default_factory=list)

    @property
    def last_day(self) -> datetime:
        """For full-day leave, the inclusive last day (end is exclusive midnight)."""
        return self.end - timedelta(days=1) if self.all_day else self.end

    @property
    def duration(self) -> timedelta:
        """Length of the submission window."""
        return self.end - self.start

    @property
    def leave_days(self) -> int:
        """Number of calendar days for full-day leave (0 for partial-day)."""
        if not self.all_day:
            return 0
        start_local, end_local = to_local(self.start), to_local(self.end)
        assert start_local is not None and end_local is not None
        return (end_local.date() - start_local.date()).days


def to_view(sub: Submission) -> SubmissionView:
    return SubmissionView(
        id=sub.id,
        user_id=sub.user_id,
        username=sub.user.username,
        full_name=sub.user.full_name,
        employee_id=sub.user.employee_id,
        team=sub.user.team,
        submission_type=sub.submission_type,
        status=sub.status,
        start=sub.start_datetime,
        end=sub.end_datetime,
        all_day=sub.all_day,
        reason_category=sub.reason_category,
        description=sub.description,
        notes=sub.notes,
        impact_level=sub.impact_level,
        outage_channel=sub.outage_channel,
        ticket_reference=sub.ticket_reference,
        manager_comment=sub.manager_comment,
        admin_comment=sub.admin_comment,
        overlap_override=sub.overlap_override,
        reviewer_name=sub.reviewer.full_name if sub.reviewer else None,
        reviewed_at=sub.reviewed_at,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
        attachments=[attachment_service.to_view(a) for a in sub.attachments],
    )


def _snapshot(sub: Submission) -> dict[str, Any]:
    return {
        "submission_type": sub.submission_type,
        "status": sub.status,
        "start_datetime": sub.start_datetime,
        "end_datetime": sub.end_datetime,
        "all_day": sub.all_day,
        "reason_category": sub.reason_category,
        "description": sub.description,
        "notes": sub.notes,
        "impact_level": sub.impact_level,
        "outage_channel": sub.outage_channel,
        "ticket_reference": sub.ticket_reference,
        "manager_comment": sub.manager_comment,
        "admin_comment": sub.admin_comment,
        "overlap_override": sub.overlap_override,
        "reviewed_by": sub.reviewed_by,
        "reviewed_at": sub.reviewed_at,
    }


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def _parse(model: type, data: Any) -> Any:
    if isinstance(data, model):
        return data
    try:
        return model(**data)
    except PydanticValidationError as exc:
        errors = friendly_errors(exc)
        raise ValidationError(errors[0] if len(errors) == 1 else "Please correct the following problems.", errors) from exc


def _check_advisor_time_window(submission_type: str, start: datetime, end: datetime) -> None:
    """Time-window rules applied to advisor-created/edited records."""
    settings = get_settings()
    now = now_utc()
    if submission_type == SubmissionType.OUTAGE.value:
        if start > now + FUTURE_TOLERANCE:
            raise ValidationError("An outage cannot start in the future. Please check the date and times.")
        if start < now - timedelta(days=settings.outage_max_past_days):
            raise ValidationError(
                f"Outages older than {settings.outage_max_past_days} days cannot be submitted. "
                "Please contact your supervisor."
            )
    else:
        earliest, _ = local_day_bounds_utc(today_local() - timedelta(days=settings.leave_max_past_days), today_local())
        if start < earliest:
            raise ValidationError(
                f"Leave cannot start more than {settings.leave_max_past_days} days in the past."
            )
        if start > now + timedelta(days=LEAVE_MAX_FUTURE_DAYS):
            raise ValidationError("Leave can be requested at most one year in advance.")


def find_overlap(
    session: Session,
    *,
    user_id: int,
    submission_type: str,
    start: datetime,
    end: datetime,
    exclude_id: int | None = None,
) -> Submission | None:
    """Return an *active* record of the same type that overlaps ``[start, end)``.

    Cancelled and rejected records are ignored. Intervals that merely touch
    (one ends exactly when the other starts) do not overlap.
    """
    stmt = select(Submission).where(
        Submission.user_id == user_id,
        Submission.submission_type == submission_type,
        Submission.status.in_([s.value for s in ACTIVE_STATUSES]),
        Submission.start_datetime < end,
        Submission.end_datetime > start,
    )
    if exclude_id is not None:
        stmt = stmt.where(Submission.id != exclude_id)
    return session.scalars(stmt.order_by(Submission.start_datetime).limit(1)).first()


def _overlap_message(existing: Submission) -> str:
    label = "leave request" if existing.submission_type == SubmissionType.ANNUAL_LEAVE.value else "outage"
    return (
        f"This overlaps with your existing {label} #{existing.id} "
        f"({format_datetime(existing.start_datetime)} – {format_datetime(existing.end_datetime)}, "
        f"{existing.status}). Please adjust the dates or edit the existing record."
    )


def _enforce_no_overlap(
    session: Session,
    *,
    owner: User,
    submission_type: str,
    start: datetime,
    end: datetime,
    exclude_id: int | None,
    admin_override: bool,
) -> bool:
    """Raise :class:`ConflictError` on a forbidden overlap.

    Returns True when an overlap exists but was explicitly permitted (outages
    only — overlapping annual leave is never allowed).
    """
    existing = find_overlap(
        session, user_id=owner.id, submission_type=submission_type, start=start, end=end, exclude_id=exclude_id
    )
    if existing is None:
        return False
    if submission_type == SubmissionType.OUTAGE.value and (admin_override or owner.allow_overlapping_outages):
        return True
    raise ConflictError(_overlap_message(existing))


def _get_submission(session: Session, submission_id: int) -> Submission:
    stmt = (
        select(Submission)
        .where(Submission.id == submission_id)
        .options(selectinload(Submission.user), selectinload(Submission.reviewer), selectinload(Submission.attachments))
    )
    sub = session.scalars(stmt).first()
    if sub is None:
        raise NotFoundError()
    return sub


# --------------------------------------------------------------------------- #
# Advisor operations
# --------------------------------------------------------------------------- #
def create_outage(
    session: Session,
    actor: AuthUser,
    data: OutageInput | dict[str, Any],
    attachments: list[UploadedFileData] | None = None,
    ip_address: str | None = None,
) -> SubmissionView:
    """Create an outage submission for the acting advisor."""
    require_advisor(actor)
    payload: OutageInput = _parse(OutageInput, data)
    attachments = attachments or []
    attachment_service.validate_uploads(attachments)
    owner = session.get(User, actor.id)
    if owner is None or not owner.is_active:
        raise NotFoundError("Your account could not be found.")
    _check_advisor_time_window(SubmissionType.OUTAGE.value, payload.start, payload.end)
    overlap_allowed = _enforce_no_overlap(
        session,
        owner=owner,
        submission_type=SubmissionType.OUTAGE.value,
        start=payload.start,
        end=payload.end,
        exclude_id=None,
        admin_override=False,
    )
    sub = Submission(
        user=owner,
        submission_type=SubmissionType.OUTAGE.value,
        status=SubmissionStatus.PENDING.value,
        start_datetime=payload.start,
        end_datetime=payload.end,
        all_day=False,
        reason_category=payload.reason_category,
        description=payload.description,
        impact_level=payload.impact_level.value,
        outage_channel=payload.outage_channel,
        ticket_reference=payload.ticket_reference,
        overlap_override=overlap_allowed,
    )
    session.add(sub)
    session.flush()
    if attachments:
        attachment_service.store_attachments(session, sub, attachments, uploaded_by=actor.id)
        session.flush()
    audit_service.record(
        session,
        action=AuditAction.SUBMISSION_CREATED,
        entity_type="submission",
        entity_id=sub.id,
        actor_id=actor.id,
        new_values={**_snapshot(sub), "attachments": len(attachments)},
        ip_address=ip_address,
    )
    session.refresh(sub)
    return to_view(sub)


def create_leave(
    session: Session,
    actor: AuthUser,
    data: LeaveInput | dict[str, Any],
    ip_address: str | None = None,
) -> SubmissionView:
    """Create an annual-leave request for the acting advisor."""
    require_advisor(actor)
    payload: LeaveInput = _parse(LeaveInput, data)
    owner = session.get(User, actor.id)
    if owner is None or not owner.is_active:
        raise NotFoundError("Your account could not be found.")
    _check_advisor_time_window(SubmissionType.ANNUAL_LEAVE.value, payload.start, payload.end)
    _enforce_no_overlap(
        session,
        owner=owner,
        submission_type=SubmissionType.ANNUAL_LEAVE.value,
        start=payload.start,
        end=payload.end,
        exclude_id=None,
        admin_override=False,
    )
    sub = Submission(
        user=owner,
        submission_type=SubmissionType.ANNUAL_LEAVE.value,
        status=SubmissionStatus.PENDING.value,
        start_datetime=payload.start,
        end_datetime=payload.end,
        all_day=payload.all_day,
        reason_category=payload.reason_category,
        description=payload.description,
        notes=payload.notes,
    )
    session.add(sub)
    session.flush()
    audit_service.record(
        session,
        action=AuditAction.SUBMISSION_CREATED,
        entity_type="submission",
        entity_id=sub.id,
        actor_id=actor.id,
        new_values=_snapshot(sub),
        ip_address=ip_address,
    )
    session.refresh(sub)
    return to_view(sub)


def update_own_submission(
    session: Session,
    actor: AuthUser,
    submission_id: int,
    data: OutageInput | LeaveInput | dict[str, Any],
    ip_address: str | None = None,
) -> SubmissionView:
    """Let an advisor edit one of their own *pending* submissions."""
    require_advisor(actor)
    sub = _get_submission(session, submission_id)
    require_modify_submission(actor, sub)
    is_outage = sub.submission_type == SubmissionType.OUTAGE.value
    payload = _parse(OutageInput if is_outage else LeaveInput, data)
    _check_advisor_time_window(sub.submission_type, payload.start, payload.end)
    overlap_allowed = _enforce_no_overlap(
        session,
        owner=sub.user,
        submission_type=sub.submission_type,
        start=payload.start,
        end=payload.end,
        exclude_id=sub.id,
        admin_override=False,
    )
    before = _snapshot(sub)
    sub.start_datetime = payload.start
    sub.end_datetime = payload.end
    sub.reason_category = payload.reason_category
    sub.description = payload.description
    if is_outage:
        sub.impact_level = payload.impact_level.value
        sub.outage_channel = payload.outage_channel
        sub.ticket_reference = payload.ticket_reference
        sub.overlap_override = overlap_allowed
    else:
        sub.all_day = payload.all_day
        sub.notes = payload.notes
    session.flush()
    old, new = audit_service.diff(before, _snapshot(sub))
    if new:
        audit_service.record(
            session,
            action=AuditAction.SUBMISSION_UPDATED,
            entity_type="submission",
            entity_id=sub.id,
            actor_id=actor.id,
            old_values=old,
            new_values=new,
            ip_address=ip_address,
        )
    return to_view(sub)


def cancel_submission(
    session: Session,
    actor: AuthUser,
    submission_id: int,
    reason: str | None = None,
    ip_address: str | None = None,
) -> SubmissionView:
    """Cancel a submission.

    Advisors may cancel only their own pending records. Admins may cancel any
    record whose current status allows it.
    """
    sub = _get_submission(session, submission_id)
    require_modify_submission(actor, sub)
    if is_admin(actor):
        return change_status(
            session,
            actor,
            submission_id,
            StatusChange(new_status=SubmissionStatus.CANCELLED, admin_comment=reason or sub.admin_comment),
            ip_address=ip_address,
        )
    note = validators.optional_text(reason, max_length=500, multiline=True)
    before = {"status": sub.status}
    sub.status = SubmissionStatus.CANCELLED.value
    session.flush()
    audit_service.record(
        session,
        action=AuditAction.SUBMISSION_CANCELLED,
        entity_type="submission",
        entity_id=sub.id,
        actor_id=actor.id,
        old_values=before,
        new_values={"status": sub.status, "reason": note},
        ip_address=ip_address,
    )
    return to_view(sub)


# --------------------------------------------------------------------------- #
# Admin operations
# --------------------------------------------------------------------------- #
def change_status(
    session: Session,
    actor: AuthUser,
    submission_id: int,
    change: StatusChange | dict[str, Any],
    ip_address: str | None = None,
) -> SubmissionView:
    """Approve, reject, resolve, cancel or reopen a submission. Admin only.

    Every change writes an audit record. Re-activating a record (moving it out
    of cancelled/rejected) re-checks overlaps.
    """
    require_admin(actor)
    change = _parse(StatusChange, change)
    sub = _get_submission(session, submission_id)
    current = SubmissionStatus(sub.status)
    target = change.new_status
    if target == current:
        raise ConflictError(f"This submission is already {current.value}.")
    if target not in allowed_transitions(sub.submission_type, current.value):
        raise ConflictError(f"A {current.value} submission cannot be changed to {target.value}.")

    if current not in ACTIVE_STATUSES and target in ACTIVE_STATUSES:
        sub.overlap_override = _enforce_no_overlap(
            session,
            owner=sub.user,
            submission_type=sub.submission_type,
            start=sub.start_datetime,
            end=sub.end_datetime,
            exclude_id=sub.id,
            admin_override=change.allow_overlap,
        ) or sub.overlap_override

    before = _snapshot(sub)
    sub.status = target.value
    if change.admin_comment is not None:
        sub.admin_comment = change.admin_comment
    if target == SubmissionStatus.PENDING:
        sub.reviewed_by = None
        sub.reviewed_at = None
    else:
        sub.reviewed_by = actor.id
        sub.reviewed_at = now_utc()
    session.flush()
    session.refresh(sub)
    old, new = audit_service.diff(before, _snapshot(sub))
    audit_service.record(
        session,
        action=AuditAction.SUBMISSION_STATUS_CHANGED,
        entity_type="submission",
        entity_id=sub.id,
        actor_id=actor.id,
        old_values=old,
        new_values=new,
        ip_address=ip_address,
    )
    return to_view(sub)


def admin_update_submission(
    session: Session,
    actor: AuthUser,
    submission_id: int,
    update: AdminSubmissionUpdate | dict[str, Any],
    ip_address: str | None = None,
) -> SubmissionView:
    """Edit any field of any submission. Admin only.

    The merged record is re-validated with the same schema advisors use, and
    overlap rules still apply unless ``allow_overlap`` is set (outages only).
    """
    require_admin(actor)
    update = _parse(AdminSubmissionUpdate, update)
    sub = _get_submission(session, submission_id)
    is_outage = sub.submission_type == SubmissionType.OUTAGE.value

    def pick(name: str, current: Any) -> Any:
        value = getattr(update, name)
        return current if value is None else value

    merged: dict[str, Any] = {
        "start": pick("start", sub.start_datetime),
        "end": pick("end", sub.end_datetime),
        "reason_category": pick("reason_category", sub.reason_category),
        "description": pick("description", sub.description),
    }
    if is_outage:
        merged |= {
            "impact_level": pick("impact_level", sub.impact_level),
            "outage_channel": pick("outage_channel", sub.outage_channel),
            "ticket_reference": pick("ticket_reference", sub.ticket_reference),
        }
        payload: OutageInput | LeaveInput = _parse(OutageInput, merged)
    else:
        merged |= {"all_day": pick("all_day", sub.all_day), "notes": pick("notes", sub.notes)}
        payload = _parse(LeaveInput, merged)

    overlap_allowed = sub.overlap_override
    if sub.status in {s.value for s in ACTIVE_STATUSES}:
        overlap_allowed = _enforce_no_overlap(
            session,
            owner=sub.user,
            submission_type=sub.submission_type,
            start=payload.start,
            end=payload.end,
            exclude_id=sub.id,
            admin_override=update.allow_overlap,
        )

    before = _snapshot(sub)
    sub.start_datetime = payload.start
    sub.end_datetime = payload.end
    sub.reason_category = payload.reason_category
    sub.description = payload.description
    sub.overlap_override = overlap_allowed
    if isinstance(payload, OutageInput):
        sub.impact_level = payload.impact_level.value
        sub.outage_channel = payload.outage_channel
        sub.ticket_reference = payload.ticket_reference
    else:
        sub.all_day = payload.all_day
        sub.notes = payload.notes
    if update.manager_comment is not None:
        sub.manager_comment = update.manager_comment or None
    if update.admin_comment is not None:
        sub.admin_comment = update.admin_comment or None
    session.flush()
    old, new = audit_service.diff(before, _snapshot(sub))
    if new:
        audit_service.record(
            session,
            action=AuditAction.SUBMISSION_UPDATED,
            entity_type="submission",
            entity_id=sub.id,
            actor_id=actor.id,
            old_values=old,
            new_values=new,
            ip_address=ip_address,
        )
    return to_view(sub)


def delete_submission(
    session: Session, actor: AuthUser, submission_id: int, ip_address: str | None = None
) -> None:
    """Permanently delete a submission and its attachments. Admin only."""
    require_admin(actor)
    sub = _get_submission(session, submission_id)
    snapshot = {**_snapshot(sub), "user_id": sub.user_id, "attachments": len(sub.attachments)}
    attachment_service.schedule_file_removal(session, sub)
    session.delete(sub)
    session.flush()
    audit_service.record(
        session,
        action=AuditAction.SUBMISSION_DELETED,
        entity_type="submission",
        entity_id=submission_id,
        actor_id=actor.id,
        old_values=snapshot,
        ip_address=ip_address,
    )


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def get_submission(session: Session, actor: AuthUser, submission_id: int) -> SubmissionView:
    """Return one submission if ``actor`` may see it (else NotFoundError)."""
    sub = _get_submission(session, submission_id)
    require_view_submission(actor, sub)
    return to_view(sub)


def build_filtered_query(filters: SubmissionFilters, *, restrict_user_id: int | None = None) -> Select[Any]:
    """Build a parameterised SELECT for submissions matching ``filters``.

    ``restrict_user_id`` forces the query to a single owner (used for
    advisors so they can never see others' data, whatever the filters say).
    """
    owner = aliased(User, name="owner")
    stmt = select(Submission).join(owner, Submission.user_id == owner.id).options(
        contains_eager(Submission.user.of_type(owner)),
        selectinload(Submission.reviewer),
        selectinload(Submission.attachments),
    )
    conditions = []
    if restrict_user_id is not None:
        conditions.append(Submission.user_id == restrict_user_id)
    elif filters.user_ids:
        conditions.append(Submission.user_id.in_(filters.user_ids))
    if filters.teams and restrict_user_id is None:
        conditions.append(owner.team.in_(filters.teams))
    if filters.submission_types:
        conditions.append(Submission.submission_type.in_(filters.submission_types))
    if filters.statuses:
        conditions.append(Submission.status.in_(filters.statuses))
    if filters.impact_levels:
        conditions.append(Submission.impact_level.in_(filters.impact_levels))
    if filters.date_from or filters.date_to:
        start_day = filters.date_from or filters.date_to
        end_day = filters.date_to or filters.date_from
        assert start_day is not None and end_day is not None
        if end_day < start_day:
            start_day, end_day = end_day, start_day
        window_start, window_end = local_day_bounds_utc(start_day, end_day)
        conditions.append(and_(Submission.start_datetime < window_end, Submission.end_datetime > window_start))
    term = validators.sanitize_text(filters.search, max_length=100) if filters.search else ""
    if term:
        conditions.append(
            or_(
                owner.employee_id.icontains(term, autoescape=True),
                owner.username.icontains(term, autoescape=True),
                owner.full_name.icontains(term, autoescape=True),
                Submission.ticket_reference.icontains(term, autoescape=True),
                Submission.description.icontains(term, autoescape=True),
            )
        )
    if conditions:
        stmt = stmt.where(and_(*conditions))
    return stmt


def _run_query(
    session: Session, stmt: Select[Any], *, sort: str, limit: int | None, offset: int
) -> tuple[list[SubmissionView], int]:
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    stmt = stmt.order_by(*SORT_OPTIONS.get(sort, SORT_OPTIONS["start_desc"]))
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    rows = session.scalars(stmt).unique().all()
    return [to_view(r) for r in rows], total


def list_my_submissions(
    session: Session,
    actor: AuthUser,
    filters: SubmissionFilters | None = None,
    *,
    sort: str = "start_desc",
    limit: int | None = 500,
    offset: int = 0,
) -> tuple[list[SubmissionView], int]:
    """List the actor's own submissions (any role)."""
    stmt = build_filtered_query(filters or SubmissionFilters(), restrict_user_id=actor.id)
    return _run_query(session, stmt, sort=sort, limit=limit, offset=offset)


def search_submissions(
    session: Session,
    actor: AuthUser,
    filters: SubmissionFilters,
    *,
    sort: str = "start_desc",
    limit: int | None = 50,
    offset: int = 0,
) -> tuple[list[SubmissionView], int]:
    """Search all submissions with filters and pagination. Admin only."""
    require_admin(actor)
    return _run_query(session, build_filtered_query(filters), sort=sort, limit=limit, offset=offset)
