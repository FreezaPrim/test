"""User management (admin only) and user look-ups."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.models import Role, User
from database.schemas import AuthUser, UserCreate, UserUpdate, friendly_errors
from services import audit_service
from services.audit_service import AuditAction
from services.errors import ConflictError, NotFoundError, ValidationError
from utils import validators
from utils.datetime_utils import now_utc
from utils.permissions import require_admin
from utils.security import generate_temporary_password, hash_password

logger = logging.getLogger(__name__)


class ConfirmationRequiredError(ConflictError):
    """The operation needs explicit confirmation (e.g. removing your own admin role)."""


@dataclass(frozen=True)
class UserRow:
    """Safe read model of a user (never includes the password hash)."""

    id: int
    username: str
    full_name: str
    employee_id: str
    email: str | None
    role: str
    team: str | None
    is_active: bool
    must_change_password: bool
    allow_overlapping_outages: bool
    is_locked: bool
    created_at: datetime
    last_login_at: datetime | None


def _to_row(user: User) -> UserRow:
    now = now_utc()
    return UserRow(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        employee_id=user.employee_id,
        email=user.email,
        role=user.role,
        team=user.team,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        allow_overlapping_outages=user.allow_overlapping_outages,
        is_locked=bool(user.locked_until and user.locked_until > now),
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _snapshot(user: User) -> dict[str, object]:
    return {
        "username": user.username,
        "full_name": user.full_name,
        "employee_id": user.employee_id,
        "email": user.email,
        "role": user.role,
        "team": user.team,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "allow_overlapping_outages": user.allow_overlapping_outages,
    }


def count_active_admins(session: Session) -> int:
    """Return the number of active admin accounts."""
    return session.scalar(
        select(func.count(User.id)).where(User.role == Role.ADMIN.value, User.is_active.is_(True))
    ) or 0


def _get_user_or_404(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    return user


def _ensure_unique(
    session: Session, *, username: str | None, employee_id: str, email: str | None, exclude_id: int | None = None
) -> None:
    problems: list[str] = []
    checks: list[tuple[str, object, str]] = [("employee_id", employee_id, "This employee ID is already in use.")]
    if username is not None:
        checks.insert(0, ("username", username, "This username is already taken."))
    if email:
        checks.append(("email", email, "This e-mail address is already in use."))
    for column, value, message in checks:
        stmt = select(User.id).where(getattr(User, column) == value)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        if session.scalar(stmt) is not None:
            problems.append(message)
    if problems:
        raise ConflictError(problems[0], problems)


def list_users(
    session: Session,
    actor: AuthUser,
    *,
    search: str | None = None,
    role: str | None = None,
    active: bool | None = None,
) -> list[UserRow]:
    """List users matching the optional filters. Admin only."""
    require_admin(actor)
    stmt = select(User)
    term = validators.sanitize_text(search, max_length=100) if search else ""
    if term:
        like = f"%{term.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.username).like(like),
                func.lower(User.full_name).like(like),
                func.lower(User.employee_id).like(like),
                func.lower(func.coalesce(User.email, "")).like(like),
                func.lower(func.coalesce(User.team, "")).like(like),
            )
        )
    if role:
        stmt = stmt.where(User.role == role)
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    return [_to_row(u) for u in session.scalars(stmt.order_by(User.full_name, User.id))]


def get_user(session: Session, actor: AuthUser, user_id: int) -> UserRow:
    """Return one user. Admin only."""
    require_admin(actor)
    return _to_row(_get_user_or_404(session, user_id))


def list_teams(session: Session, actor: AuthUser) -> list[str]:
    """Return all distinct team names. Admin only."""
    require_admin(actor)
    return [t for t in session.scalars(select(User.team).where(User.team.is_not(None)).distinct().order_by(User.team))]


def list_advisor_choices(session: Session, actor: AuthUser) -> list[tuple[int, str]]:
    """Return ``(id, label)`` pairs for advisor filters. Admin only."""
    require_admin(actor)
    rows = session.execute(
        select(User.id, User.full_name, User.employee_id)
        .where(User.role == Role.ADVISOR.value)
        .order_by(User.full_name)
    ).all()
    return [(row.id, f"{row.full_name} ({row.employee_id})") for row in rows]


def create_user(
    session: Session, actor: AuthUser | None, data: UserCreate | dict, ip_address: str | None = None
) -> tuple[UserRow, str | None]:
    """Create a user.

    Args:
        actor: the admin performing the action. ``None`` is only allowed for
            the bootstrap/CLI seed path (see ``database.seed``).

    Returns:
        ``(user, temporary_password)`` — the temporary password is returned
        only when one was generated, so the admin can hand it over once. It
        is never stored or logged in plain text.
    """
    if actor is not None:
        require_admin(actor)
    if isinstance(data, dict):
        try:
            data = UserCreate(**data)
        except PydanticValidationError as exc:
            errors = friendly_errors(exc)
            raise ValidationError(errors[0], errors) from exc

    _ensure_unique(session, username=data.username, employee_id=data.employee_id, email=data.email)
    temporary_password: str | None = None
    password = data.password
    if not password:
        temporary_password = password = generate_temporary_password()
    validators.validate_password(
        password, min_length=get_settings().password_min_length, username=data.username
    )
    now = now_utc()
    user = User(
        username=data.username,
        full_name=data.full_name,
        employee_id=data.employee_id,
        email=data.email,
        role=data.role.value,
        team=data.team,
        is_active=data.is_active,
        password_hash=hash_password(password),
        must_change_password=data.must_change_password or temporary_password is not None,
        allow_overlapping_outages=data.allow_overlapping_outages,
        password_changed_at=now,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConflictError("A user with the same username, employee ID or e-mail already exists.") from exc
    audit_service.record(
        session,
        action=AuditAction.USER_CREATED,
        entity_type="user",
        entity_id=user.id,
        actor_id=actor.id if actor else None,
        new_values=_snapshot(user),
        ip_address=ip_address,
    )
    return _to_row(user), temporary_password


def update_user(
    session: Session,
    actor: AuthUser,
    user_id: int,
    data: UserUpdate | dict,
    *,
    confirm_self_demotion: bool = False,
    ip_address: str | None = None,
) -> UserRow:
    """Update a user's profile, role and team. Admin only.

    Raises:
        ConflictError: demoting the last active admin.
        ConfirmationRequiredError: removing your own admin role without
            ``confirm_self_demotion=True``.
    """
    require_admin(actor)
    if isinstance(data, dict):
        try:
            data = UserUpdate(**data)
        except PydanticValidationError as exc:
            errors = friendly_errors(exc)
            raise ValidationError(errors[0], errors) from exc
    user = _get_user_or_404(session, user_id)
    demoting = user.role == Role.ADMIN.value and data.role != Role.ADMIN
    if demoting and user.is_active and count_active_admins(session) <= 1:
        raise ConflictError("You cannot remove the admin role from the last active admin.")
    if demoting and user.id == actor.id and not confirm_self_demotion:
        raise ConfirmationRequiredError(
            "You are about to remove your own admin access. Please confirm to continue."
        )
    _ensure_unique(session, username=None, employee_id=data.employee_id, email=data.email, exclude_id=user.id)

    before = _snapshot(user)
    user.full_name = data.full_name
    user.employee_id = data.employee_id
    user.email = data.email
    user.role = data.role.value
    user.team = data.team
    user.allow_overlapping_outages = data.allow_overlapping_outages
    try:
        session.flush()
    except IntegrityError as exc:
        raise ConflictError("Another user already uses this employee ID or e-mail.") from exc
    old, new = audit_service.diff(before, _snapshot(user))
    if new:
        audit_service.record(
            session,
            action=AuditAction.USER_UPDATED,
            entity_type="user",
            entity_id=user.id,
            actor_id=actor.id,
            old_values=old,
            new_values=new,
            ip_address=ip_address,
        )
    return _to_row(user)


def set_user_active(
    session: Session, actor: AuthUser, user_id: int, active: bool, ip_address: str | None = None
) -> UserRow:
    """Activate or deactivate a user. Admin only.

    Admins cannot deactivate themselves, and the last active admin can never
    be deactivated.
    """
    require_admin(actor)
    user = _get_user_or_404(session, user_id)
    if user.is_active == active:
        return _to_row(user)
    if not active:
        if user.id == actor.id:
            raise ConflictError("You cannot deactivate your own account.")
        if user.role == Role.ADMIN.value and count_active_admins(session) <= 1:
            raise ConflictError("You cannot deactivate the last active admin.")
    user.is_active = active
    session.flush()
    audit_service.record(
        session,
        action=AuditAction.USER_ACTIVATED if active else AuditAction.USER_DEACTIVATED,
        entity_type="user",
        entity_id=user.id,
        actor_id=actor.id,
        old_values={"is_active": not active},
        new_values={"is_active": active},
        ip_address=ip_address,
    )
    return _to_row(user)


def reset_password(
    session: Session,
    actor: AuthUser,
    user_id: int,
    *,
    new_password: str | None = None,
    force_change: bool = True,
    ip_address: str | None = None,
) -> str | None:
    """Reset a user's password. Admin only.

    Existing sessions of that user are invalidated. Returns the generated
    temporary password when ``new_password`` is not supplied.
    """
    require_admin(actor)
    user = _get_user_or_404(session, user_id)
    generated: str | None = None
    if not new_password:
        generated = new_password = generate_temporary_password()
    try:
        validators.validate_password(
            new_password, min_length=get_settings().password_min_length, username=user.username
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    user.password_hash = hash_password(new_password)
    user.must_change_password = force_change or generated is not None
    user.password_changed_at = now_utc()
    user.failed_login_attempts = 0
    user.locked_until = None
    session.flush()
    audit_service.record(
        session,
        action=AuditAction.PASSWORD_RESET,
        entity_type="user",
        entity_id=user.id,
        actor_id=actor.id,
        new_values={"must_change_password": user.must_change_password, "generated": generated is not None},
        ip_address=ip_address,
    )
    return generated


def unlock_user(session: Session, actor: AuthUser, user_id: int, ip_address: str | None = None) -> UserRow:
    """Clear a temporary login lockout. Admin only."""
    require_admin(actor)
    user = _get_user_or_404(session, user_id)
    user.locked_until = None
    user.failed_login_attempts = 0
    session.flush()
    audit_service.record(
        session,
        action=AuditAction.USER_UNLOCKED,
        entity_type="user",
        entity_id=user.id,
        actor_id=actor.id,
        ip_address=ip_address,
    )
    return _to_row(user)
