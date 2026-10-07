"""Dashboard aggregations."""

from __future__ import annotations

from database.connection import session_scope
from services import dashboard_service
from services import submission_service as svc
from tests.conftest import leave_payload, local, outage_payload


def test_advisor_dashboard_counts_only_own_data(admin, advisor, other_advisor):
    with session_scope(write=True) as s:
        svc.create_outage(s, advisor, outage_payload(start=local(-1, 9)))
        leave = svc.create_leave(s, advisor, leave_payload(first_day_offset=3, days=2))
        partial = svc.create_leave(s, advisor, leave_payload(all_day=False, start=local(10, 9), end=local(10, 13)))
        svc.create_outage(s, other_advisor, outage_payload())
    with session_scope(write=True) as s:
        svc.change_status(s, admin, leave.id, {"new_status": "approved"})
        svc.change_status(s, admin, partial.id, {"new_status": "approved"})
    with session_scope() as s:
        data = dashboard_service.advisor_dashboard(s, advisor)
    assert data.pending_count == 1
    assert len(data.recent_outages) == 1 and len(data.latest) == 3
    assert [u.id for u in data.upcoming_leave] == [leave.id, partial.id]
    # Days/hours are counted within the current calendar year only.
    assert data.approved_leave_days in (0, 1, 2) and data.approved_leave_hours in (0, 4)


def test_admin_dashboard(admin, advisor, other_advisor):
    with session_scope(write=True) as s:
        o = svc.create_outage(s, advisor, outage_payload(impact_level="high"))
        svc.create_outage(s, other_advisor, outage_payload(reason_category="Power outage"))
        svc.create_leave(s, advisor, leave_payload())
    with session_scope(write=True) as s:
        svc.change_status(s, admin, o.id, {"new_status": "resolved"})
    with session_scope() as s:
        data = dashboard_service.admin_dashboard(s, admin, period_days=30)
    assert data.active_advisors == 2
    assert data.pending_outages == 1 and data.pending_leave == 1
    assert int(data.outages_by_category["Outages"].sum()) == 2
    assert data.outages_by_impact.loc["High", "Outages"] == 1
    assert set(data.submissions_by_team.index) == {"Billing", "Support"}
    assert len(data.submissions_over_time) == 30
    assert int(data.submissions_over_time.to_numpy().sum()) == 3
    assert data.avg_review_hours is not None and data.avg_review_hours >= 0


def test_admin_dashboard_empty(admin):
    with session_scope() as s:
        data = dashboard_service.admin_dashboard(s, admin)
    assert data.active_advisors == 0 and data.submissions_over_time.empty
