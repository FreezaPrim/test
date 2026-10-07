"""Sign-in page and password-change page."""

from __future__ import annotations

import time

import streamlit as st

from config.settings import get_settings
from services import auth_service
from ui.components import (
    AUTH_KEY,
    client_ip,
    md_escape,
    page_header,
    require_user,
    run_service,
    show_flashes,
)
from ui.session import start_session

_FAILS_KEY = "_login_failures"
_NEXT_TRY_KEY = "_login_next_try"
_THROTTLE_AFTER = 3
_MAX_DELAY_SECONDS = 60


def _throttle_remaining() -> int:
    return max(0, int(st.session_state.get(_NEXT_TRY_KEY, 0) - time.time()))


def _register_failure() -> None:
    fails = int(st.session_state.get(_FAILS_KEY, 0)) + 1
    st.session_state[_FAILS_KEY] = fails
    if fails >= _THROTTLE_AFTER:
        delay = min(2 ** (fails - _THROTTLE_AFTER + 1), _MAX_DELAY_SECONDS)
        st.session_state[_NEXT_TRY_KEY] = time.time() + delay


def render_login() -> None:
    """Render the sign-in form.

    Protection against repeated attempts is layered: a growing delay per
    browser session (here) plus a per-account lockout in the auth service.
    """
    settings = get_settings()
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.title(settings.app_name)
        st.caption("Sign in with your company username and password.")
        show_flashes()
        remaining = _throttle_remaining()
        if remaining:
            st.warning(f"Too many attempts. Please wait {remaining} seconds before trying again.")
        with st.form("login_form", clear_on_submit=False, enter_to_submit=True):
            username = st.text_input("Username", max_chars=64, autocomplete="username")
            password = st.text_input("Password", type="password", max_chars=128, autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if not submitted:
            return
        if _throttle_remaining():
            st.error("Please wait a moment before trying again.")
            return
        if not username.strip() or not password:
            st.error("Please enter both your username and password.")
            return
        result = run_service(lambda s: auth_service.authenticate(s, username, password, client_ip()), write=True)
        if result is None:
            return
        if not result.ok:
            _register_failure()
            st.error(result.error or auth_service.GENERIC_LOGIN_ERROR)
            return
        assert result.user is not None
        start_session(result.user)
        st.rerun()


def render_change_password(*, forced: bool = False) -> None:
    """Let the signed-in user change their own password."""
    user = require_user()
    if forced:
        page_header("Set a new password", "For your security you must choose a new password before continuing.")
    else:
        page_header("My account")
        cols = st.columns(3)
        cols[0].markdown(f"**Name**  \n{md_escape(user.full_name)}")
        cols[1].markdown(f"**Username**  \n{md_escape(user.username)}")
        cols[2].markdown(f"**Employee ID**  \n{user.employee_id}")
        st.markdown(f"**Role:** {user.role.capitalize()} &nbsp;&nbsp; **Team:** {md_escape(user.team or '—')}")
        st.divider()
        st.subheader("Change password")

    min_len = get_settings().password_min_length
    st.caption(
        f"Use at least {min_len} characters with uppercase and lowercase letters, a digit and a symbol. "
        "Do not include your username."
    )
    with st.form("change_password_form", clear_on_submit=True):
        current = st.text_input("Current password", type="password", autocomplete="current-password")
        new = st.text_input("New password", type="password", autocomplete="new-password")
        confirm = st.text_input("Repeat new password", type="password", autocomplete="new-password")
        submitted = st.form_submit_button("Update password", type="primary")
    if not submitted:
        return
    if new != confirm:
        st.error("The new passwords do not match.")
        return

    refreshed = run_service(
        lambda s: auth_service.change_password(s, user, current, new, client_ip()), write=True
    )
    if refreshed is not None:
        st.session_state[AUTH_KEY] = refreshed
        st.success("Your password has been updated.")
        if forced:
            st.rerun()


def render_forced_password_change() -> None:
    """Page shown when the user must replace a temporary/reset password."""
    render_change_password(forced=True)
