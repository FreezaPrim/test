"""Pydantic input schemas and lightweight DTOs.

Schemas validate *structure and content* of user input. Rules that depend on
who is acting or on existing data (permissions, overlaps, time windows) live in
the service layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from config.settings import get_settings
from database.models import ImpactLevel, Role, SubmissionStatus, SubmissionType
from utils import validators as v
from utils.datetime_utils import ensure_aware_utc

OUTAGE_REASON_CATEGORIES: tuple[str, ...] = (
    "Internet / network down",
    "VPN disconnection",
    "CRM / business application down",
    "Telephony / softphone issue",
    "Hardware failure",
    "Power outage",
    "Login / access problem",
    "Other",
)
OUTAGE_CHANNELS: tuple[str, ...] = (
    "VPN", "CRM", "Internet", "Hardware", "Application", "Telephony", "Other",
)
LEAVE_REASON_CATEGORIES: tuple[str, ...] = (
    "Vacation",
    "Family matter",
    "Personal",
    "Medical appointment",
    "Religious / public holiday",
    "Study / exam",
    "Other",
)

FIELD_LABELS: dict[str, str] = {
    "username": "Username",
    "full_name": "Full name",
    "employee_id": "Employee ID",
    "email": "E-mail",
    "role": "Role",
    "team": "Team",
    "password": "Password",
    "start": "Start",
    "end": "End",
    "reason_category": "Reason",
    "description": "Description",
    "notes": "Additional notes",
    "impact_level": "Impact level",
    "outage_channel": "Outage channel",
    "ticket_reference": "Ticket reference",
    "admin_comment": "Admin comment",
    "manager_comment": "Manager comment",
}


def friendly_errors(exc: ValidationError) -> list[str]:
    """Turn a Pydantic ``ValidationError`` into plain-language messages."""
    messages: list[str] = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part != "__root__"]
        label = FIELD_LABELS.get(loc[0], loc[0].replace("_", " ").capitalize()) if loc else ""
        msg = str(err.get("msg", "is invalid"))
        for prefix in ("Value error, ", "Assertion failed, "):
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
        if err.get("type") == "missing":
            msg = "is required"
        messages.append(f"{label}: {msg[:1].upper()}{msg[1:]}" if label else msg)
    return messages


@dataclass(frozen=True)
class AuthUser:
    """The authenticated principal kept in the Streamlit session.

    Contains no secrets; it is re-validated against the database on every run.
    """

    id: int
    username: str
    full_name: str
    employee_id: str
    role: str
    team: str | None
    must_change_password: bool
    authenticated_at: datetime

    @property
    def is_admin(self) -> bool:
        """Whether this principal has the admin role."""
        return self.role == Role.ADMIN.value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class _WindowModel(_StrictModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        try:
            return ensure_aware_utc(value)
        except ValueError as exc:
            raise ValueError("must include a timezone") from exc


class OutageInput(_WindowModel):
    """Data required to create or edit an outage submission."""

    reason_category: str
    impact_level: ImpactLevel
    outage_channel: str | None = None
    ticket_reference: str | None = None
    description: str

    @field_validator("reason_category")
    @classmethod
    def _reason(cls, value: str) -> str:
        value = v.required_text(value, max_length=60)
        if value not in OUTAGE_REASON_CATEGORIES:
            raise ValueError("must be one of the listed options")
        return value

    @field_validator("outage_channel")
    @classmethod
    def _channel(cls, value: str | None) -> str | None:
        value = v.optional_text(value, max_length=30)
        if value is not None and value not in OUTAGE_CHANNELS:
            raise ValueError("must be one of the listed options")
        return value

    @field_validator("ticket_reference")
    @classmethod
    def _ticket(cls, value: str | None) -> str | None:
        return v.validate_ticket_reference(value)

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return v.required_text(value, max_length=2000, min_length=10, multiline=True)

    @model_validator(mode="after")
    def _window(self) -> OutageInput:
        if self.end <= self.start:
            raise ValueError("The outage end time must be after the start time.")
        max_hours = get_settings().max_outage_hours
        if self.end - self.start > timedelta(hours=max_hours):
            raise ValueError(
                f"An outage cannot last longer than {max_hours} hours. "
                "Please split it into separate submissions."
            )
        return self


class LeaveInput(_WindowModel):
    """Data required to create or edit an annual-leave request.

    For full-day leave ``end`` is the *exclusive* midnight after the last day.
    """

    all_day: bool = True
    reason_category: str
    description: str
    notes: str | None = None

    @field_validator("reason_category")
    @classmethod
    def _reason(cls, value: str) -> str:
        value = v.required_text(value, max_length=60)
        if value not in LEAVE_REASON_CATEGORIES:
            raise ValueError("must be one of the listed options")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return v.required_text(value, max_length=1000, min_length=3, multiline=True)

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str | None) -> str | None:
        return v.optional_text(value, max_length=1000, multiline=True)

    @model_validator(mode="after")
    def _window(self) -> LeaveInput:
        if self.end <= self.start:
            raise ValueError("The leave end must be after the leave start.")
        max_days = get_settings().max_leave_days
        if self.end - self.start > timedelta(days=max_days):
            raise ValueError(f"A single leave request cannot be longer than {max_days} days.")
        return self


class AdminSubmissionUpdate(_StrictModel):
    """Fields an admin may change on any submission (``None`` = unchanged)."""

    start: datetime | None = None
    end: datetime | None = None
    all_day: bool | None = None
    reason_category: str | None = None
    description: str | None = None
    notes: str | None = None
    impact_level: ImpactLevel | None = None
    outage_channel: str | None = None
    ticket_reference: str | None = None
    manager_comment: str | None = None
    admin_comment: str | None = None
    allow_overlap: bool = False

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return ensure_aware_utc(value) if value is not None else None

    @field_validator("manager_comment", "admin_comment")
    @classmethod
    def _comments(cls, value: str | None) -> str | None:
        # "" is kept (not None) so an admin can clear an existing comment.
        return v.sanitize_text(value, max_length=1000, multiline=True) if value is not None else None


class StatusChange(_StrictModel):
    """An admin decision on a submission."""

    new_status: SubmissionStatus
    admin_comment: str | None = None
    allow_overlap: bool = False

    @field_validator("admin_comment")
    @classmethod
    def _comment(cls, value: str | None) -> str | None:
        return v.optional_text(value, max_length=1000, multiline=True)


class _UserProfileModel(_StrictModel):
    """Shared validators for user profile fields."""

    @field_validator("full_name", check_fields=False)
    @classmethod
    def _full_name(cls, value: str) -> str:
        return v.validate_full_name(value)

    @field_validator("employee_id", check_fields=False)
    @classmethod
    def _employee_id(cls, value: str) -> str:
        return v.validate_employee_id(value)

    @field_validator("email", check_fields=False)
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return v.validate_email_address(value)

    @field_validator("team", check_fields=False)
    @classmethod
    def _team(cls, value: str | None) -> str | None:
        return v.validate_team(value)


class UserCreate(_UserProfileModel):
    """Data required to create a user. ``password=None`` generates one."""

    username: str
    full_name: str
    employee_id: str
    email: str | None = None
    role: Role = Role.ADVISOR
    team: str | None = None
    is_active: bool = True
    password: str | None = Field(default=None, repr=False)
    must_change_password: bool = True
    allow_overlapping_outages: bool = False

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        return v.validate_username(value)

    @model_validator(mode="after")
    def _password(self) -> UserCreate:
        if self.password:
            v.validate_password(
                self.password, min_length=get_settings().password_min_length, username=self.username
            )
        return self


class UserUpdate(_UserProfileModel):
    """Editable user profile fields (username is immutable)."""

    full_name: str
    employee_id: str
    email: str | None = None
    role: Role
    team: str | None = None
    allow_overlapping_outages: bool = False


@dataclass
class SubmissionFilters:
    """Filters for listing/exporting submissions. Dates are local calendar days."""

    date_from: date | None = None
    date_to: date | None = None
    user_ids: list[int] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)
    submission_types: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    impact_levels: list[str] = field(default_factory=list)
    search: str | None = None

    def as_audit_dict(self) -> dict[str, Any]:
        """Serialisable representation used in audit logs."""
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "user_ids": list(self.user_ids),
            "teams": list(self.teams),
            "submission_types": list(self.submission_types),
            "statuses": list(self.statuses),
            "impact_levels": list(self.impact_levels),
            "search": self.search,
        }


__all__ = [
    "AdminSubmissionUpdate",
    "AuthUser",
    "LEAVE_REASON_CATEGORIES",
    "LeaveInput",
    "OUTAGE_CHANNELS",
    "OUTAGE_REASON_CATEGORIES",
    "OutageInput",
    "StatusChange",
    "SubmissionFilters",
    "SubmissionType",
    "UserCreate",
    "UserUpdate",
    "friendly_errors",
]
