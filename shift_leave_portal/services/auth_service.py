"""Authentication: login, logout, password change and session re-validation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.models import User
from database.schemas import AuthUser
from services import audit_service
from services.audit_service import AuditAction
from services.errors import AuthenticationError, ValidationError
from utils import validators
from utils.datetime_utils import now_utc
from utils.security import hash_password, needs_rehash, verify_password

logger = logging.getLogger(__name__)

GENERIC_LOGIN_ERROR = (
    "Sign-in failed. Please check your username and password and try again. "
    "After several failed attempts, sign-in is paused for a few minutes."
)


@dataclass(frozen=True)
class LoginResult:
    """Outcome of a login attempt. ``user`` is set only on success."""

    user: AuthUser | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether authentication succeeded."""
        return self.user is not None


def to_auth_user(user: User, authenticated_at: datetime) -> AuthUser:
    """Build the session principal for ``user``."""
    return AuthUser(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        employee_id=user.employee_id,
        role=user.role,
        team=user.team,
        must_change_password=user.must_change_password,
        authenticated_at=authenticated_at,
    )


def _audit_failure(session: Session, user: User | None, attempted: str, reason: str, ip: str | None) -> None:
    audit_service.record(
        session,
        action=AuditAction.LOGIN_FAILURE,
        entity_type="user",
        entity_id=user.id if user else None,
        actor_id=user.id if user else None,
        new_values={"attempted_username": attempted, "reason": reason},
        ip_address=ip,
    )


def authenticate(
    session: Session, username: str, password: str, ip_address: str | None = None
) -> LoginResult:
    """Verify credentials and return a :class:`LoginResult`.

    The returned error message is identical for unknown users, wrong
    passwords, locked and inactive accounts so it cannot be used to discover
    valid usernames. A password hash verification is always performed (against
    a dummy hash for unknown users) to keep timing similar.

    Failed attempts increment a per-account counter; reaching
    ``MAX_LOGIN_ATTEMPTS`` locks the account for ``LOCKOUT_MINUTES``.
    The caller must commit the session even when login fails so that the
    counter and audit record persist.
    """
    settings = get_settings()
    now = now_utc()
    password = password or ""
    try:
        normalized = validators.validate_username(username)
    except ValueError:
        verify_password(None, password)
        # Never log free text that is not a well-formed username: users
        # sometimes type their password into the username field.
        _audit_failure(session, None, "<invalid format>", "invalid_username_format", ip_address)
        return LoginResult(None, GENERIC_LOGIN_ERROR)

    user = session.scalar(select(User).where(User.username == normalized))
    if user is None:
        verify_password(None, password)
        _audit_failure(session, None, normalized, "unknown_user", ip_address)
        return LoginResult(None, GENERIC_LOGIN_ERROR)

    if user.locked_until is not None and user.locked_until > now:
        verify_password(None, password)
        _audit_failure(session, user, normalized, "account_locked", ip_address)
        return LoginResult(None, GENERIC_LOGIN_ERROR)

    if not verify_password(user.password_hash, password):
        user.failed_login_attempts += 1
        reason = "bad_password"
        if user.failed_login_attempts >= settings.max_login_attempts:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_attempts = 0
            reason = "bad_password_account_locked"
            logger.warning("Account %s locked after repeated failed logins", user.id)
        _audit_failure(session, user, normalized, reason, ip_address)
        return LoginResult(None, GENERIC_LOGIN_ERROR)

    if not user.is_active:
        _audit_failure(session, user, normalized, "inactive_account", ip_address)
        return LoginResult(None, GENERIC_LOGIN_ERROR)

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    audit_service.record(
        session,
        action=AuditAction.LOGIN_SUCCESS,
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        ip_address=ip_address,
    )
    session.flush()
    return LoginResult(to_auth_user(user, now))


def logout(session: Session, actor: AuthUser, ip_address: str | None = None, *, expired: bool = False) -> None:
    """Record a logout (or an idle-timeout expiry) in the audit log."""
    audit_service.record(
        session,
        action=AuditAction.SESSION_EXPIRED if expired else AuditAction.LOGOUT,
        entity_type="user",
        entity_id=actor.id,
        actor_id=actor.id,
        ip_address=ip_address,
    )


def refresh_session_user(session: Session, auth_user: AuthUser) -> AuthUser | None:
    """Re-validate a session principal against the database.

    Returns ``None`` (forcing sign-out) when the account no longer exists, was
    deactivated, or its password was changed/reset after this session signed
    in. Otherwise returns an up-to-date principal (role/team changes apply
    immediately).
    """
    user = session.get(User, auth_user.id)
    if user is None or not user.is_active:
        return None
    if user.password_changed_at is not None and user.password_changed_at > auth_user.authenticated_at:
        return None
    return to_auth_user(user, auth_user.authenticated_at)


def change_password(
    session: Session,
    actor: AuthUser,
    current_password: str,
    new_password: str,
    ip_address: str | None = None,
) -> AuthUser:
    """Change the actor's own password after verifying the current one.

    Returns a refreshed principal so the current session stays valid while
    other sessions of the same user are invalidated.
    """
    user = session.get(User, actor.id)
    if user is None or not user.is_active:
        raise AuthenticationError("Your session is no longer valid. Please sign in again.")
    if not verify_password(user.password_hash, current_password or ""):
        raise AuthenticationError("Your current password is incorrect.")
    if current_password == new_password:
        raise ValidationError("Your new password must be different from the current one.")
    try:
        validators.validate_password(
            new_password, min_length=get_settings().password_min_length, username=user.username
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    now = now_utc()
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = now
    user.failed_login_attempts = 0
    user.locked_until = None
    audit_service.record(
        session,
        action=AuditAction.PASSWORD_CHANGED,
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        ip_address=ip_address,
    )
    session.flush()
    return to_auth_user(user, now)
