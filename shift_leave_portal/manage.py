"""Command-line management tasks.

Usage::

    python manage.py init-db          # create / migrate the database (Alembic)
    python manage.py create-admin     # interactively create the first admin
    python manage.py seed-demo        # demo users + data (APP_ENV=development only)
    python manage.py backup-db        # consistent online backup of the SQLite file
"""

from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.engine import make_url

from config.settings import PROJECT_ROOT, configure_logging, get_settings
from database.connection import session_scope
from database.schemas import friendly_errors
from database.seed import (
    DEMO_PASSWORD_ADMIN,
    DEMO_PASSWORD_ADVISOR,
    admin_exists,
    create_initial_admin,
    seed_demo_data,
)
from services.errors import AppError
from utils.datetime_utils import now_local


def alembic_config() -> Config:
    """Return an Alembic config pointing at this project's migrations."""
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return cfg


def cmd_init_db(_args: argparse.Namespace) -> int:
    """Apply all database migrations."""
    command.upgrade(alembic_config(), "head")
    print("Database is up to date.")
    return 0


def _prompt(label: str, env_name: str, required: bool = True) -> str | None:
    value = os.getenv(env_name)
    if value:
        return value
    while True:
        try:
            value = input(f"{label}: ").strip()
        except EOFError:  # non-interactive run without the env variable
            if not required:
                return None
            raise SystemExit(f"{label} is required (set {env_name}).") from None
        if value or not required:
            return value or None
        print(f"{label} is required.")


def cmd_create_admin(args: argparse.Namespace) -> int:
    """Create an admin account from prompts or INITIAL_ADMIN_* variables."""
    with session_scope() as session:
        if admin_exists(session) and not args.allow_additional:
            print("An admin account already exists. Use the app's User Management page, "
                  "or pass --allow-additional to create another admin from the CLI.")
            return 1

    username = _prompt("Username", "INITIAL_ADMIN_USERNAME")
    full_name = _prompt("Full name", "INITIAL_ADMIN_FULL_NAME")
    employee_id = _prompt("Employee ID", "INITIAL_ADMIN_EMPLOYEE_ID")
    email = _prompt("E-mail (optional)", "INITIAL_ADMIN_EMAIL", required=False)
    password = os.getenv("INITIAL_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            return 1
    try:
        with session_scope(write=True) as session:
            user = create_initial_admin(
                session,
                username=username or "",
                full_name=full_name or "",
                employee_id=employee_id or "",
                email=email,
                password=password,
            )
    except PydanticValidationError as exc:
        print("Could not create admin:\n  - " + "\n  - ".join(friendly_errors(exc)))
        return 1
    except (AppError, ValueError) as exc:
        print(f"Could not create admin: {exc}")
        return 1
    print(f"Admin '{user.username}' created.")
    if os.getenv("INITIAL_ADMIN_PASSWORD"):
        print("Remove INITIAL_ADMIN_PASSWORD from your environment/.env now.")
    return 0


def cmd_seed_demo(_args: argparse.Namespace) -> int:
    """Create demo users and data for local development."""
    if not get_settings().is_development:
        print("Refusing to seed demo data: set APP_ENV=development (local use only).")
        return 1
    with session_scope(write=True) as session:
        created = seed_demo_data(session)
    if not created:
        print("Demo users already exist; nothing to do.")
        return 0
    print("Created demo users (LOCAL DEVELOPMENT ONLY):")
    for name in created:
        pw = DEMO_PASSWORD_ADMIN if name == "demo.admin" else DEMO_PASSWORD_ADVISOR
        print(f"  {name:<15} {pw}")
    return 0


def cmd_backup_db(args: argparse.Namespace) -> int:
    """Create a consistent copy of the SQLite database using the online backup API."""
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        print("backup-db only supports file-based SQLite databases.")
        return 1
    source = Path(url.database)
    if not source.exists():
        print(f"Database file not found: {source}")
        return 1
    out_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{source.stem}_{now_local():%Y%m%d_%H%M%S}.db"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    with sqlite3.connect(target) as check:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"Backup written to {target} (integrity: {result})")
    return 0 if result == "ok" else 2


def main(argv: list[str] | None = None) -> int:
    """Entry point for the management CLI."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Shift Outage & Leave Portal management")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create or migrate the database").set_defaults(func=cmd_init_db)
    admin = sub.add_parser("create-admin", help="create an admin account")
    admin.add_argument("--allow-additional", action="store_true", help="allow creating another admin")
    admin.set_defaults(func=cmd_create_admin)
    sub.add_parser("seed-demo", help="seed demo data (development only)").set_defaults(func=cmd_seed_demo)
    backup = sub.add_parser("backup-db", help="back up the SQLite database")
    backup.add_argument("--output-dir", help="directory for the backup file (default: ./backups)")
    backup.set_defaults(func=cmd_backup_db)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
