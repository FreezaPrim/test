"""Shared Streamlit building blocks: service calls, badges, tables, details."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import session_scope
from database.schemas import AuthUser
from services import attachment_service
from services.errors import AppError
from services.submission_service import SubmissionView
from utils.datetime_utils import (
    format_date,
    format_datetime,
    humanize_duration,
    to_local,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")

AUTH_KEY = "auth_user"
FLASH_KEY = "_flash_messages"

STATUS_STYLE: dict[str, tuple[str, str, str]] = {
    # status: (badge colour, table icon, label)
    "pending": ("orange", "🟡", "Pending"),
    "approved": ("green", "🟢", "Approved"),
    "rejected": ("red", "🔴", "Rejected"),
    "resolved": ("blue", "🔵", "Resolved"),
    "cancelled": ("gray", "⚪", "Cancelled"),
}
TYPE_LABELS = {"outage": "Outage", "annual_leave": "Annual leave"}
IMPACT_LABELS = {"low": "Low", "medium": "Medium", "high": "High"}
ACTION_LABELS = {
    "approved": "Approve",
    "rejected": "Reject",
    "resolved": "Mark resolved",
    "cancelled": "Cancel",
    "pending": "Reopen (back to pending)",
}


# --------------------------------------------------------------------------- #
# Session / request helpers
# --------------------------------------------------------------------------- #
def current_user() -> AuthUser | None:
    """Return the signed-in principal, if any."""
    user = st.session_state.get(AUTH_KEY)
    return user if isinstance(user, AuthUser) else None


def require_user() -> AuthUser:
    """Return the signed-in user or stop rendering the page."""
    user = current_user()
    if user is None:
        st.error("Please sign in to continue.")
        st.stop()
    return user


def require_admin_user() -> AuthUser:
    """Return the signed-in admin or stop rendering (defence in depth)."""
    user = require_user()
    if not user.is_admin:
        st.error("You do not have permission to view this page.")
        st.stop()
    return user


def client_ip() -> str | None:
    """Best-effort client IP for audit logs.

    ``X-Forwarded-For`` is only trusted when ``TRUST_PROXY_HEADERS=true``
    (i.e. the app runs behind a reverse proxy you control).
    """
    try:
        if get_settings().trust_proxy_headers:
            forwarded = st.context.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()[:45]
        ip = getattr(st.context, "ip_address", None)
        return str(ip)[:45] if ip else None
    except Exception:  # noqa: BLE001 - IP is optional metadata
        return None


def run_service(func: Callable[[Session], T], *, write: bool = False) -> T | None:
    """Run ``func`` inside a DB transaction and show friendly errors.

    Returns the function result, or ``None`` if an error was displayed.
    """
    try:
        with session_scope(write=write) as session:
            return func(session)
    except AppError as exc:
        if exc.details and len(exc.details) > 1:
            st.error(exc.message + "\n\n" + "\n".join(f"- {d}" for d in exc.details))
        else:
            st.error(exc.message)
    except SQLAlchemyError:
        logger.exception("Database error")
        st.error("Something went wrong while saving or loading data. Please try again in a moment.")
    return None


def flash(message: str, kind: str = "success") -> None:
    """Queue a message to show after the next rerun."""
    st.session_state.setdefault(FLASH_KEY, []).append((kind, message))


def show_flashes() -> None:
    """Display and clear queued messages."""
    for kind, message in st.session_state.pop(FLASH_KEY, []):
        getattr(st, kind, st.info)(message)


def page_header(title: str, caption: str | None = None) -> None:
    """Render a consistent page title."""
    st.title(title)
    if caption:
        st.caption(caption)
    show_flashes()


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def status_badge(status: str) -> str:
    """Markdown badge for a status value (enum-controlled, never user text)."""
    color, _, label = STATUS_STYLE.get(status, ("gray", "", status.capitalize()))
    return f":{color}-badge[{label}]"


def status_text(status: str) -> str:
    """Status with a coloured icon, for tables."""
    _, icon, label = STATUS_STYLE.get(status, ("gray", "", status.capitalize()))
    return f"{icon} {label}"


def period_text(view: SubmissionView) -> str:
    """Human-readable time window of a submission."""
    if view.all_day:
        first, last = format_date(view.start), format_date(view.last_day)
        days = view.leave_days
        span = first if first == last else f"{first} → {last}"
        return f"{span} ({days} day{'s' if days != 1 else ''})"
    start_local, end_local = to_local(view.start), to_local(view.end)
    assert start_local is not None and end_local is not None
    end_fmt = end_local.strftime("%H:%M") if start_local.date() == end_local.date() else format_datetime(view.end)
    return f"{format_datetime(view.start)} → {end_fmt} ({humanize_duration(view.duration)})"


def _naive_local(value: Any) -> Any:
    local = to_local(value)
    return local.replace(tzinfo=None) if local else None


def submissions_dataframe(rows: list[SubmissionView], *, include_owner: bool) -> pd.DataFrame:
    """Build a display DataFrame for submissions."""
    records = []
    for r in rows:
        record: dict[str, Any] = {"ID": r.id}
        if include_owner:
            record |= {"Advisor": r.full_name, "Employee ID": r.employee_id, "Team": r.team or "—"}
        record |= {
            "Type": TYPE_LABELS.get(r.submission_type, r.submission_type),
            "Status": status_text(r.status),
            # Full-day leave shows dates only (its stored end is the exclusive midnight).
            "Start": format_date(r.start) if r.all_day else format_datetime(r.start),
            "End": format_date(r.last_day) if r.all_day else format_datetime(r.end),
            "Duration": f"{r.leave_days} day(s)" if r.all_day else humanize_duration(r.duration),
            "Reason": r.reason_category,
            "Impact": IMPACT_LABELS.get(r.impact_level or "", "—"),
            "Ticket": r.ticket_reference or "—",
            "Submitted": _naive_local(r.created_at),
        }
        records.append(record)
    return pd.DataFrame.from_records(records)


TABLE_COLUMN_CONFIG = {
    "ID": st.column_config.NumberColumn("ID", format="%d", width="small"),
    "Submitted": st.column_config.DatetimeColumn("Submitted", format="D MMM YYYY, HH:mm"),
}


def selectable_table(df: pd.DataFrame, key: str, height: int | str = "auto") -> int | None:
    """Show a sortable table with single-row selection; return the selected ID."""
    if df.empty:
        st.info("No records match the current filters.")
        return None
    event = st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        height=height,
        column_config=TABLE_COLUMN_CONFIG,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    selected = event.selection.rows if event and event.selection else []
    if not selected:
        return None
    return int(df.iloc[selected[0]]["ID"])


def render_submission_details(view: SubmissionView, *, show_owner: bool) -> None:
    """Render the full details of one submission (all text is escaped by Streamlit)."""
    st.markdown(f"#### {TYPE_LABELS.get(view.submission_type, '')} #{view.id} &nbsp; {status_badge(view.status)}")
    cols = st.columns(2)
    with cols[0]:
        if show_owner:
            st.markdown(f"**Advisor:** {md_escape(view.full_name)} ({md_escape(view.employee_id)})")
            st.markdown(f"**Team:** {md_escape(view.team or '—')}")
        st.markdown(f"**When:** {period_text(view)}")
        st.markdown(f"**Reason:** {md_escape(view.reason_category)}")
        if view.submission_type == "outage":
            st.markdown(f"**Impact:** {IMPACT_LABELS.get(view.impact_level or '', '—')}")
            st.markdown(f"**Channel:** {md_escape(view.outage_channel or '—')}")
            st.markdown(f"**Ticket:** {md_escape(view.ticket_reference or '—')}")
    with cols[1]:
        st.markdown(f"**Submitted:** {format_datetime(view.created_at)}")
        st.markdown(f"**Last updated:** {format_datetime(view.updated_at)}")
        st.markdown(f"**Reviewed by:** {md_escape(view.reviewer_name or '—')}")
        st.markdown(f"**Reviewed at:** {format_datetime(view.reviewed_at)}")
        if view.overlap_override:
            st.caption("Overlap with another outage was explicitly allowed.")
    st.markdown("**Description**")
    st.text(view.description)
    if view.notes:
        st.markdown("**Additional notes**")
        st.text(view.notes)
    if view.admin_comment:
        st.markdown("**Reviewer comment**")
        st.text(view.admin_comment)
    if view.manager_comment:
        st.markdown("**Manager comment**")
        st.text(view.manager_comment)
    render_attachments(view)


def md_escape(text: str) -> str:
    """Escape Markdown control characters in user-supplied text."""
    out = []
    for ch in str(text):
        if ch in "\\`*_{}[]<>()#+-.!|:$~":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def render_attachments(view: SubmissionView) -> None:
    """List attachments with on-demand, permission-checked downloads."""
    if not view.attachments:
        return
    st.markdown("**Evidence files**")
    actor = require_user()
    for att in view.attachments:
        cols = st.columns([4, 1])
        cols[0].write(f"📎 {md_escape(att.filename)} ({att.size_bytes / 1024:.0f} KB)")
        cache_key = f"_att_{att.id}"
        if cols[1].button("Prepare download", key=f"prep_att_{view.id}_{att.id}"):
            result = run_service(
                lambda s, a=att: attachment_service.get_attachment_content(s, actor, a.id, client_ip()),
                write=True,
            )
            if result:
                st.session_state[cache_key] = result
        prepared = st.session_state.pop(cache_key, None)
        if prepared:
            filename, mime, data = prepared
            st.download_button(
                f"Download {filename}", data=data, file_name=filename, mime=mime,
                key=f"dl_att_{view.id}_{att.id}", on_click="ignore",
            )
