"""Role-based access control and user-management safeguards."""

from __future__ import annotations

import pytest

from database.connection import session_scope
from database.schemas import SubmissionFilters
from services import audit_service, dashboard_service, export_service, user_service
from services import submission_service as svc
from services.errors import ConflictError, NotFoundError, PermissionDeniedError
from services.user_service import ConfirmationRequiredError
from tests.conftest import leave_payload, outage_payload


def _outage(actor):
    with session_scope(write=True) as s:
        return svc.create_outage(s, actor, outage_payload())


def test_advisor_cannot_view_or_modify_others_records(advisor, other_advisor):
    theirs = _outage(other_advisor)
    with session_scope() as s:
        with pytest.raises(NotFoundError):
            svc.get_submission(s, advisor, theirs.id)
    with pytest.raises(NotFoundError):
        with session_scope(write=True) as s:
            svc.update_own_submission(s, advisor, theirs.id, outage_payload())
    with pytest.raises(NotFoundError):
        with session_scope(write=True) as s:
            svc.cancel_submission(s, advisor, theirs.id)


def test_advisor_listing_is_always_restricted_to_self(advisor, other_advisor):
    mine = _outage(advisor)
    _outage(other_advisor)
    with session_scope() as s:
        rows, total = svc.list_my_submissions(
            s, advisor, SubmissionFilters(user_ids=[other_advisor.id], teams=["Support"])
        )
    assert total == 1 and [r.id for r in rows] == [mine.id]


@pytest.mark.parametrize(
    "call",
    [
        lambda s, a: svc.search_submissions(s, a, SubmissionFilters()),
        lambda s, a: export_service.export_submissions(s, a, SubmissionFilters(), "csv"),
        lambda s, a: export_service.build_export_dataframe(s, a, SubmissionFilters()),
        lambda s, a: user_service.list_users(s, a),
        lambda s, a: user_service.create_user(s, a, {"username": "x.y.z", "full_name": "X Y",
                                                     "employee_id": "E-9", "role": "admin"}),
        lambda s, a: user_service.reset_password(s, a, a.id),
        lambda s, a: user_service.set_user_active(s, a, a.id, False),
        lambda s, a: audit_service.list_audit_logs(s, a),
        lambda s, a: dashboard_service.admin_dashboard(s, a),
        lambda s, a: svc.delete_submission(s, a, 1),
        lambda s, a: svc.change_status(s, a, 1, {"new_status": "approved"}),
        lambda s, a: svc.admin_update_submission(s, a, 1, {}),
    ],
)
def test_advisor_cannot_use_admin_operations(advisor, call):
    with pytest.raises(PermissionDeniedError):
        with session_scope(write=True) as s:
            call(s, advisor)


def test_advisor_cannot_approve_own_submission(advisor):
    view = _outage(advisor)
    with pytest.raises(PermissionDeniedError):
        with session_scope(write=True) as s:
            svc.change_status(s, advisor, view.id, {"new_status": "approved"})


def test_admin_can_view_and_modify_any_record(admin, advisor):
    view = _outage(advisor)
    with session_scope() as s:
        assert svc.get_submission(s, admin, view.id).id == view.id
    with session_scope(write=True) as s:
        assert svc.cancel_submission(s, admin, view.id, "duplicate").status == "cancelled"


def test_only_advisors_create_submissions(admin):
    with pytest.raises(PermissionDeniedError):
        with session_scope(write=True) as s:
            svc.create_leave(s, admin, leave_payload())


def test_unauthenticated_actor_rejected():
    with pytest.raises(PermissionDeniedError):
        with session_scope() as s:
            svc.search_submissions(s, None, SubmissionFilters())  # type: ignore[arg-type]


def test_last_active_admin_cannot_be_deactivated_or_demoted(admin, make_user):
    second = make_user("admin", team="Workforce")
    # Admins can never deactivate themselves.
    with pytest.raises(ConflictError, match="your own"):
        with session_scope(write=True) as s:
            user_service.set_user_active(s, admin, admin.id, False)
    with session_scope(write=True) as s:
        user_service.set_user_active(s, admin, second.id, False)
    # Now `admin` is the last active admin.
    with pytest.raises(ConflictError, match="last active admin"):
        with session_scope(write=True) as s:
            user_service.update_user(
                s, admin, admin.id,
                {"full_name": admin.full_name, "employee_id": admin.employee_id, "role": "advisor"},
                confirm_self_demotion=True,
            )


def test_self_demotion_requires_confirmation(admin, make_user):
    make_user("admin", team="Workforce")
    data = {"full_name": admin.full_name, "employee_id": admin.employee_id, "role": "advisor"}
    with pytest.raises(ConfirmationRequiredError):
        with session_scope(write=True) as s:
            user_service.update_user(s, admin, admin.id, data)
    with session_scope(write=True) as s:
        assert user_service.update_user(s, admin, admin.id, data, confirm_self_demotion=True).role == "advisor"


def test_user_uniqueness_and_validation(admin, advisor):
    from services.errors import ValidationError

    with pytest.raises(ConflictError, match="username"):
        with session_scope(write=True) as s:
            user_service.create_user(s, admin, {"username": advisor.username, "full_name": "Dup User",
                                                "employee_id": "NEW-1"})
    with pytest.raises(ConflictError, match="employee ID"):
        with session_scope(write=True) as s:
            user_service.create_user(s, admin, {"username": "fresh.user", "full_name": "Dup User",
                                                "employee_id": advisor.employee_id})
    with pytest.raises(ValidationError):
        with session_scope(write=True) as s:
            user_service.create_user(s, admin, {"username": "Bad User!", "full_name": "B",
                                                "employee_id": "??", "email": "nope"})


def test_create_user_generates_temporary_password(admin):
    with session_scope(write=True) as s:
        row, temp = user_service.create_user(
            s, admin, {"username": "new.advisor", "full_name": "New Advisor", "employee_id": "EMP-777"}
        )
    assert temp and row.must_change_password
    assert not hasattr(row, "password_hash")
