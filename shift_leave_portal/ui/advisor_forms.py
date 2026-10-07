"""Advisor forms: add outage, add annual leave (also reused for editing)."""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any

import streamlit as st

from config.settings import get_settings
from database.schemas import (
    LEAVE_REASON_CATEGORIES,
    OUTAGE_CHANNELS,
    OUTAGE_REASON_CATEGORIES,
)
from services import submission_service
from services.attachment_service import ALLOWED_EXTENSIONS, UploadedFileData
from services.submission_service import SubmissionView
from ui.components import client_ip, flash, page_header, require_user, run_service
from utils.datetime_utils import combine_local, now_local, to_local, today_local

IMPACT_OPTIONS = ["low", "medium", "high"]
IMPACT_HELP = (
    "Low: a few calls/tasks affected. Medium: unable to work for part of the outage. "
    "High: completely unable to work."
)


def _index(options: tuple[str, ...] | list[str], value: str | None) -> int | None:
    return options.index(value) if value in options else None


def _form_version(name: str) -> int:
    return int(st.session_state.get(f"_form_version_{name}", 0))


def _reset_form(name: str) -> None:
    st.session_state[f"_form_version_{name}"] = _form_version(name) + 1


# --------------------------------------------------------------------------- #
# Outage
# --------------------------------------------------------------------------- #
def outage_fields(prefix: str, defaults: SubmissionView | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Render outage inputs (inside an ``st.form``) and return ``(payload, problems)``."""
    settings = get_settings()
    today = today_local()
    if defaults:
        start_local, end_local = to_local(defaults.start), to_local(defaults.end)
        assert start_local is not None and end_local is not None
        d_date, d_start, d_end = start_local.date(), start_local.time(), end_local.time()
        d_next_day = end_local.date() > start_local.date()
    else:
        now = now_local().replace(second=0, microsecond=0)
        d_date, d_start, d_end, d_next_day = today, (now - timedelta(hours=1)).time(), now.time(), False

    outage_date = st.date_input(
        "Outage date",
        value=d_date,
        min_value=min(d_date, today - timedelta(days=settings.outage_max_past_days)),
        max_value=max(d_date, today),
        format="DD/MM/YYYY",
        key=f"{prefix}_date",
    )
    c1, c2, c3 = st.columns([1, 1, 2])
    start_time = c1.time_input("Start time", value=d_start, step=300, key=f"{prefix}_start")
    end_time = c2.time_input("End time", value=d_end, step=300, key=f"{prefix}_end")
    next_day = c3.checkbox(
        "The outage ended after midnight (next day)", value=d_next_day, key=f"{prefix}_nextday",
        help="Tick this for night shifts where the outage continued past midnight.",
    )
    c4, c5 = st.columns(2)
    reason = c4.selectbox(
        "Reason category", OUTAGE_REASON_CATEGORIES,
        index=_index(OUTAGE_REASON_CATEGORIES, defaults.reason_category if defaults else None),
        placeholder="Choose what went wrong", key=f"{prefix}_reason",
    )
    channel = c5.selectbox(
        "Affected system / channel (optional)", OUTAGE_CHANNELS,
        index=_index(OUTAGE_CHANNELS, defaults.outage_channel if defaults else None),
        placeholder="Choose a system", key=f"{prefix}_channel",
    )
    impact = st.radio(
        "Impact level", IMPACT_OPTIONS,
        index=_index(IMPACT_OPTIONS, defaults.impact_level if defaults else None),
        format_func=str.capitalize, horizontal=True, help=IMPACT_HELP, key=f"{prefix}_impact",
    )
    ticket = st.text_input(
        "IT ticket reference (optional)", value=(defaults.ticket_reference or "") if defaults else "",
        max_chars=50, placeholder="e.g. INC-12345", key=f"{prefix}_ticket",
    )
    description = st.text_area(
        "What happened?", value=defaults.description if defaults else "", max_chars=2000,
        placeholder="Describe the problem, what you could not do, and how it was fixed (at least 10 characters).",
        key=f"{prefix}_desc",
    )

    problems: list[str] = []
    if reason is None:
        problems.append("Please choose a reason category.")
    if impact is None:
        problems.append("Please choose the impact level.")
    if outage_date is None or start_time is None or end_time is None:
        problems.append("Please enter the outage date, start time and end time.")
        return None, problems
    end_date = outage_date + timedelta(days=1) if next_day else outage_date
    start = combine_local(outage_date, start_time)
    end = combine_local(end_date, end_time)
    if end <= start:
        problems.append(
            "The end time must be after the start time. If the outage continued past midnight, "
            "tick 'The outage ended after midnight'."
        )
    payload = {
        "start": start,
        "end": end,
        "reason_category": reason,
        "impact_level": impact,
        "outage_channel": channel,
        "ticket_reference": ticket,
        "description": description,
    }
    return payload, problems


def render_add_outage() -> None:
    """Page: report a shift outage."""
    user = require_user()
    settings = get_settings()
    page_header("Report an outage", "Tell us about a system or connectivity problem that stopped you working during your shift.")
    version = _form_version("outage")
    with st.form(f"outage_form_{version}"):
        payload, problems = outage_fields(f"new_outage_{version}")
        uploads = []
        if settings.max_attachments_per_submission:
            uploads = st.file_uploader(
                f"Evidence (optional) — screenshots or PDF, up to {settings.max_attachments_per_submission} "
                f"files, {settings.max_upload_mb} MB each",
                type=list(ALLOWED_EXTENSIONS), accept_multiple_files=True, key=f"new_outage_files_{version}",
            ) or []
        submitted = st.form_submit_button("Submit outage", type="primary")
    if not submitted:
        return
    if problems or payload is None:
        st.error("\n".join(f"- {p}" for p in problems))
        return
    files = [UploadedFileData(filename=f.name, content=f.getvalue()) for f in uploads]
    result = run_service(
        lambda s: submission_service.create_outage(s, user, payload, files, client_ip()), write=True
    )
    if result:
        flash(f"Outage #{result.id} submitted. You can follow its status under **My submissions**.")
        _reset_form("outage")
        st.rerun()


# --------------------------------------------------------------------------- #
# Annual leave
# --------------------------------------------------------------------------- #
def leave_partial_toggle(prefix: str, defaults: SubmissionView | None = None) -> bool:
    """Toggle (outside the form) for partial-day leave."""
    return st.toggle(
        "Partial-day leave (choose specific hours)",
        value=bool(defaults and not defaults.all_day),
        key=f"{prefix}_partial",
        help="Leave this off to book whole days.",
    )


def leave_fields(
    prefix: str, partial: bool, defaults: SubmissionView | None = None
) -> tuple[dict[str, Any] | None, list[str]]:
    """Render leave inputs (inside an ``st.form``) and return ``(payload, problems)``."""
    settings = get_settings()
    today = today_local()
    if defaults:
        s_local, e_local = to_local(defaults.start), to_local(defaults.last_day)
        assert s_local is not None and e_local is not None
        d_start_date, d_end_date = s_local.date(), e_local.date()
        d_start_time = s_local.time() if not defaults.all_day else time(9, 0)
        d_end_time = e_local.time() if not defaults.all_day else time(17, 0)
    else:
        d_start_date = d_end_date = today + timedelta(days=1)
        d_start_time, d_end_time = time(9, 0), time(17, 0)

    min_date: date = min(d_start_date, today - timedelta(days=settings.leave_max_past_days))
    c1, c2 = st.columns(2)
    start_date = c1.date_input("First day of leave", value=d_start_date, min_value=min_date, format="DD/MM/YYYY", key=f"{prefix}_sdate")
    end_date = c2.date_input("Last day of leave", value=d_end_date, min_value=min_date, format="DD/MM/YYYY", key=f"{prefix}_edate")
    start_time = end_time = None
    if partial:
        c3, c4 = st.columns(2)
        start_time = c3.time_input("Leave starts at", value=d_start_time, step=900, key=f"{prefix}_stime")
        end_time = c4.time_input("Leave ends at", value=d_end_time, step=900, key=f"{prefix}_etime")
    reason = st.selectbox(
        "Reason", LEAVE_REASON_CATEGORIES,
        index=_index(LEAVE_REASON_CATEGORIES, defaults.reason_category if defaults else None),
        placeholder="Choose a reason", key=f"{prefix}_reason",
    )
    details = st.text_input(
        "Reason details", value=defaults.description if defaults else "", max_chars=1000,
        placeholder="e.g. Family trip, doctor's appointment", key=f"{prefix}_details",
    )
    notes = st.text_area(
        "Additional notes (optional)", value=(defaults.notes or "") if defaults else "", max_chars=1000,
        key=f"{prefix}_notes",
    )

    problems: list[str] = []
    if reason is None:
        problems.append("Please choose a reason.")
    if not details.strip():
        problems.append("Please add a short reason description.")
    if start_date is None or end_date is None:
        problems.append("Please choose the first and last day of your leave.")
        return None, problems
    if end_date < start_date:
        problems.append("The last day of leave cannot be before the first day.")
        return None, problems
    if partial:
        if start_time is None or end_time is None:
            problems.append("Please choose the start and end time.")
            return None, problems
        start, end = combine_local(start_date, start_time), combine_local(end_date, end_time)
        if end <= start:
            problems.append("The leave end time must be after the start time.")
    else:
        start = combine_local(start_date, time.min)
        end = combine_local(end_date + timedelta(days=1), time.min)
    payload = {
        "start": start,
        "end": end,
        "all_day": not partial,
        "reason_category": reason,
        "description": details,
        "notes": notes,
    }
    return payload, problems


def render_add_leave() -> None:
    """Page: request annual leave."""
    user = require_user()
    page_header("Request annual leave", "Book whole days, or switch on partial-day leave to choose specific hours.")
    version = _form_version("leave")
    prefix = f"new_leave_{version}"
    partial = leave_partial_toggle(prefix)
    with st.form(f"leave_form_{version}"):
        payload, problems = leave_fields(prefix, partial)
        submitted = st.form_submit_button("Submit leave request", type="primary")
    if not submitted:
        return
    if problems or payload is None:
        st.error("\n".join(f"- {p}" for p in problems))
        return
    result = run_service(lambda s: submission_service.create_leave(s, user, payload, client_ip()), write=True)
    if result:
        flash(f"Leave request #{result.id} submitted and is waiting for approval.")
        _reset_form("leave")
        st.rerun()
