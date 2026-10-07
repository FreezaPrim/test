"""Role-based access control rules.

These checks are enforced inside the service layer (not just by hiding UI
elements), so a crafted request can never bypass them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from database.models import Role, SubmissionStatus
from services.errors import NotFoundError, PermissionDeniedError

if TYPE_CHECKING:
    from database.models import Submission
    from database.schemas import AuthUser


def is_admin(actor: AuthUser | None) -> bool:
    """Return True if ``actor`` is an authenticated admin."""
    return actor is not None and actor.role == Role.ADMIN.value


def require_authenticated(actor: AuthUser | None) -> AuthUser:
    """Ensure there is an authenticated actor."""
    if actor is None:
        raise PermissionDeniedError("Please sign in to continue.")
    return actor


def require_admin(actor: AuthUser | None) -> AuthUser:
    """Ensure ``actor`` is an admin."""
    actor = require_authenticated(actor)
    if not is_admin(actor):
        raise PermissionDeniedError()
    return actor


def require_advisor(actor: AuthUser | None) -> AuthUser:
    """Ensure ``actor`` is an advisor (only advisors create submissions)."""
    actor = require_authenticated(actor)
    if actor.role != Role.ADVISOR.value:
        raise PermissionDeniedError("Only advisors can create submissions.")
    return actor


def can_view_submission(actor: AuthUser | None, submission: Submission) -> bool:
    """Admins can view everything; advisors only their own records."""
    if actor is None:
        return False
    return is_admin(actor) or submission.user_id == actor.id


def can_modify_submission(actor: AuthUser | None, submission: Submission) -> bool:
    """Admins can modify any record; advisors only their own *pending* records."""
    if actor is None:
        return False
    if is_admin(actor):
        return True
    return submission.user_id == actor.id and submission.status == SubmissionStatus.PENDING.value


def require_view_submission(actor: AuthUser | None, submission: Submission) -> None:
    """Raise if ``actor`` may not view ``submission``.

    A generic "not found" is raised to avoid confirming that someone else's
    record exists.
    """
    if not can_view_submission(actor, submission):
        raise NotFoundError()


def require_modify_submission(actor: AuthUser | None, submission: Submission) -> None:
    """Raise if ``actor`` may not modify ``submission``."""
    require_view_submission(actor, submission)
    if not can_modify_submission(actor, submission):
        raise PermissionDeniedError("Only pending submissions can be changed.")
