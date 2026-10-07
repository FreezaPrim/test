"""Authentication, password policy, lockout and session re-validation."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from database.connection import session_scope
from database.models import AuditLog, User
from services import auth_service, user_service
from services.audit_service import AuditAction
from services.errors import AuthenticationError, ValidationError
from utils import validators
from utils.datetime_utils import now_utc
from utils.security import (
    generate_temporary_password,
    hash_password,
    needs_rehash,
    verify_password,
)

PASSWORD = "Correct#Horse42"


def _login(username: str, password: str):
    with session_scope(write=True) as s:
        return auth_service.authenticate(s, username, password, "10.0.0.1")


def _audit_actions() -> list[str]:
    with session_scope() as s:
        return [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]


def test_password_hashing_roundtrip():
    hashed = hash_password(PASSWORD)
    assert hashed != PASSWORD and hashed.startswith("$argon2id$")
    assert verify_password(hashed, PASSWORD)
    assert not verify_password(hashed, "wrong-password")
    assert not verify_password(None, PASSWORD)
    assert not verify_password("not-a-hash", PASSWORD)
    assert not needs_rehash(hashed)


def test_generated_password_meets_policy():
    for _ in range(20):
        validators.validate_password(generate_temporary_password(), min_length=12)


@pytest.mark.parametrize(
    "password",
    ["short1!A", "alllowercase#123", "ALLUPPERCASE#123", "NoDigitsHere!!", "NoSymbols12345", " Leading#Space1"],
)
def test_weak_passwords_rejected(password):
    with pytest.raises(ValueError):
        validators.validate_password(password, min_length=12)


def test_password_must_not_contain_username():
    with pytest.raises(ValueError, match="username"):
        validators.validate_password("Sara.Ali#2026xx", min_length=12, username="sara.ali")


def test_login_success_updates_last_login_and_audits(advisor):
    result = _login(advisor.username, PASSWORD)
    assert result.ok and result.user.id == advisor.id
    assert result.user.role == "advisor"
    with session_scope() as s:
        assert s.get(User, advisor.id).last_login_at is not None
    assert AuditAction.LOGIN_SUCCESS in _audit_actions()


def test_login_is_case_insensitive_for_username(advisor):
    assert _login(advisor.username.upper(), PASSWORD).ok


def test_wrong_password_and_unknown_user_give_identical_errors(advisor):
    bad = _login(advisor.username, "Wrong#Password1")
    unknown = _login("no.such.user", PASSWORD)
    malformed = _login("x", PASSWORD)
    assert not bad.ok and not unknown.ok and not malformed.ok
    assert bad.error == unknown.error == malformed.error == auth_service.GENERIC_LOGIN_ERROR
    assert _audit_actions().count(AuditAction.LOGIN_FAILURE) == 3


def test_account_locks_after_max_attempts(advisor):
    for _ in range(3):  # MAX_LOGIN_ATTEMPTS=3 in tests
        assert not _login(advisor.username, "Wrong#Password1").ok
    # Correct password is refused while locked.
    assert not _login(advisor.username, PASSWORD).ok
    with session_scope(write=True) as s:
        user = s.get(User, advisor.id)
        assert user.locked_until is not None
        user.locked_until = now_utc() - timedelta(seconds=1)  # lock expires
    assert _login(advisor.username, PASSWORD).ok


def test_inactive_user_cannot_login(admin, advisor):
    with session_scope(write=True) as s:
        user_service.set_user_active(s, admin, advisor.id, False)
    result = _login(advisor.username, PASSWORD)
    assert not result.ok and result.error == auth_service.GENERIC_LOGIN_ERROR


def test_audit_log_never_contains_passwords(advisor):
    _login(advisor.username, "Secret#Typo99")
    _login("Secret#Typo99", PASSWORD)  # password typed into the username box
    with session_scope() as s:
        dump = json.dumps([(a.old_values, a.new_values) for a in s.scalars(select(AuditLog))])
        hashes = [u.password_hash for u in s.scalars(select(User))]
    assert "Secret#Typo99" not in dump and PASSWORD not in dump
    assert not any(h in dump for h in hashes)
    assert "<invalid format>" in dump


def test_change_password_flow(advisor):
    with session_scope(write=True) as s:
        with pytest.raises(AuthenticationError):
            auth_service.change_password(s, advisor, "Wrong#Password1", "Another#Pass2026")
    with session_scope(write=True) as s:
        with pytest.raises(ValidationError):
            auth_service.change_password(s, advisor, PASSWORD, "weak")
    with session_scope(write=True) as s:
        refreshed = auth_service.change_password(s, advisor, PASSWORD, "Another#Pass2026")
    assert _login(advisor.username, "Another#Pass2026").ok
    assert not _login(advisor.username, PASSWORD).ok
    with session_scope() as s:
        # The session that changed the password stays valid...
        assert auth_service.refresh_session_user(s, refreshed) is not None
        # ...but older sessions (signed in before the change) are invalidated.
        stale = advisor.__class__(**{**advisor.__dict__, "authenticated_at": refreshed.authenticated_at - timedelta(minutes=1)})
        assert auth_service.refresh_session_user(s, stale) is None


def test_admin_password_reset_forces_change_and_ends_sessions(admin, advisor):
    with session_scope(write=True) as s:
        temp = user_service.reset_password(s, admin, advisor.id)
    assert temp and validators.password_problems(temp, min_length=12) == []
    with session_scope() as s:
        assert auth_service.refresh_session_user(s, advisor) is None
    result = _login(advisor.username, temp)
    assert result.ok and result.user.must_change_password
    assert AuditAction.PASSWORD_RESET in _audit_actions()


def test_refresh_reflects_role_changes_and_deactivation(admin, advisor):
    with session_scope(write=True) as s:
        user_service.update_user(
            s, admin, advisor.id,
            {"full_name": "New Name", "employee_id": advisor.employee_id, "role": "admin", "team": "Ops"},
        )
    with session_scope() as s:
        refreshed = auth_service.refresh_session_user(s, advisor)
    assert refreshed.role == "admin" and refreshed.full_name == "New Name" and refreshed.team == "Ops"
    with session_scope(write=True) as s:
        user_service.set_user_active(s, admin, advisor.id, False)
    with session_scope() as s:
        assert auth_service.refresh_session_user(s, advisor) is None


def test_logout_is_audited(advisor):
    with session_scope(write=True) as s:
        auth_service.logout(s, advisor)
        auth_service.logout(s, advisor, expired=True)
    actions = _audit_actions()
    assert AuditAction.LOGOUT in actions and AuditAction.SESSION_EXPIRED in actions
