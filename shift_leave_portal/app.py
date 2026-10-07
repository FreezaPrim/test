"""Streamlit entry point: ``streamlit run app.py``.

Responsibilities: page config, session enforcement and role-based navigation.
Only pages permitted for the signed-in role are registered with
``st.navigation``, and every page re-checks permissions itself.
"""

from __future__ import annotations

import logging
import os

import streamlit as st
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from config.settings import ConfigurationError, configure_logging, get_settings
from database.connection import get_engine, session_scope
from database.migrate import upgrade_database
from database.schemas import friendly_errors
from database.seed import ensure_admin_from_env
from services.errors import AppError
from ui import (
    admin_audit,
    admin_dashboard,
    admin_submissions,
    admin_users,
    advisor_dashboard,
    advisor_forms,
    advisor_submissions,
    login,
)
from ui.components import md_escape
from ui.navigation import PAGES
from ui.session import end_session, enforce_session

logger = logging.getLogger(__name__)


def _load_secrets_into_env() -> None:
    """Expose top-level Streamlit secrets as environment variables.

    Hosted platforms such as Streamlit Community Cloud provide configuration
    through ``st.secrets`` instead of a ``.env`` file. Real environment
    variables always win; only plain top-level values are copied.
    """
    try:
        items = dict(st.secrets)
    except Exception:  # noqa: BLE001 - no secrets file is the normal local case
        return
    for key, value in items.items():
        if isinstance(value, (str, int, float, bool)) and key.isupper():
            os.environ.setdefault(key, str(value).lower() if isinstance(value, bool) else str(value))


def _harden_client() -> None:
    """Apply security-relevant client options in code.

    ``.streamlit/config.toml`` sets these too, but hosts that run the app from
    another folder (Community Cloud runs from the repository root) don't read
    that file.
    """
    st.set_option("client.showErrorDetails", "none")
    st.set_option("client.toolbarMode", "minimal")


@st.cache_resource(show_spinner=False)
def _bootstrap() -> str | None:
    """Initialise logging and the DB engine once per process.

    With ``AUTO_INIT_DB=true`` the database is migrated and the first admin is
    created from ``INITIAL_ADMIN_*`` settings when none exists (for hosts where
    nobody can run ``manage.py``). Returns an error message for the UI, or
    None when everything is ready. Only non-sensitive, process-wide resources
    are cached here.
    """
    log = logging.getLogger(__name__)
    try:
        settings = get_settings()
        configure_logging(settings)
        if settings.auto_init_db:
            upgrade_database(configure_logger=False)
            with session_scope(write=True) as session:
                created = ensure_admin_from_env(session)
            if created:
                log.info("Created initial admin account %s from configuration", created)
        tables = set(inspect(get_engine()).get_table_names())
    except ConfigurationError as exc:
        return f"Configuration error: {exc}"
    except PydanticValidationError as exc:
        # Never show str(exc): Pydantic includes the input values (the password).
        problems = "; ".join(friendly_errors(exc))
        log.error("Initial admin could not be created: %s", problems)
        return f"The initial admin account could not be created. Check the INITIAL_ADMIN_* settings: {problems}"
    except (ValueError, AppError) as exc:
        log.error("Initial admin could not be created: %s", exc)
        return f"The initial admin account could not be created. Check the INITIAL_ADMIN_* settings: {exc}"
    except SQLAlchemyError:
        log.exception("Database connection failed")
        return "The database is not reachable. Please contact the administrator."
    if not {"users", "submissions", "audit_logs"} <= tables:
        return "The database has not been initialised. Run `python manage.py init-db` first."
    return None


def _sidebar(user_label: str, role: str, team: str | None) -> None:
    with st.sidebar:
        st.markdown(f"**{md_escape(user_label)}**")
        st.caption(f"{role.capitalize()}" + (f" · {md_escape(team)}" if team else ""))
        if st.button("Sign out", icon=":material/logout:", width="stretch"):
            end_session(message="You have signed out.")
            st.rerun()
        settings = get_settings()
        st.caption(f"Times shown in {settings.timezone_name}")


def main() -> None:
    """Render the application."""
    _load_secrets_into_env()
    settings_error = None
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        settings_error = str(exc)
    st.set_page_config(
        page_title="Shift Outage & Leave Portal" if settings_error else settings.app_name,
        page_icon=":material/schedule:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _harden_client()
    if settings_error:
        st.error(f"Configuration error: {settings_error}")
        st.stop()
    error = _bootstrap()
    if error:
        st.error(error)
        st.stop()

    user = enforce_session()
    PAGES.clear()

    if user is None:
        nav = st.navigation([st.Page(login.render_login, title="Sign in", icon=":material/login:", url_path="login")],
                           position="hidden")
        nav.run()
        return

    if user.must_change_password:
        _sidebar(user.full_name, user.role, user.team)
        nav = st.navigation(
            [st.Page(login.render_forced_password_change, title="Set new password",
                     icon=":material/key:", url_path="set-password")],
            position="hidden",
        )
        nav.run()
        return

    account = st.Page(login.render_change_password, title="My account", icon=":material/person:", url_path="account")
    if user.is_admin:
        PAGES.update(
            dashboard=st.Page(admin_dashboard.render, title="Dashboard", icon=":material/monitoring:",
                              url_path="admin-dashboard", default=True),
            submissions=st.Page(admin_submissions.render, title="All submissions", icon=":material/table_view:",
                                url_path="all-submissions"),
            users=st.Page(admin_users.render, title="User management", icon=":material/group:", url_path="users"),
            audit=st.Page(admin_audit.render, title="Audit log", icon=":material/history:", url_path="audit"),
            account=account,
        )
        sections = {
            "Overview": [PAGES["dashboard"], PAGES["submissions"]],
            "Administration": [PAGES["users"], PAGES["audit"]],
            "Account": [account],
        }
    else:
        PAGES.update(
            dashboard=st.Page(advisor_dashboard.render, title="Dashboard", icon=":material/home:",
                              url_path="dashboard", default=True),
            add_outage=st.Page(advisor_forms.render_add_outage, title="Report an outage", icon=":material/wifi_off:",
                               url_path="add-outage"),
            add_leave=st.Page(advisor_forms.render_add_leave, title="Request annual leave",
                              icon=":material/beach_access:", url_path="add-leave"),
            my_submissions=st.Page(advisor_submissions.render, title="My submissions", icon=":material/list_alt:",
                                   url_path="my-submissions"),
            account=account,
        )
        sections = {
            "Overview": [PAGES["dashboard"], PAGES["my_submissions"]],
            "Submit": [PAGES["add_outage"], PAGES["add_leave"]],
            "Account": [account],
        }

    nav = st.navigation(sections)
    _sidebar(user.full_name, user.role, user.team)
    nav.run()


if __name__ == "__main__":  # Streamlit executes the script as __main__
    main()
