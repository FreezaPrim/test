"""Admin page: search, review and export all submissions."""

from __future__ import annotations

import math
from datetime import time, timedelta

import streamlit as st

from database.models import SubmissionStatus, SubmissionType
from database.schemas import (
    LEAVE_REASON_CATEGORIES,
    OUTAGE_CHANNELS,
    OUTAGE_REASON_CATEGORIES,
    AuthUser,
    SubmissionFilters,
)
from services import export_service, submission_service, user_service
from services.submission_service import SubmissionView
from ui.components import (
    ACTION_LABELS,
    IMPACT_LABELS,
    STATUS_STYLE,
    TYPE_LABELS,
    client_ip,
    flash,
    md_escape,
    page_header,
    render_submission_details,
    require_admin_user,
    run_service,
    selectable_table,
    submissions_dataframe,
)
from utils.datetime_utils import combine_local, to_local

FILTERS_KEY = "admin_sub_filters"
PAGE_KEY = "admin_sub_page"
EXPORT_KEY = "_admin_export_file"
SORT_LABELS = {
    "start_desc": "Start date (newest first)",
    "start_asc": "Start date (oldest first)",
    "created_desc": "Submitted (newest first)",
    "created_asc": "Submitted (oldest first)",
}


def _filters_panel(user: AuthUser) -> SubmissionFilters:
    """Render the filter form and return the *applied* filters."""
    choices = run_service(
        lambda s: (user_service.list_advisor_choices(s, user), user_service.list_teams(s, user))
    ) or ([], [])
    advisors, teams = choices
    advisor_labels = dict(advisors)
    current: SubmissionFilters = st.session_state.get(FILTERS_KEY) or SubmissionFilters()

    with st.form("admin_filters"):
        c1, c2, c3 = st.columns(3)
        dates = c1.date_input(
            "Date range", value=tuple(d for d in (current.date_from, current.date_to) if d), format="DD/MM/YYYY",
            help="Shows records that overlap this period.",
        )
        user_ids = c2.multiselect(
            "Advisor", list(advisor_labels), default=[u for u in current.user_ids if u in advisor_labels],
            format_func=lambda i: advisor_labels.get(i, str(i)),
        )
        team_sel = c3.multiselect("Team", teams, default=[t for t in current.teams if t in teams])
        c4, c5, c6 = st.columns(3)
        types = c4.multiselect("Type", list(TYPE_LABELS), default=current.submission_types, format_func=TYPE_LABELS.get)
        statuses = c5.multiselect(
            "Status", list(STATUS_STYLE), default=current.statuses, format_func=lambda s: STATUS_STYLE[s][2]
        )
        impacts = c6.multiselect(
            "Impact level", list(IMPACT_LABELS), default=current.impact_levels, format_func=IMPACT_LABELS.get
        )
        search = st.text_input(
            "Search", value=current.search or "", max_chars=100,
            placeholder="Employee ID, username, name, ticket reference or description",
        )
        b1, b2, _ = st.columns([1, 1, 4])
        applied = b1.form_submit_button("Apply filters", type="primary", width="stretch")
        cleared = b2.form_submit_button("Clear", width="stretch")

    if cleared:
        current = SubmissionFilters()
        st.session_state[FILTERS_KEY] = current
        st.session_state[PAGE_KEY] = 1
        st.session_state.pop(EXPORT_KEY, None)
        st.rerun()
    if applied:
        date_from = dates[0] if len(dates) >= 1 else None
        date_to = dates[1] if len(dates) == 2 else date_from
        current = SubmissionFilters(
            date_from=date_from, date_to=date_to, user_ids=user_ids, teams=team_sel,
            submission_types=types, statuses=statuses, impact_levels=impacts, search=search or None,
        )
        st.session_state[FILTERS_KEY] = current
        st.session_state[PAGE_KEY] = 1
        st.session_state.pop(EXPORT_KEY, None)
    return current


def _export_panel(user: AuthUser, filters: SubmissionFilters) -> None:
    with st.expander("⬇️ Export the filtered records"):
        st.caption("The export contains exactly the records matching the filters above (all pages).")
        c1, c2 = st.columns([1, 3])
        fmt = c1.radio("Format", ["xlsx", "csv"], format_func=lambda f: "Excel (.xlsx)" if f == "xlsx" else "CSV",
                       horizontal=True)
        if c2.button("Prepare export file"):
            file = run_service(
                lambda s: export_service.export_submissions(s, user, filters, fmt, client_ip()), write=True
            )
            if file:
                st.session_state[EXPORT_KEY] = file
        file = st.session_state.get(EXPORT_KEY)
        if file:
            st.success(f"Export ready: {file.row_count} row(s).")
            st.download_button(
                f"Download {file.filename}", data=file.data, file_name=file.filename, mime=file.mime,
                type="primary", on_click="ignore",
            )


@st.dialog("Confirm cancellation")
def _confirm_cancel(user: AuthUser, view: SubmissionView, comment: str | None) -> None:
    st.write(f"Cancel **{TYPE_LABELS[view.submission_type].lower()} #{view.id}** for {md_escape(view.full_name)}?")
    c1, c2 = st.columns(2)
    if c1.button("Yes, cancel it", type="primary", width="stretch"):
        result = run_service(
            lambda s: submission_service.change_status(
                s, user, view.id, {"new_status": "cancelled", "admin_comment": comment}, client_ip()
            ),
            write=True,
        )
        if result:
            flash(f"Submission #{view.id} cancelled.")
            st.rerun()
    if c2.button("Go back", width="stretch"):
        st.rerun()


@st.dialog("Delete submission permanently")
def _confirm_delete(user: AuthUser, view: SubmissionView) -> None:
    st.warning(
        f"This permanently deletes **#{view.id}** and its evidence files. "
        "The deletion is recorded in the audit log. Prefer **Cancel** or **Reject** in most cases."
    )
    confirm = st.text_input(f"Type {view.id} to confirm")
    if st.button("Delete permanently", type="primary", disabled=confirm.strip() != str(view.id)):
        result = run_service(
            lambda s: submission_service.delete_submission(s, user, view.id, client_ip()) or True, write=True
        )
        if result:
            flash(f"Submission #{view.id} deleted.", "warning")
            st.rerun()


def _review_actions(user: AuthUser, view: SubmissionView) -> None:
    st.markdown("##### Review")
    targets = submission_service.allowed_transitions(view.submission_type, view.status)
    comment = st.text_area(
        "Comment for the advisor (optional)", value=view.admin_comment or "", max_chars=1000,
        key=f"review_comment_{view.id}",
    )
    allow_overlap = False
    if view.submission_type == SubmissionType.OUTAGE.value and SubmissionStatus.PENDING in targets:
        allow_overlap = st.checkbox(
            "Allow this outage to overlap another active outage when reopening",
            key=f"review_overlap_{view.id}",
        )
    cols = st.columns(max(len(targets), 1) + 1)
    for idx, target in enumerate(targets):
        label = ACTION_LABELS[target.value]
        kind = "primary" if target in {SubmissionStatus.APPROVED, SubmissionStatus.RESOLVED} else "secondary"
        if cols[idx].button(label, key=f"act_{view.id}_{target.value}", type=kind, width="stretch"):
            if target == SubmissionStatus.CANCELLED:
                _confirm_cancel(user, view, comment)
            else:
                result = run_service(
                    lambda s, t=target: submission_service.change_status(
                        s, user, view.id,
                        {"new_status": t, "admin_comment": comment, "allow_overlap": allow_overlap},
                        client_ip(),
                    ),
                    write=True,
                )
                if result:
                    flash(f"Submission #{view.id} is now {result.status}.")
                    st.rerun()
    if cols[-1].button("🗑️ Delete", key=f"del_{view.id}", width="stretch"):
        _confirm_delete(user, view)


def _admin_edit(user: AuthUser, view: SubmissionView) -> None:
    is_outage = view.submission_type == SubmissionType.OUTAGE.value
    start_local, end_local = to_local(view.start), to_local(view.end)
    assert start_local is not None and end_local is not None
    with st.form(f"admin_edit_{view.id}"):
        c1, c2, c3, c4 = st.columns(4)
        s_date = c1.date_input("Start date", value=start_local.date(), format="DD/MM/YYYY")
        s_time = c2.time_input("Start time", value=start_local.time(), step=300)
        last = to_local(view.last_day) if view.all_day else end_local
        assert last is not None
        e_date = c3.date_input("End date" + (" (last day)" if view.all_day else ""), value=last.date(), format="DD/MM/YYYY")
        e_time = c4.time_input("End time", value=end_local.time(), step=300)
        all_day = False
        if not is_outage:
            all_day = st.checkbox("Full-day leave (times are ignored)", value=view.all_day)
        reasons = OUTAGE_REASON_CATEGORIES if is_outage else LEAVE_REASON_CATEGORIES
        reason = st.selectbox(
            "Reason", reasons, index=reasons.index(view.reason_category) if view.reason_category in reasons else 0
        )
        description = st.text_area("Description", value=view.description, max_chars=2000)
        update: dict[str, object] = {}
        if is_outage:
            c5, c6, c7 = st.columns(3)
            impact = c5.selectbox(
                "Impact", list(IMPACT_LABELS), index=list(IMPACT_LABELS).index(view.impact_level or "low"),
                format_func=IMPACT_LABELS.get,
            )
            channel = c6.selectbox(
                "Channel", ["", *OUTAGE_CHANNELS],
                index=(["", *OUTAGE_CHANNELS]).index(view.outage_channel or ""),
                format_func=lambda c: c or "—",
            )
            ticket = c7.text_input("Ticket reference", value=view.ticket_reference or "", max_chars=50)
            update |= {"impact_level": impact, "outage_channel": channel, "ticket_reference": ticket}
        else:
            notes = st.text_area("Additional notes", value=view.notes or "", max_chars=1000)
            update |= {"notes": notes, "all_day": all_day}
        manager_comment = st.text_area("Manager comment", value=view.manager_comment or "", max_chars=1000)
        allow_overlap = is_outage and st.checkbox("Allow overlap with the advisor's other outages")
        saved = st.form_submit_button("Save changes", type="primary")
    if not saved:
        return
    if is_outage or not all_day:
        start, end = combine_local(s_date, s_time), combine_local(e_date, e_time)
    else:
        start, end = combine_local(s_date, time.min), combine_local(e_date + timedelta(days=1), time.min)
    update |= {
        "start": start, "end": end, "reason_category": reason, "description": description,
        "manager_comment": manager_comment, "allow_overlap": bool(allow_overlap),
    }
    result = run_service(
        lambda s: submission_service.admin_update_submission(s, user, view.id, update, client_ip()), write=True
    )
    if result:
        flash(f"Submission #{view.id} updated.")
        st.rerun()


def render() -> None:
    """Page: all submissions (admin)."""
    user = require_admin_user()
    page_header("All submissions", "Filter, review and export advisor outages and leave requests.")
    filters = _filters_panel(user)

    c1, c2, _ = st.columns([1, 1, 2])
    sort = c1.selectbox("Sort by", list(SORT_LABELS), format_func=SORT_LABELS.get, key="admin_sort")
    page_size = c2.selectbox("Rows per page", [25, 50, 100, 200], index=1, key="admin_page_size")
    page = int(st.session_state.get(PAGE_KEY, 1))
    result = run_service(
        lambda s: submission_service.search_submissions(
            s, user, filters, sort=sort, limit=page_size, offset=(page - 1) * page_size
        )
    )
    if result is None:
        return
    rows, total = result
    pages = max(1, math.ceil(total / page_size))
    if page > pages:
        st.session_state[PAGE_KEY] = pages
        st.rerun()

    _export_panel(user, filters)

    nav1, nav2, nav3 = st.columns([1, 2, 1])
    if nav1.button("◀ Previous", disabled=page <= 1, width="stretch"):
        st.session_state[PAGE_KEY] = page - 1
        st.rerun()
    nav2.markdown(f"Page **{page}** of **{pages}** · {total} record(s)", text_alignment="center")
    if nav3.button("Next ▶", disabled=page >= pages, width="stretch"):
        st.session_state[PAGE_KEY] = page + 1
        st.rerun()

    selected_id = selectable_table(
        submissions_dataframe(rows, include_owner=True), key=f"admin_table_{page}", height=420
    )
    if selected_id is None:
        if rows:
            st.caption("👆 Click a row to review it.")
        return
    view = next((r for r in rows if r.id == selected_id), None)
    if view is None:
        return
    with st.container(border=True):
        render_submission_details(view, show_owner=True)
        st.divider()
        _review_actions(user, view)
        with st.expander("✏️ Edit record details"):
            _admin_edit(user, view)
