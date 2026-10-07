"""The Alembic migrations must produce exactly the ORM schema."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from database.connection import create_app_engine
from database.migrate import upgrade_database
from database.models import Base


def test_migrations_match_models(tmp_path):
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    upgrade_database(url, configure_logger=False)
    engine = create_app_engine(url)
    try:
        assert {"users", "submissions", "attachments", "audit_logs"} <= set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == []
    finally:
        engine.dispose()
