"""Advisor home page."""

from __future__ import annotations

import streamlit as st

from services import dashboard_service
from ui.components import (
    TYPE_LABELS,
    md_escape,
    page_header,
    period_text,
    require_user,
    run_service,
    status_badge,
)
from ui.navigation import go
from utils.datetime_utils import format_datetime


def render() -> None:
    """Page: advisor dashboard."""
    user = require_user()
    page_header(f"Welcome, {md_escape(user.full_name.split(' ')[0])}", "Your outages and leave at a glance.")
    data = run_service(lambda s: dashboard_service.advisor_dashboard(s, user))
    if data is None:
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pending submissions", data.pending_count, border=True)
    c2.metric(f"Approved leave days ({data.year})", data.approved_leave_days, border=True)
    c3.metric(f"Approved leave hours ({data.year})", f"{data.approved_leave_hours:g}", border=True,
              help="Partial-day leave, counted in hours.")
    c4.metric("Upcoming approved leave", len(data.upcoming_leave), border=True)

    b1, b2 = st.columns(2)
    if b1.button("➕ Report an outage", width="stretch"):
        go("add_outage")
    if b2.button("🌴 Request annual leave", width="stretch"):
        go("add_leave")

    left, right = st.columns(2)
    with left:
        st.subheader("Latest submissions")
        if not data.latest:
            st.info("You have not submitted anything yet.")
        for item in data.latest:
            with st.container(border=True):
                st.markdown(f"**{TYPE_LABELS[item.submission_type]} #{item.id}** &nbsp; {status_badge(item.status)}")
                st.caption(f"{period_text(item)} · submitted {format_datetime(item.created_at)}")
    with right:
        st.subheader("Upcoming approved leave")
        if not data.upcoming_leave:
            st.info("No upcoming approved leave.")
        for item in data.upcoming_leave:
            with st.container(border=True):
                st.markdown(f"🌴 **{period_text(item)}**")
                st.caption(item.reason_category)
        st.subheader("Recent outages")
        if not data.recent_outages:
            st.info("No outages reported.")
        for item in data.recent_outages:
            with st.container(border=True):
                st.markdown(f"**#{item.id} · {item.reason_category}** &nbsp; {status_badge(item.status)}")
                st.caption(period_text(item))
