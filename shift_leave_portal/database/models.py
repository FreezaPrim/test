"""SQLAlchemy ORM models.

Enumerated values are stored as short strings guarded by CHECK constraints,
which keeps the schema portable and Alembic-friendly while still letting the
database reject invalid values.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """Store aware datetimes as naive UTC and return aware UTC datetimes.

    Naive datetimes are rejected on write so that local/UTC confusion cannot
    silently corrupt data.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("UTCDateTime requires timezone-aware datetimes")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class Role(StrEnum):
    """User roles."""

    ADVISOR = "advisor"
    ADMIN = "admin"


class SubmissionType(StrEnum):
    """Kinds of submissions an advisor can create."""

    OUTAGE = "outage"
    ANNUAL_LEAVE = "annual_leave"


class SubmissionStatus(StrEnum):
    """Lifecycle states of a submission."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class ImpactLevel(StrEnum):
    """Outage impact levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Records in these states block overlapping submissions / count as active leave.
ACTIVE_STATUSES: frozenset[SubmissionStatus] = frozenset(
    {SubmissionStatus.PENDING, SubmissionStatus.APPROVED, SubmissionStatus.RESOLVED}
)


def _in_list(column: str, values: type[StrEnum]) -> str:
    quoted = ", ".join(f"'{v.value}'" for v in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    """Declarative base for all models."""

    type_annotation_map = {datetime: UTCDateTime()}


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` columns managed by the ORM."""

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class User(TimestampMixin, Base):
    """An application user (advisor or admin)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in_list("role", Role), name="ck_users_role"),
        CheckConstraint("failed_login_attempts >= 0", name="ck_users_failed_attempts"),
        CheckConstraint("length(username) >= 3", name="ck_users_username_len"),
        Index("ix_users_role_active", "role", "is_active"),
        Index("ix_users_team", "team"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    employee_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=Role.ADVISOR.value)
    team: Mapped[str | None] = mapped_column(String(60), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    allow_overlapping_outages: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    submissions: Mapped[list[Submission]] = relationship(
        back_populates="user", foreign_keys="Submission.user_id"
    )

    @property
    def is_admin(self) -> bool:
        """Whether the user has the admin role."""
        return self.role == Role.ADMIN.value

    def __repr__(self) -> str:  # never include the password hash
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


class Submission(TimestampMixin, Base):
    """An outage incident or an annual-leave request."""

    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint(_in_list("submission_type", SubmissionType), name="ck_submissions_type"),
        CheckConstraint(_in_list("status", SubmissionStatus), name="ck_submissions_status"),
        CheckConstraint(
            f"impact_level IS NULL OR {_in_list('impact_level', ImpactLevel)}",
            name="ck_submissions_impact",
        ),
        CheckConstraint("end_datetime > start_datetime", name="ck_submissions_end_after_start"),
        CheckConstraint(
            "submission_type != 'outage' OR impact_level IS NOT NULL",
            name="ck_submissions_outage_impact_required",
        ),
        CheckConstraint("length(trim(reason_category)) > 0", name="ck_submissions_reason"),
        CheckConstraint("length(trim(description)) > 0", name="ck_submissions_description"),
        Index("ix_submissions_user_type_window", "user_id", "submission_type", "start_datetime", "end_datetime"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    submission_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SubmissionStatus.PENDING.value, index=True
    )
    start_datetime: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    end_datetime: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    reason_category: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    outage_channel: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ticket_reference: Mapped[str | None] = mapped_column(String(50), nullable=True)
    manager_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    overlap_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # created_at gets its own explicit index for dashboards / time series.
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="submissions", foreign_keys=[user_id])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Submission id={self.id} type={self.submission_type} status={self.status}>"


class Attachment(Base):
    """Evidence file uploaded with a submission (stored on disk, metadata here)."""

    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_attachments_size"),
        CheckConstraint(
            "content_type IN ('image/png', 'image/jpeg', 'application/pdf')",
            name="ck_attachments_content_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, nullable=False)

    submission: Mapped[Submission] = relationship(back_populates="attachments")


class AuditLog(Base):
    """Append-only audit trail of security-relevant and business events."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    old_values: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_values: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, nullable=False, index=True
    )

    actor: Mapped[User | None] = relationship(foreign_keys=[actor_user_id])
