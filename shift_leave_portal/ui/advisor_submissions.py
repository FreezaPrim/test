"""Advisor page: list, search, view, edit and cancel own submissions."""

from __future__ import annotations

import streamlit as st

from database.models import SubmissionStatus, SubmissionType
from database.schemas import AuthUser, SubmissionFilters
from services import submission_service
from services.submission_service import SubmissionView
from ui.advisor_forms import leave_fields, leave_partial_toggle, outage_fields
from ui.components import (
    STATUS_STYLE,
    TYPE_LABELS,
    client_ip,
    flash,
    page_header,
    render_submission_details,
    require_user,
    run_service,
    selectable_table,
    submissions_dataframe,
)

SORT_LABELS = {
    "start_desc": "Start date (newest first)",
    "start_asc": "Start date (oldest first)",
    "created_desc": "Submitted (newest first)",
    "created_asc": "Submitted (oldest first)",
}


@st.dialog("Cancel submission")
def _confirm_cancel(user: AuthUser, view: SubmissionView) -> None:
    st.write(
        f"Are you sure you want to cancel **{TYPE_LABELS[view.submission_type].lower()} #{view.id}**? "
        "This cannot be undone — you would need to submit it again."
    )
    reason = st.text_input("Reason for cancelling (optional)", max_chars=500)
    c1, c2 = st.columns(2)
    if c1.button("Yes, cancel it", type="primary", width="stretch"):
        result = run_service(
            lambda s: submission_service.cancel_submission(s, user, view.id, reason, client_ip()), write=True
        )
        if result:
            flash(f"Submission #{view.id} was cancelled.")
            st.rerun()
    if c2.button("Keep it", width="stretch"):
        st.rerun()


def _edit_form(user: AuthUser, view: SubmissionView) -> None:
    prefix = f"edit_{view.id}"
    if view.submission_type == SubmissionType.OUTAGE.value:
        with st.form(f"{prefix}_form"):
            payload, problems = outage_fields(prefix, view)
            submitted = st.form_submit_button("Save changes", type="primary")
    else:
        partial = leave_partial_toggle(prefix, view)
        with st.form(f"{prefix}_form"):
            payload, problems = leave_fields(prefix, partial, view)
            submitted = st.form_submit_button("Save changes", type="primary")
    if not submitted:
        return
    if problems or payload is None:
        st.error("\n".join(f"- {p}" for p in problems))
        return
    result = run_service(
        lambda s: submission_service.update_own_submission(s, user, view.id, payload, client_ip()), write=True
    )
    if result:
        flash(f"Submission #{view.id} was updated.")
        st.rerun()


def render() -> None:
    """Page: the advisor's own submission history."""
    user = require_user()
    page_header("My submissions", "Select a row to see details. Pending items can still be edited or cancelled.")

    with st.container(border=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        search = c1.text_input("Search", placeholder="Ticket number or words in the description", max_chars=100)
        types = c2.multiselect("Type", list(TYPE_LABELS), format_func=TYPE_LABELS.get)
        statuses = c3.multiselect("Status", list(STATUS_STYLE), format_func=lambda s: STATUS_STYLE[s][2])
        c4, c5 = st.columns([2, 2])
        dates = c4.date_input("Between dates", value=(), format="DD/MM/YYYY")
        sort = c5.selectbox("Sort by", list(SORT_LABELS), format_func=SORT_LABELS.get)

    date_from = dates[0] if len(dates) >= 1 else None
    date_to = dates[1] if len(dates) == 2 else date_from
    filters = SubmissionFilters(
        date_from=date_from, date_to=date_to, submission_types=types, statuses=statuses, search=search or None
    )
    result = run_service(lambda s: submission_service.list_my_submissions(s, user, filters, sort=sort))
    if result is None:
        return
    rows, total = result
    st.caption(f"{total} submission(s)")
    selected_id = selectable_table(submissions_dataframe(rows, include_owner=False), key="my_submissions_table")
    if selected_id is None:
        if rows:
            st.caption("👆 Click a row to view details.")
        return

    view = next((r for r in rows if r.id == selected_id), None)
    if view is None:
        return
    with st.container(border=True):
        render_submission_details(view, show_owner=False)
        if view.status == SubmissionStatus.PENDING.value:
            st.divider()
            with st.expander("✏️ Edit this submission"):
                _edit_form(user, view)
            if st.button("Cancel this submission", key=f"cancel_{view.id}"):
                _confirm_cancel(user, view)
        else:
            st.caption("This submission has been processed and can no longer be changed.")
