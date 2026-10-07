"""Password hashing and other security helpers (Argon2id via argon2-cffi)."""

from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults follow RFC 9106 "low memory" recommendations (Argon2id).
_hasher = PasswordHasher()

# Pre-computed hash used to equalise timing when a username does not exist,
# so response time does not reveal whether an account exists.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    """Return an Argon2id hash of ``password``."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Verify ``password`` against ``password_hash`` in constant-ish time.

    When ``password_hash`` is ``None`` a dummy verification is still performed
    so callers can use this for unknown users without leaking timing info.
    """
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return True if the hash was produced with outdated parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_temporary_password(length: int = 16) -> str:
    """Generate a random password that satisfies the password policy."""
    length = max(length, 12)
    symbols = "!@#$%^&*-_=+?"
    alphabet = string.ascii_letters + string.digits + symbols
    while True:
        candidate = [
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.digits),
            secrets.choice(symbols),
        ]
        candidate += [secrets.choice(alphabet) for _ in range(length - len(candidate))]
        secrets.SystemRandom().shuffle(candidate)
        password = "".join(candidate)
        if password[0] != " " and password[-1] != " ":
            return password


def neutralize_spreadsheet_formula(value: object) -> object:
    """Prevent CSV/Excel formula injection for user-controlled text cells.

    Spreadsheet applications execute cells beginning with ``= + - @`` (and tab
    / carriage return). Such values are prefixed with a single quote.
    """
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
