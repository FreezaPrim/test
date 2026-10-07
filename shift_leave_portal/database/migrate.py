"""Programmatic access to the Alembic migrations (used by manage.py and app start-up)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from config.settings import PROJECT_ROOT


def alembic_config(database_url: str | None = None) -> Config:
    """Return an Alembic config pointing at this project's migrations.

    Args:
        database_url: overrides ``DATABASE_URL`` for this run (used by tests).
    """
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    if database_url:
        cfg.attributes["database_url"] = database_url
    return cfg


def upgrade_database(database_url: str | None = None, *, configure_logger: bool = True) -> None:
    """Apply all pending migrations (``alembic upgrade head``)."""
    cfg = alembic_config(database_url)
    cfg.attributes["configure_logger"] = configure_logger
    command.upgrade(cfg, "head")
