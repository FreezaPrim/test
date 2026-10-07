"""Audit logging.

Audit rows are written in the *same transaction* as the change they describe,
so a change is never committed without its audit record (and vice versa).
Secrets are stripped from logged values defensively.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import AuditLog, User
from database.schemas import AuthUser
from utils.permissions import require_admin

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = frozenset(
    {"password", "password_hash", "new_password", "current_password", "token", "secret"}
)


class AuditAction:
    """Well-known audit action names."""

    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    SESSION_EXPIRED = "session_expired"
    PASSWORD_CHANGED = "password_changed"
    SUBMISSION_CREATED = "submission_created"
    SUBMISSION_UPDATED = "submission_updated"
    SUBMISSION_CANCELLED = "submission_cancelled"
    SUBMISSION_STATUS_CHANGED = "submission_status_changed"
    SUBMISSION_DELETED = "submission_deleted"
    ATTACHMENT_DOWNLOADED = "attachment_downloaded"
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_ACTIVATED = "user_activated"
    USER_DEACTIVATED = "user_deactivated"
    USER_UNLOCKED = "user_unlocked"
    PASSWORD_RESET = "password_reset"
    DATA_EXPORTED = "data_exported"


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return scrub(value)
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def scrub(values: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a JSON-safe copy of ``values`` with sensitive keys removed."""
    if values is None:
        return None
    return {
        str(key): _jsonable(val)
        for key, val in values.items()
        if str(key).lower() not in SENSITIVE_KEYS
    }


def record(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: int | str | None = None,
    actor_id: int | None = None,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Add an audit row to ``session`` (committed with the surrounding transaction)."""
    entry = AuditLog(
        actor_user_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        old_values=scrub(old_values),
        new_values=scrub(new_values),
        ip_address=(ip_address or None) and ip_address[:45],
    )
    session.add(entry)
    logger.info(
        "audit action=%s entity=%s:%s actor=%s", action, entity_type, entity_id, actor_id
    )
    return entry


def diff(old: dict[str, Any], new: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return only the keys whose values changed, as ``(old, new)`` dicts."""
    changed = [key for key in new if old.get(key) != new.get(key)]
    return {k: old.get(k) for k in changed}, {k: new.get(k) for k in changed}


@dataclass(frozen=True)
class AuditRow:
    """Read model for the audit log viewer."""

    id: int
    created_at: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str | None
    old_values: dict[str, Any] | None
    new_values: dict[str, Any] | None
    ip_address: str | None


def list_audit_logs(
    session: Session,
    actor: AuthUser,
    *,
    actions: list[str] | None = None,
    entity_type: str | None = None,
    since: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[AuditRow], int]:
    """Return a page of audit entries (newest first) and the total count. Admin only."""
    require_admin(actor)
    stmt = select(AuditLog, User.username).outerjoin(User, AuditLog.actor_user_id == User.id)
    count_stmt = select(func.count(AuditLog.id))
    conditions = []
    if actions:
        conditions.append(AuditLog.action.in_(actions))
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    if since is not None:
        conditions.append(AuditLog.created_at >= since)
    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    total = session.scalar(count_stmt) or 0
    rows = session.execute(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
    ).all()
    return (
        [
            AuditRow(
                id=log.id,
                created_at=log.created_at,
                actor=username or "—",
                action=log.action,
                entity_type=log.entity_type,
                entity_id=log.entity_id,
                old_values=log.old_values,
                new_values=log.new_values,
                ip_address=log.ip_address,
            )
            for log, username in rows
        ],
        total,
    )
