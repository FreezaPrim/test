"""Shared fixtures: an isolated SQLite database and users per test."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from config import settings as settings_module


@pytest.fixture(autouse=True)
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the app at a fresh temp database and upload folder for every test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("APP_TIMEZONE", "Africa/Cairo")
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "12")
    monkeypatch.setenv("MAX_LOGIN_ATTEMPTS", "3")
    monkeypatch.setenv("LOCKOUT_MINUTES", "15")
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    settings_module.get_settings.cache_clear()

    from database.connection import get_engine, init_engine
    from database.models import Base

    init_engine()
    Base.metadata.create_all(get_engine())
    yield tmp_path
    get_engine().dispose()
    settings_module.get_settings.cache_clear()


@pytest.fixture
def make_user() -> Callable[..., object]:
    """Factory creating a user and returning its AuthUser principal."""
    from database.connection import session_scope
    from database.models import User
    from database.schemas import UserCreate
    from services import auth_service, user_service
    from utils.datetime_utils import now_utc

    counter = {"n": 0}

    def _make(role: str = "advisor", team: str | None = "Billing", password: str = "Correct#Horse42",
              allow_overlap: bool = False, username: str | None = None):
        counter["n"] += 1
        n = counter["n"]
        with session_scope(write=True) as s:
            row, _ = user_service.create_user(
                s,
                None,
                UserCreate(
                    username=username or f"{role}.user{n}",
                    full_name=f"{role.capitalize()} User {n}",
                    employee_id=f"EMP-{n:03d}",
                    email=f"{role}{n}@example.com",
                    role=role,
                    team=team,
                    password=password,
                    must_change_password=False,
                    allow_overlapping_outages=allow_overlap,
                ),
            )
            user = s.get(User, row.id)
            return auth_service.to_auth_user(user, now_utc())

    return _make


@pytest.fixture
def admin(make_user):
    """An admin principal."""
    return make_user("admin", team="Workforce")


@pytest.fixture
def advisor(make_user):
    """An advisor principal (team Billing)."""
    return make_user("advisor", team="Billing")


@pytest.fixture
def other_advisor(make_user):
    """A second advisor principal (team Support)."""
    return make_user("advisor", team="Support")


def local(day_offset: int, hour: int, minute: int = 0) -> datetime:
    """Aware UTC datetime for ``today + day_offset`` at ``hour:minute`` local time."""
    from utils.datetime_utils import combine_local, today_local

    return combine_local(today_local() + timedelta(days=day_offset), time(hour, minute))


def outage_payload(start: datetime | None = None, minutes: int = 60, **overrides):
    """Valid outage input dict (defaults to yesterday 10:00 local)."""
    start = start or local(-1, 10)
    data = {
        "start": start,
        "end": start + timedelta(minutes=minutes),
        "reason_category": "VPN disconnection",
        "impact_level": "medium",
        "outage_channel": "VPN",
        "ticket_reference": "INC-12345",
        "description": "VPN dropped repeatedly and I could not take calls.",
    }
    data.update(overrides)
    return data


def leave_payload(first_day_offset: int = 5, days: int = 2, **overrides):
    """Valid full-day leave input dict."""
    start = local(first_day_offset, 0)
    data = {
        "start": start,
        "end": local(first_day_offset + days, 0),
        "all_day": True,
        "reason_category": "Vacation",
        "description": "Family trip",
        "notes": None,
    }
    data.update(overrides)
    return data
