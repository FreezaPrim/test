"""Application configuration loaded from environment variables.

All configuration is read once from the process environment (optionally
populated from a ``.env`` file) and exposed through an immutable
:class:`Settings` object. Nothing secret is hard-coded here.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root if present. Real environment variables win.
load_dotenv(PROJECT_ROOT / ".env", override=False)


class ConfigurationError(RuntimeError):
    """Raised when the environment contains an invalid configuration value."""


def _get_int(name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _default_database_url() -> str:
    return f"sqlite:///{(PROJECT_ROOT / 'data' / 'app.db').as_posix()}"


def _normalise_sqlite_url(url: str) -> str:
    """Resolve relative SQLite file paths against the project root.

    ``sqlite:///data/app.db`` would otherwise depend on the working directory
    from which Streamlit, Alembic or pytest happens to be launched.
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix) or url.startswith("sqlite:////") or ":memory:" in url:
        return url
    raw_path = url[len(prefix):]
    if Path(raw_path).is_absolute():  # Windows drive paths such as C:/...
        return url
    return f"{prefix}{(PROJECT_ROOT / raw_path).as_posix()}"


@dataclass(frozen=True)
class Settings:
    """Immutable application settings."""

    app_name: str
    app_env: str
    database_url: str
    timezone_name: str
    session_timeout_minutes: int
    session_max_hours: int
    max_login_attempts: int
    lockout_minutes: int
    password_min_length: int
    upload_dir: Path
    max_upload_mb: int
    max_attachments_per_submission: int
    max_outage_hours: int
    outage_max_past_days: int
    max_leave_days: int
    leave_max_past_days: int
    export_row_limit: int
    log_level: str
    log_file: Path | None
    sql_echo: bool
    trust_proxy_headers: bool
    tz: ZoneInfo = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            zone = ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigurationError(
                f"APP_TIMEZONE={self.timezone_name!r} is not a valid IANA timezone "
                "(on Windows install the 'tzdata' package)."
            ) from exc
        object.__setattr__(self, "tz", zone)

    @property
    def is_development(self) -> bool:
        """Return True when running in a local development environment."""
        return self.app_env == "development"

    @property
    def max_upload_bytes(self) -> int:
        """Maximum attachment size in bytes."""
        return self.max_upload_mb * 1024 * 1024


def load_settings() -> Settings:
    """Build a :class:`Settings` instance from the current environment."""
    log_file_raw = os.getenv("LOG_FILE", "").strip()
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in logging.getLevelNamesMapping():
        raise ConfigurationError(f"LOG_LEVEL={log_level!r} is not a valid logging level")

    app_env = os.getenv("APP_ENV", "production").strip().lower()
    if app_env not in {"development", "test", "production"}:
        raise ConfigurationError("APP_ENV must be one of: development, test, production")

    return Settings(
        app_name=os.getenv("APP_NAME", "Shift Outage & Leave Portal").strip(),
        app_env=app_env,
        database_url=_normalise_sqlite_url(os.getenv("DATABASE_URL", "").strip() or _default_database_url()),
        timezone_name=os.getenv("APP_TIMEZONE", "Africa/Cairo").strip(),
        session_timeout_minutes=_get_int("SESSION_TIMEOUT_MINUTES", 30, minimum=1, maximum=24 * 60),
        session_max_hours=_get_int("SESSION_MAX_HOURS", 12, minimum=1, maximum=72),
        max_login_attempts=_get_int("MAX_LOGIN_ATTEMPTS", 5, minimum=1, maximum=100),
        lockout_minutes=_get_int("LOCKOUT_MINUTES", 15, minimum=1, maximum=24 * 60),
        password_min_length=_get_int("PASSWORD_MIN_LENGTH", 12, minimum=8, maximum=64),
        upload_dir=_resolve_path(os.getenv("UPLOAD_DIR", "data/uploads").strip() or "data/uploads"),
        max_upload_mb=_get_int("MAX_UPLOAD_MB", 5, minimum=1, maximum=50),
        max_attachments_per_submission=_get_int("MAX_ATTACHMENTS_PER_SUBMISSION", 3, minimum=0, maximum=10),
        max_outage_hours=_get_int("MAX_OUTAGE_HOURS", 24, minimum=1, maximum=24 * 7),
        outage_max_past_days=_get_int("OUTAGE_MAX_PAST_DAYS", 30, minimum=1, maximum=366),
        max_leave_days=_get_int("MAX_LEAVE_DAYS", 60, minimum=1, maximum=366),
        leave_max_past_days=_get_int("LEAVE_MAX_PAST_DAYS", 30, minimum=0, maximum=366),
        export_row_limit=_get_int("EXPORT_ROW_LIMIT", 50_000, minimum=100, maximum=1_000_000),
        log_level=log_level,
        log_file=_resolve_path(log_file_raw) if log_file_raw else None,
        sql_echo=_get_bool("SQL_ECHO", False),
        trust_proxy_headers=_get_bool("TRUST_PROXY_HEADERS", False),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (loaded lazily, cached)."""
    return load_settings()


def configure_logging(settings: Settings | None = None) -> None:
    """Configure the root logger once for the whole application."""
    settings = settings or get_settings()
    root = logging.getLogger()
    if getattr(root, "_slp_configured", False):
        return
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file is not None:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(settings.log_file, encoding="utf-8"))
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(settings.log_level)
    root._slp_configured = True  # type: ignore[attr-defined]
