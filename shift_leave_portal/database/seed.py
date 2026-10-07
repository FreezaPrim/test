"""Seed logic: first admin account and optional local demo data.

These functions are called from ``manage.py``; credentials are never
hard-coded for the real admin account.
"""

from __future__ import annotations

import logging
from datetime import time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.models import Role, Submission, SubmissionStatus, SubmissionType, User
from database.schemas import UserCreate
from services import user_service
from services.user_service import UserRow
from utils.datetime_utils import combine_local, now_utc, today_local

logger = logging.getLogger(__name__)

# Local-development-only demo accounts. seed_demo_data() refuses to run unless
# APP_ENV=development. NEVER use these in a shared or production environment.
DEMO_PASSWORD_ADVISOR = "Advisor#Demo2026"
DEMO_PASSWORD_ADMIN = "Admin#Demo2026!"
DEMO_USERS: list[dict[str, str]] = [
    {"username": "demo.admin", "full_name": "Demo Admin", "employee_id": "ADM-900", "role": "admin", "team": "Workforce"},
    {"username": "sara.ali", "full_name": "Sara Ali", "employee_id": "EMP-101", "role": "advisor", "team": "Billing"},
    {"username": "omar.hassan", "full_name": "Omar Hassan", "employee_id": "EMP-102", "role": "advisor", "team": "Billing"},
    {"username": "mona.adel", "full_name": "Mona Adel", "employee_id": "EMP-201", "role": "advisor", "team": "Technical Support"},
    {"username": "youssef.nabil", "full_name": "Youssef Nabil", "employee_id": "EMP-202", "role": "advisor", "team": "Technical Support"},
]


def admin_exists(session: Session) -> bool:
    """Return True if at least one admin account exists."""
    return session.scalar(select(User.id).where(User.role == Role.ADMIN.value).limit(1)) is not None


def create_initial_admin(
    session: Session,
    *,
    username: str,
    full_name: str,
    employee_id: str,
    password: str,
    email: str | None = None,
    team: str | None = None,
) -> UserRow:
    """Create an admin account from the command line (no acting user).

    The password must satisfy the password policy; it is hashed with Argon2id.
    """
    data = UserCreate(
        username=username,
        full_name=full_name,
        employee_id=employee_id,
        email=email,
        role=Role.ADMIN,
        team=team,
        password=password,
        must_change_password=False,
    )
    user, _ = user_service.create_user(session, None, data)
    logger.info("Created admin account %s", user.username)
    return user


def seed_demo_data(session: Session) -> list[str]:
    """Create demo users and submissions for local development only.

    Returns:
        The usernames that were created (existing ones are skipped).
    """
    if not get_settings().is_development:
        raise RuntimeError("Demo data can only be seeded when APP_ENV=development.")

    created: list[str] = []
    users: dict[str, User] = {}
    for spec in DEMO_USERS:
        existing = session.scalar(select(User).where(User.username == spec["username"]))
        if existing is None:
            password = DEMO_PASSWORD_ADMIN if spec["role"] == "admin" else DEMO_PASSWORD_ADVISOR
            user_service.create_user(
                session,
                None,
                UserCreate(
                    username=spec["username"],
                    full_name=spec["full_name"],
                    employee_id=spec["employee_id"],
                    email=f"{spec['username']}@example.com",
                    role=Role(spec["role"]),
                    team=spec["team"],
                    password=password,
                    must_change_password=False,
                ),
            )
            created.append(spec["username"])
            existing = session.scalar(select(User).where(User.username == spec["username"]))
        assert existing is not None
        users[spec["username"]] = existing

    if not created:
        return created

    admin = users["demo.admin"]
    today = today_local()
    now = now_utc()

    def outage(user: User, days_ago: int, start_h: int, minutes: int, category: str, impact: str,
               channel: str, status: SubmissionStatus, ticket: str | None) -> Submission:
        start = combine_local(today - timedelta(days=days_ago), time(start_h, 0))
        reviewed = status != SubmissionStatus.PENDING
        return Submission(
            user=user,
            submission_type=SubmissionType.OUTAGE.value,
            status=status.value,
            start_datetime=start,
            end_datetime=start + timedelta(minutes=minutes),
            reason_category=category,
            description=f"{category} during shift; could not handle calls until it was restored.",
            impact_level=impact,
            outage_channel=channel,
            ticket_reference=ticket,
            reviewed_by=admin.id if reviewed else None,
            reviewed_at=(start + timedelta(hours=5)) if reviewed else None,
            created_at=start + timedelta(minutes=minutes + 10),
        )

    def leave(user: User, offset_days: int, length: int, status: SubmissionStatus, reason: str) -> Submission:
        start_day = today + timedelta(days=offset_days)
        reviewed = status != SubmissionStatus.PENDING
        return Submission(
            user=user,
            submission_type=SubmissionType.ANNUAL_LEAVE.value,
            status=status.value,
            start_datetime=combine_local(start_day, time.min),
            end_datetime=combine_local(start_day + timedelta(days=length), time.min),
            all_day=True,
            reason_category=reason,
            description=f"{reason} leave",
            reviewed_by=admin.id if reviewed else None,
            reviewed_at=now if reviewed else None,
        )

    sara, omar, mona, youssef = (users[u] for u in ("sara.ali", "omar.hassan", "mona.adel", "youssef.nabil"))
    session.add_all(
        [
            outage(sara, 1, 10, 45, "VPN disconnection", "medium", "VPN", SubmissionStatus.PENDING, "INC-1001"),
            outage(sara, 6, 14, 90, "CRM / business application down", "high", "CRM", SubmissionStatus.RESOLVED, "INC-0990"),
            outage(omar, 2, 9, 30, "Internet / network down", "low", "Internet", SubmissionStatus.APPROVED, None),
            outage(mona, 3, 20, 120, "Power outage", "high", "Hardware", SubmissionStatus.PENDING, None),
            outage(youssef, 9, 11, 25, "Telephony / softphone issue", "medium", "Telephony", SubmissionStatus.REJECTED, "INC-0950"),
            leave(sara, 10, 3, SubmissionStatus.APPROVED, "Vacation"),
            leave(omar, 20, 5, SubmissionStatus.PENDING, "Family matter"),
            leave(mona, 2, 1, SubmissionStatus.APPROVED, "Medical appointment"),
            leave(youssef, 30, 7, SubmissionStatus.CANCELLED, "Vacation"),
        ]
    )
    session.flush()
    return created
