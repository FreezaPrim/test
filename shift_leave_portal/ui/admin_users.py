"""Admin page: user management."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from config.settings import get_settings
from database.models import Role
from database.schemas import AuthUser
from services import user_service
from services.user_service import UserRow
from ui.components import (
    client_ip,
    flash,
    md_escape,
    page_header,
    require_admin_user,
    run_service,
)
from utils.datetime_utils import format_datetime

ROLE_LABELS = {"advisor": "Advisor", "admin": "Admin"}
TEMP_PASSWORD_KEY = "_temp_password_notice"


def _show_temp_password() -> None:
    """Show a one-time temporary password notice, then forget it."""
    notice = st.session_state.pop(TEMP_PASSWORD_KEY, None)
    if notice:
        username, password = notice
        st.warning(
            f"Temporary password for **{md_escape(username)}** — copy it now and share it privately (it will not be shown "
            "again). The user must change it at first sign-in."
        )
        st.code(password, language=None)


def _users_table(users: list[UserRow]) -> int | None:
    df = pd.DataFrame(
        [
            {
                "ID": u.id,
                "Name": u.full_name,
                "Username": u.username,
                "Employee ID": u.employee_id,
                "Role": ROLE_LABELS.get(u.role, u.role),
                "Team": u.team or "—",
                "Status": ("🟢 Active" if u.is_active else "⚪ Inactive") + (" 🔒" if u.is_locked else ""),
                "Last sign-in": format_datetime(u.last_login_at, empty="Never"),
            }
            for u in users
        ]
    )
    if df.empty:
        st.info("No users match your search.")
        return None
    event = st.dataframe(
        df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="users_table",
        column_config={"ID": st.column_config.NumberColumn(format="%d", width="small")},
    )
    rows = event.selection.rows if event and event.selection else []
    return int(df.iloc[rows[0]]["ID"]) if rows else None


def _create_user_form(actor: AuthUser, teams: list[str]) -> None:
    min_len = get_settings().password_min_length
    mode = st.radio(
        "Password", ["generate", "set"], horizontal=True, key="new_user_pw_mode",
        format_func=lambda m: "Generate a temporary password" if m == "generate" else "Set a password now",
    )
    with st.form("create_user", clear_on_submit=False):
        c1, c2 = st.columns(2)
        full_name = c1.text_input("Full name", max_chars=120)
        username = c2.text_input("Username", max_chars=32, help="Lowercase letters, digits, dots, dashes or underscores.")
        c3, c4 = st.columns(2)
        employee_id = c3.text_input("Employee ID", max_chars=20)
        email = c4.text_input("E-mail (optional)", max_chars=254)
        c5, c6 = st.columns(2)
        role = c5.selectbox("Role", list(ROLE_LABELS), format_func=ROLE_LABELS.get)
        team = c6.text_input("Team (optional)", max_chars=60,
                             help="Existing teams: " + (", ".join(teams) if teams else "none yet"))
        password = None
        if mode == "set":
            password = st.text_input(f"Password (min. {min_len} characters, mixed case, digit and symbol)",
                                     type="password", autocomplete="new-password")
        must_change = st.checkbox("Require a password change at first sign-in", value=True,
                                  disabled=mode == "generate")
        overlap = st.checkbox("Allow this advisor to submit overlapping outages", value=False)
        submitted = st.form_submit_button("Create user", type="primary")
    if not submitted:
        return
    data = {
        "username": username, "full_name": full_name, "employee_id": employee_id, "email": email or None,
        "role": role, "team": team or None, "password": password or None,
        "must_change_password": True if mode == "generate" else must_change,
        "allow_overlapping_outages": overlap,
    }
    result = run_service(lambda s: user_service.create_user(s, actor, data, client_ip()), write=True)
    if result:
        user, temp = result
        if temp:
            st.session_state[TEMP_PASSWORD_KEY] = (user.username, temp)
        flash(f"User **{md_escape(user.username)}** created.")
        st.rerun()


def _edit_user_form(actor: AuthUser, user: UserRow) -> None:
    is_self = user.id == actor.id
    with st.form(f"edit_user_{user.id}"):
        c1, c2 = st.columns(2)
        full_name = c1.text_input("Full name", value=user.full_name, max_chars=120)
        employee_id = c2.text_input("Employee ID", value=user.employee_id, max_chars=20)
        c3, c4 = st.columns(2)
        email = c3.text_input("E-mail", value=user.email or "", max_chars=254)
        team = c4.text_input("Team", value=user.team or "", max_chars=60)
        role = st.selectbox("Role", list(ROLE_LABELS), index=list(ROLE_LABELS).index(user.role),
                            format_func=ROLE_LABELS.get)
        overlap = st.checkbox("Allow overlapping outage submissions", value=user.allow_overlapping_outages)
        confirm_self = False
        if is_self and user.role == Role.ADMIN.value:
            confirm_self = st.checkbox(
                "I understand that changing my own role to Advisor removes my admin access immediately."
            )
        saved = st.form_submit_button("Save changes", type="primary")
    if not saved:
        return
    data = {"full_name": full_name, "employee_id": employee_id, "email": email or None, "role": role,
            "team": team or None, "allow_overlapping_outages": overlap}
    # A ConfirmationRequiredError (self-demotion without the checkbox) is shown by run_service.
    result = run_service(
        lambda s: user_service.update_user(
            s, actor, user.id, data, confirm_self_demotion=confirm_self, ip_address=client_ip()
        ),
        write=True,
    )
    if result:
        flash(f"User **{md_escape(result.username)}** updated.")
        st.rerun()


@st.dialog("Deactivate user")
def _confirm_deactivate(actor: AuthUser, user: UserRow) -> None:
    st.write(f"Deactivate **{md_escape(user.full_name)}** ({md_escape(user.username)})? They will be signed out and unable to sign in. "
             "Their records are kept and you can reactivate them later.")
    c1, c2 = st.columns(2)
    if c1.button("Deactivate", type="primary", width="stretch"):
        if run_service(lambda s: user_service.set_user_active(s, actor, user.id, False, client_ip()), write=True):
            flash(f"User **{md_escape(user.username)}** deactivated.", "warning")
            st.rerun()
    if c2.button("Cancel", width="stretch"):
        st.rerun()


@st.dialog("Reset password")
def _reset_password_dialog(actor: AuthUser, user: UserRow) -> None:
    st.write(f"Reset the password for **{md_escape(user.full_name)}** ({md_escape(user.username)}). Their current sessions will end.")
    mode = st.radio("New password", ["generate", "set"], horizontal=True,
                    format_func=lambda m: "Generate temporary password" if m == "generate" else "Set it myself")
    new_password = None
    if mode == "set":
        new_password = st.text_input("New password", type="password", autocomplete="new-password")
    force = st.checkbox("Require a password change at next sign-in", value=True, disabled=mode == "generate")
    if st.button("Reset password", type="primary"):
        result = run_service(
            lambda s: user_service.reset_password(
                s, actor, user.id, new_password=new_password or None, force_change=force, ip_address=client_ip()
            ) or "",
            write=True,
        )
        if result is not None:
            if result:
                st.session_state[TEMP_PASSWORD_KEY] = (user.username, result)
            flash(f"Password for **{md_escape(user.username)}** was reset.")
            st.rerun()


def _user_actions(actor: AuthUser, user: UserRow) -> None:
    st.markdown(f"#### {md_escape(user.full_name)}")
    st.caption(
        f"Created {format_datetime(user.created_at)} · Last sign-in {format_datetime(user.last_login_at, empty='never')}"
        + (" · must change password" if user.must_change_password else "")
    )
    c1, c2, c3 = st.columns(3)
    if user.is_active:
        if c1.button("Deactivate", width="stretch", disabled=user.id == actor.id,
                     help="You cannot deactivate your own account." if user.id == actor.id else None):
            _confirm_deactivate(actor, user)
    elif c1.button("Reactivate", width="stretch", type="primary"):
        if run_service(lambda s: user_service.set_user_active(s, actor, user.id, True, client_ip()), write=True):
            flash(f"User **{md_escape(user.username)}** reactivated.")
            st.rerun()
    if c2.button("Reset password", width="stretch"):
        _reset_password_dialog(actor, user)
    if c3.button("Unlock sign-in", width="stretch", disabled=not user.is_locked,
                 help="Clears a temporary lock after too many failed sign-ins."):
        if run_service(lambda s: user_service.unlock_user(s, actor, user.id, client_ip()), write=True):
            flash(f"User **{md_escape(user.username)}** unlocked.")
            st.rerun()
    with st.expander("✏️ Edit details", expanded=False):
        _edit_user_form(actor, user)


def render() -> None:
    """Page: user management (admin only)."""
    actor = require_admin_user()
    page_header("User management", "Create accounts, assign roles and teams, and manage access.")
    _show_temp_password()

    tab_list, tab_new = st.tabs(["Users", "➕ New user"])
    teams = run_service(lambda s: user_service.list_teams(s, actor)) or []
    with tab_new:
        _create_user_form(actor, teams)
    with tab_list:
        c1, c2, c3 = st.columns([2, 1, 1])
        search = c1.text_input("Search users", placeholder="Name, username, employee ID, e-mail or team")
        role = c2.selectbox("Role", ["", *ROLE_LABELS], format_func=lambda r: ROLE_LABELS.get(r, "All roles"))
        status = c3.selectbox("Status", ["all", "active", "inactive"], format_func=str.capitalize)
        active = None if status == "all" else status == "active"
        users = run_service(
            lambda s: user_service.list_users(s, actor, search=search or None, role=role or None, active=active)
        )
        if users is None:
            return
        st.caption(f"{len(users)} user(s)")
        selected = _users_table(users)
        if selected is None:
            st.caption("👆 Click a row to manage that user.")
            return
        user = next((u for u in users if u.id == selected), None)
        if user is not None:
            with st.container(border=True):
                _user_actions(actor, user)
