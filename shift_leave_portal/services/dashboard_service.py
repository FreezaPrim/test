"""Aggregations for the advisor and admin dashboards."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.models import Role, Submission, SubmissionStatus, SubmissionType, User
from database.schemas import AuthUser, SubmissionFilters
from services.submission_service import SubmissionView, list_my_submissions
from utils.datetime_utils import (
    combine_local,
    month_bounds_utc,
    now_utc,
    overlap_duration,
    to_local,
    today_local,
)
from utils.permissions import require_admin


@dataclass
class AdvisorDashboard:
    """Figures shown on an advisor's home page."""

    pending_count: int
    approved_leave_days: int
    approved_leave_hours: float
    year: int
    recent_outages: list[SubmissionView] = field(default_factory=list)
    upcoming_leave: list[SubmissionView] = field(default_factory=list)
    latest: list[SubmissionView] = field(default_factory=list)


@dataclass
class AdminDashboard:
    """Figures and chart data shown on the admin dashboard."""

    active_advisors: int
    pending_outages: int
    pending_leave: int
    approved_leave_month_count: int
    approved_leave_month_days: int
    avg_review_hours: float | None
    period_days: int
    outages_by_category: pd.DataFrame
    outages_by_impact: pd.DataFrame
    submissions_by_team: pd.DataFrame
    submissions_over_time: pd.DataFrame


def _full_days_within(view_start: datetime, view_end: datetime, window: tuple[date, date]) -> int:
    """Count local calendar days of an all-day leave that fall inside ``[window)``."""
    start_local, end_local = to_local(view_start), to_local(view_end)
    assert start_local is not None and end_local is not None
    first = max(start_local.date(), window[0])
    last_exclusive = min(end_local.date(), window[1])
    return max((last_exclusive - first).days, 0)


def advisor_dashboard(session: Session, actor: AuthUser) -> AdvisorDashboard:
    """Build the dashboard for the acting user (own data only)."""
    now = now_utc()
    year = today_local().year
    year_window = (date(year, 1, 1), date(year + 1, 1, 1))
    year_start = combine_local(year_window[0], time.min)
    year_end = combine_local(year_window[1], time.min)

    rows, _ = list_my_submissions(session, actor, SubmissionFilters(), sort="created_desc", limit=None)
    pending = sum(1 for r in rows if r.status == SubmissionStatus.PENDING.value)
    approved_leave = [
        r for r in rows
        if r.submission_type == SubmissionType.ANNUAL_LEAVE.value and r.status == SubmissionStatus.APPROVED.value
    ]
    days = sum(_full_days_within(r.start, r.end, year_window) for r in approved_leave if r.all_day)
    hours = sum(
        overlap_duration(r.start, r.end, year_start, year_end).total_seconds() / 3600
        for r in approved_leave
        if not r.all_day
    )
    upcoming = sorted((r for r in approved_leave if r.end > now), key=lambda r: r.start)[:5]
    outages = [r for r in rows if r.submission_type == SubmissionType.OUTAGE.value][:5]
    return AdvisorDashboard(
        pending_count=pending,
        approved_leave_days=days,
        approved_leave_hours=round(hours, 1),
        year=year,
        recent_outages=outages,
        upcoming_leave=upcoming,
        latest=rows[:5],
    )


def admin_dashboard(session: Session, actor: AuthUser, period_days: int = 30) -> AdminDashboard:
    """Build organisation-wide statistics. Admin only."""
    require_admin(actor)
    period_days = max(1, min(period_days, 366))
    now = now_utc()
    since = now - timedelta(days=period_days)

    active_advisors = session.scalar(
        select(func.count(User.id)).where(User.role == Role.ADVISOR.value, User.is_active.is_(True))
    ) or 0
    pending_counts = dict(
        session.execute(
            select(Submission.submission_type, func.count(Submission.id))
            .where(Submission.status == SubmissionStatus.PENDING.value)
            .group_by(Submission.submission_type)
        ).all()
    )

    month_start, month_end = month_bounds_utc()
    local_month_start = to_local(month_start)
    local_month_end = to_local(month_end)
    assert local_month_start is not None and local_month_end is not None
    month_leave = session.execute(
        select(Submission.start_datetime, Submission.end_datetime, Submission.all_day).where(
            Submission.submission_type == SubmissionType.ANNUAL_LEAVE.value,
            Submission.status == SubmissionStatus.APPROVED.value,
            Submission.start_datetime < month_end,
            Submission.end_datetime > month_start,
        )
    ).all()
    month_window = (local_month_start.date(), local_month_end.date())
    month_days = sum(_full_days_within(s, e, month_window) for s, e, all_day in month_leave if all_day)

    records = session.execute(
        select(
            Submission.submission_type,
            Submission.status,
            Submission.reason_category,
            Submission.impact_level,
            Submission.created_at,
            Submission.reviewed_at,
            User.team,
        )
        .join(User, Submission.user_id == User.id)
        .where(Submission.created_at >= since)
    ).all()
    df = pd.DataFrame(
        records,
        columns=["type", "status", "category", "impact", "created_at", "reviewed_at", "team"],
    )

    if df.empty:
        empty = pd.DataFrame()
        return AdminDashboard(
            active_advisors=active_advisors,
            pending_outages=pending_counts.get(SubmissionType.OUTAGE.value, 0),
            pending_leave=pending_counts.get(SubmissionType.ANNUAL_LEAVE.value, 0),
            approved_leave_month_count=len(month_leave),
            approved_leave_month_days=month_days,
            avg_review_hours=None,
            period_days=period_days,
            outages_by_category=empty,
            outages_by_impact=empty,
            submissions_by_team=empty,
            submissions_over_time=empty,
        )

    df["team"] = df["team"].fillna("No team")
    df["type"] = df["type"].map({"outage": "Outage", "annual_leave": "Annual leave"})
    outages = df[df["type"] == "Outage"]
    by_category = (
        outages.groupby("category").size().rename("Outages").sort_values(ascending=False).to_frame()
    )
    impact_order = ["low", "medium", "high"]
    by_impact = (
        outages["impact"].value_counts().reindex(impact_order, fill_value=0).rename("Outages").to_frame()
    )
    by_impact.index = [i.capitalize() for i in by_impact.index]
    by_team = df.pivot_table(index="team", columns="type", values="status", aggfunc="count", fill_value=0)

    created_local = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert(get_settings().timezone_name)
    df["day"] = created_local.dt.date
    over_time = df.pivot_table(index="day", columns="type", values="status", aggfunc="count", fill_value=0)
    full_index = pd.date_range(end=today_local(), periods=period_days, freq="D").date
    over_time = over_time.reindex(full_index, fill_value=0)
    over_time.index = pd.to_datetime(over_time.index)
    over_time.index.name = "Date"

    reviewed = df.dropna(subset=["reviewed_at"])
    avg_hours: float | None = None
    if not reviewed.empty:
        delta = pd.to_datetime(reviewed["reviewed_at"], utc=True) - pd.to_datetime(reviewed["created_at"], utc=True)
        avg_hours = round(float(delta.dt.total_seconds().mean()) / 3600, 1)

    return AdminDashboard(
        active_advisors=active_advisors,
        pending_outages=pending_counts.get(SubmissionType.OUTAGE.value, 0),
        pending_leave=pending_counts.get(SubmissionType.ANNUAL_LEAVE.value, 0),
        approved_leave_month_count=len(month_leave),
        approved_leave_month_days=month_days,
        avg_review_hours=avg_hours,
        period_days=period_days,
        outages_by_category=by_category,
        outages_by_impact=by_impact,
        submissions_by_team=by_team,
        submissions_over_time=over_time,
    )
