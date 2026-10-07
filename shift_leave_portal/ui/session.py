"""Streamlit session lifecycle: sign-in, idle timeout, re-validation, sign-out."""

from __future__ import annotations

import time

import streamlit as st

from config.settings import get_settings
from database.schemas import AuthUser
from services import auth_service
from ui.components import AUTH_KEY, client_ip, flash, run_service

LAST_ACTIVITY_KEY = "_last_activity"
SESSION_STARTED_KEY = "_session_started"


def start_session(user: AuthUser) -> None:
    """Store the authenticated principal, discarding any previous state."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    now = time.time()
    st.session_state[AUTH_KEY] = user
    st.session_state[LAST_ACTIVITY_KEY] = now
    st.session_state[SESSION_STARTED_KEY] = now


def end_session(*, expired: bool = False, message: str | None = None) -> None:
    """Audit and clear the session."""
    user = st.session_state.get(AUTH_KEY)
    if isinstance(user, AuthUser):
        run_service(lambda s: auth_service.logout(s, user, client_ip(), expired=expired), write=True)
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if message:
        flash(message, "info")


def enforce_session() -> AuthUser | None:
    """Validate the current session on every script run.

    * Ends sessions idle longer than ``SESSION_TIMEOUT_MINUTES``.
    * Ends sessions older than ``SESSION_MAX_HOURS``.
    * Re-loads the user from the database so deactivation, password resets
      and role changes take effect immediately.
    """
    user = st.session_state.get(AUTH_KEY)
    if not isinstance(user, AuthUser):
        return None
    settings = get_settings()
    now = time.time()
    last = float(st.session_state.get(LAST_ACTIVITY_KEY, 0))
    started = float(st.session_state.get(SESSION_STARTED_KEY, 0))
    if now - last > settings.session_timeout_minutes * 60 or now - started > settings.session_max_hours * 3600:
        end_session(expired=True, message="Your session expired. Please sign in again.")
        return None

    refreshed = run_service(lambda s: auth_service.refresh_session_user(s, user))
    if refreshed is None:
        end_session(message="Your session is no longer valid. Please sign in again.")
        return None
    st.session_state[AUTH_KEY] = refreshed
    st.session_state[LAST_ACTIVITY_KEY] = now
    return refreshed
