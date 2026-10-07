"""Timezone-aware date/time helpers.

Convention used throughout the application:

* The database stores **UTC** timestamps (see ``database.models.UTCDateTime``).
* Business logic works with **timezone-aware** ``datetime`` objects.
* The UI collects and displays values in the configured application timezone
  (``APP_TIMEZONE``, default ``Africa/Cairo``).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from config.settings import get_settings

DATE_FORMAT = "%d %b %Y"
DATETIME_FORMAT = "%d %b %Y, %H:%M"
TIME_FORMAT = "%H:%M"


def app_tz() -> ZoneInfo:
    """Return the configured application timezone."""
    return get_settings().tz


def now_utc() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    """Return the current time in the application timezone."""
    return now_utc().astimezone(app_tz())


def today_local() -> date:
    """Return today's date in the application timezone."""
    return now_local().date()


def ensure_aware_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC.

    Raises:
        ValueError: if ``value`` is naive. Naive values are ambiguous and are
            rejected deliberately so that timezone bugs surface immediately.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("Naive datetime values are not allowed; attach a timezone first.")
    return value.astimezone(timezone.utc)


def local_to_utc(value: datetime) -> datetime:
    """Interpret a naive datetime as application-local time and convert to UTC."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=app_tz()).astimezone(timezone.utc)


def combine_local(day: date, at: time) -> datetime:
    """Combine a local date and time into an aware UTC datetime."""
    return local_to_utc(datetime.combine(day, at.replace(tzinfo=None)))


def to_local(value: datetime | None) -> datetime | None:
    """Convert an aware datetime to the application timezone (``None`` passes through)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(app_tz())


def format_datetime(value: datetime | None, empty: str = "—") -> str:
    """Format a datetime for display in the application timezone."""
    local = to_local(value)
    return local.strftime(DATETIME_FORMAT) if local else empty


def format_date(value: date | datetime | None, empty: str = "—") -> str:
    """Format a date (or the local date part of a datetime) for display."""
    if value is None:
        return empty
    if isinstance(value, datetime):
        value = to_local(value).date()  # type: ignore[union-attr]
    return value.strftime(DATE_FORMAT)


def local_day_bounds_utc(start_day: date, end_day: date) -> tuple[datetime, datetime]:
    """Return ``[start_day 00:00, end_day+1 00:00)`` in local time, as UTC datetimes."""
    start = combine_local(start_day, time.min)
    end = combine_local(end_day + timedelta(days=1), time.min)
    return start, end


def month_bounds_utc(reference: date | None = None) -> tuple[datetime, datetime]:
    """Return the UTC bounds of the local calendar month containing ``reference``."""
    reference = reference or today_local()
    first = reference.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    return combine_local(first, time.min), combine_local(next_first, time.min)


def overlap_duration(
    start: datetime, end: datetime, window_start: datetime, window_end: datetime
) -> timedelta:
    """Return the length of the overlap between two half-open intervals."""
    latest_start = max(start, window_start)
    earliest_end = min(end, window_end)
    return max(earliest_end - latest_start, timedelta(0))


def humanize_duration(delta: timedelta) -> str:
    """Render a duration such as ``2d 3h 15m`` for humans."""
    total_minutes = int(delta.total_seconds() // 60)
    days, rem = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)
