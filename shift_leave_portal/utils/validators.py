"""Input sanitisation and validation helpers.

These helpers are pure functions with no Streamlit or database dependency so
they can be reused by Pydantic schemas, services and tests. They raise
``ValueError`` with messages written for non-technical users.
"""

from __future__ import annotations

import re
import unicodedata

from email_validator import EmailNotValidError, validate_email

USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30})[a-z0-9]$")
EMPLOYEE_ID_RE = re.compile(r"^[A-Z0-9](?:[A-Z0-9-]{0,18})[A-Z0-9]$")
TEAM_RE = re.compile(r"^[\w .&/()-]{1,60}$", re.UNICODE)
TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 #._/-]{0,48}[A-Za-z0-9]$|^[A-Za-z0-9]$")

# Characters allowed in free text besides printable characters.
_ALLOWED_WHITESPACE = {"\n", "\t"}

# A short list of very common passwords; extend for production use.
COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "123456789012", "qwertyuiop12",
        "welcome12345", "admin1234567", "letmein12345", "changeme1234", "p@ssw0rd1234",
        "iloveyou1234", "football1234", "monkey123456", "abc123456789", "passw0rd1234",
    }
)


def sanitize_text(value: object, *, max_length: int, multiline: bool = False) -> str:
    """Normalise user-provided text.

    * Unicode NFKC normalisation (folds look-alike characters).
    * Removes control characters (keeps newlines/tabs when ``multiline``).
    * Collapses runs of spaces on single-line fields and trims whitespace.
    * Enforces ``max_length``.

    HTML is *not* stripped here: Streamlit escapes text by default and the UI
    never renders user content with ``unsafe_allow_html``. Values are stored
    exactly as typed (minus control characters) so nothing is silently lost.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    allowed = _ALLOWED_WHITESPACE if multiline else set()
    text = "".join(
        ch for ch in text if ch in allowed or unicodedata.category(ch)[0] != "C"
    )
    if multiline:
        text = "\n".join(line.rstrip() for line in text.strip().split("\n"))
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        raise ValueError(f"must be {max_length} characters or fewer")
    return text


def optional_text(value: object, *, max_length: int, multiline: bool = False) -> str | None:
    """Sanitise optional text; empty strings become ``None``."""
    text = sanitize_text(value, max_length=max_length, multiline=multiline)
    return text or None


def required_text(value: object, *, max_length: int, min_length: int = 1, multiline: bool = False) -> str:
    """Sanitise required text and enforce a minimum length."""
    text = sanitize_text(value, max_length=max_length, multiline=multiline)
    if not text:
        raise ValueError("is required")
    if len(text) < min_length:
        raise ValueError(f"must be at least {min_length} characters")
    return text


def validate_username(value: object) -> str:
    """Validate and normalise a username (lower-case, 3-32 chars)."""
    username = sanitize_text(value, max_length=64).lower()
    if not username:
        raise ValueError("is required")
    if not USERNAME_RE.fullmatch(username):
        raise ValueError(
            "must be 3-32 characters using lowercase letters, digits, dots, dashes or "
            "underscores, and must start and end with a letter or digit"
        )
    return username


def validate_employee_id(value: object) -> str:
    """Validate and normalise an employee ID (upper-case letters, digits, dashes)."""
    employee_id = sanitize_text(value, max_length=64).upper()
    if not employee_id:
        raise ValueError("is required")
    if not EMPLOYEE_ID_RE.fullmatch(employee_id):
        raise ValueError("must be 2-20 characters using letters, digits or dashes")
    return employee_id


def validate_email_address(value: object) -> str | None:
    """Validate an optional e-mail address (syntax only, no DNS lookups)."""
    email = sanitize_text(value, max_length=254)
    if not email:
        return None
    try:
        result = validate_email(email, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError("is not a valid e-mail address") from exc
    return result.normalized.lower()


def validate_full_name(value: object) -> str:
    """Validate a person's full name."""
    name = required_text(value, max_length=120, min_length=2)
    if not any(ch.isalpha() for ch in name):
        raise ValueError("must contain letters")
    return name


def validate_team(value: object) -> str | None:
    """Validate an optional team name."""
    team = optional_text(value, max_length=60)
    if team is not None and not TEAM_RE.fullmatch(team):
        raise ValueError("may only contain letters, digits, spaces and . & / ( ) -")
    return team


def validate_ticket_reference(value: object) -> str | None:
    """Validate an optional ticket reference such as ``INC-12345``."""
    ticket = optional_text(value, max_length=50)
    if ticket is not None and not TICKET_RE.fullmatch(ticket):
        raise ValueError("may only contain letters, digits, spaces and # . _ / -")
    return ticket


def password_problems(password: str, *, min_length: int, username: str | None = None) -> list[str]:
    """Return a list of human-readable password policy violations (empty if OK)."""
    problems: list[str] = []
    if len(password) < min_length:
        problems.append(f"be at least {min_length} characters long")
    if len(password) > 128:
        problems.append("be at most 128 characters long")
    if not re.search(r"[a-z]", password):
        problems.append("contain a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("contain an uppercase letter")
    if not re.search(r"\d", password):
        problems.append("contain a digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("contain a symbol (for example ! @ # $ %)")
    if password.strip() != password:
        problems.append("not start or end with a space")
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        problems.append("not be a commonly used password")
    if username and len(username) >= 3 and username.lower() in lowered:
        problems.append("not contain your username")
    return problems


def validate_password(password: str, *, min_length: int, username: str | None = None) -> str:
    """Raise ``ValueError`` if ``password`` violates the password policy."""
    if not isinstance(password, str):
        raise ValueError("Password is required.")
    problems = password_problems(password, min_length=min_length, username=username)
    if problems:
        raise ValueError("Password must " + "; ".join(problems) + ".")
    return password
