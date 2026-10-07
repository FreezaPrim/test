"""Domain exceptions raised by the service layer.

Every message is safe to show to end users: no SQL, stack traces or internal
identifiers. The UI catches :class:`AppError` and displays ``str(error)``.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for expected, user-facing errors."""

    def __init__(self, message: str, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []

    def __str__(self) -> str:
        return self.message


class ValidationError(AppError):
    """Input failed validation. ``details`` lists each problem."""


class PermissionDeniedError(AppError):
    """The actor is not allowed to perform the operation."""

    def __init__(self, message: str = "You do not have permission to do that.") -> None:
        super().__init__(message)


class NotFoundError(AppError):
    """The requested record does not exist (or is not visible to the actor)."""

    def __init__(self, message: str = "The requested record was not found.") -> None:
        super().__init__(message)


class ConflictError(AppError):
    """The operation conflicts with existing data (duplicates, overlaps, state)."""


class AuthenticationError(AppError):
    """Authentication failed. The message never reveals which part was wrong."""
