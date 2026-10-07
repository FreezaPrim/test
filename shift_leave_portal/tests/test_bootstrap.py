"""Automatic first-admin creation for hosted deployments."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from database.connection import session_scope
from database.schemas import friendly_errors
from database.seed import ensure_admin_from_env
from services import auth_service

ADMIN_ENV = {
    "INITIAL_ADMIN_USERNAME": "portal.admin",
    "INITIAL_ADMIN_FULL_NAME": "Portal Admin",
    "INITIAL_ADMIN_EMPLOYEE_ID": "ADM-001",
    "INITIAL_ADMIN_PASSWORD": "Very#Strong2026pw",
}


def test_admin_created_once_from_environment(monkeypatch):
    for key, value in ADMIN_ENV.items():
        monkeypatch.setenv(key, value)
    with session_scope(write=True) as s:
        assert ensure_admin_from_env(s) == "portal.admin"
    with session_scope(write=True) as s:
        assert ensure_admin_from_env(s) is None  # an admin now exists
    with session_scope(write=True) as s:
        result = auth_service.authenticate(s, "portal.admin", "Very#Strong2026pw")
    assert result.ok and result.user.is_admin and not result.user.must_change_password


def test_nothing_created_without_settings(monkeypatch):
    for key in ADMIN_ENV:
        monkeypatch.delenv(key, raising=False)
    with session_scope(write=True) as s:
        assert ensure_admin_from_env(s) is None


def test_weak_password_error_does_not_reveal_it(monkeypatch):
    for key, value in {**ADMIN_ENV, "INITIAL_ADMIN_PASSWORD": "weakpass"}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(PydanticValidationError) as exc:
        with session_scope(write=True) as s:
            ensure_admin_from_env(s)
    shown = " ".join(friendly_errors(exc.value))
    assert "weakpass" not in shown and "Password must" in shown
