"""Admin dashboard: organisation-wide KPIs and charts."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from services import dashboard_service
from ui.components import page_header, require_admin_user, run_service

# Colour follows the entity, never its rank: each submission type keeps the
# same colour in every chart. Single-series charts use one hue.
SERIES_COLORS = {"Outage": "#2a78d6", "Annual leave": "#eb6834"}
SINGLE_SERIES_COLOR = "#2a78d6"
PERIODS = {7: "Last 7 days", 30: "Last 30 days", 90: "Last 90 days", 365: "Last 12 months"}


def _two_series(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Ensure both type columns exist in a fixed order and return matching colours."""
    columns = list(SERIES_COLORS)
    df = df.reindex(columns=columns, fill_value=0)
    return df, [SERIES_COLORS[c] for c in columns]


def render() -> None:
    """Page: admin dashboard."""
    user = require_admin_user()
    page_header("Admin dashboard", "Overview of outages and leave across all teams.")
    period = st.segmented_control(
        "Period for charts", list(PERIODS), format_func=PERIODS.get, default=30, key="dash_period"
    ) or 30
    data = run_service(lambda s: dashboard_service.admin_dashboard(s, user, period_days=period))
    if data is None:
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Active advisors", data.active_advisors, border=True)
    c2.metric("Pending outages", data.pending_outages, border=True)
    c3.metric("Pending leave requests", data.pending_leave, border=True)
    c4.metric(
        "Leave approved (month)", data.approved_leave_month_count, border=True,
        help=f"Approved leave requests touching the current month; {data.approved_leave_month_days} full leave day(s) fall in it.",
    )
    c5.metric(
        "Avg. review time",
        f"{data.avg_review_hours:g} h" if data.avg_review_hours is not None else "—",
        border=True,
        help=f"From submission to decision, for records submitted in the selected period ({PERIODS[period].lower()}).",
    )

    if data.submissions_over_time.empty:
        st.info("No submissions in the selected period yet.")
        return

    st.subheader("Submissions over time")
    over_time, colors = _two_series(data.submissions_over_time)
    st.line_chart(over_time, color=colors, x_label="Date", y_label="Submissions")

    left, right = st.columns(2)
    with left:
        st.subheader("Outages by category")
        if data.outages_by_category.empty:
            st.info("No outages in this period.")
        else:
            st.bar_chart(
                data.outages_by_category, horizontal=True, color=SINGLE_SERIES_COLOR,
                x_label="Outages", y_label="", sort="-Outages",
            )
    with right:
        st.subheader("Outages by impact level")
        if data.outages_by_impact["Outages"].sum() == 0:
            st.info("No outages in this period.")
        else:
            st.bar_chart(
                data.outages_by_impact, color=SINGLE_SERIES_COLOR, x_label="Impact level",
                y_label="Outages", sort=False,
            )

    st.subheader("Submissions by team")
    by_team, colors = _two_series(data.submissions_by_team)
    st.bar_chart(by_team, color=colors, horizontal=True, x_label="Submissions", y_label="", stack=True)

    with st.expander("Show the numbers as tables"):
        t1, t2, t3 = st.columns(3)
        t1.dataframe(data.outages_by_category, width="stretch")
        t2.dataframe(data.outages_by_impact, width="stretch")
        t3.dataframe(by_team, width="stretch")
