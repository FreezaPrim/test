"""Database engine and transactional session management.

* SQLite is configured with foreign keys, WAL journaling and a busy timeout.
* :func:`session_scope` provides a unit of work that commits on success and
  rolls back on any exception.
* Write transactions use ``BEGIN IMMEDIATE`` so that check-then-insert logic
  (e.g. overlap detection) cannot race with another writer.
* File-system side effects (attachment files) are tied to the transaction
  outcome through ``session.info`` hooks.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from config.settings import get_settings

logger = logging.getLogger(__name__)

_BEGIN_MODES = {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_write_session_factory: sessionmaker[Session] | None = None

# session.info keys used for transactional file handling
FILES_DELETE_ON_COMMIT = "files_delete_on_commit"
FILES_DELETE_ON_ROLLBACK = "files_delete_on_rollback"


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        # Let SQLAlchemy (not the driver) control BEGIN so we can choose the mode.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Any) -> None:
        mode = str(conn.get_execution_options().get("sqlite_begin_mode", "DEFERRED")).upper()
        if mode not in _BEGIN_MODES:  # whitelist: never interpolate arbitrary text
            mode = "DEFERRED"
        conn.exec_driver_sql(f"BEGIN {mode}")


def create_app_engine(database_url: str | None = None, echo: bool | None = None) -> Engine:
    """Create a SQLAlchemy engine for ``database_url`` (defaults to settings)."""
    settings = get_settings()
    url = make_url(database_url or settings.database_url)
    connect_args: dict[str, Any] = {}
    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False  # Streamlit runs scripts in threads
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        url,
        echo=settings.sql_echo if echo is None else echo,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    if url.get_backend_name() == "sqlite":
        _configure_sqlite(engine)
    return engine


def _register_file_hooks(factory: sessionmaker[Session]) -> None:
    @event.listens_for(factory, "after_commit")
    def _after_commit(session: Session) -> None:
        for path in session.info.pop(FILES_DELETE_ON_COMMIT, []):
            _safe_unlink(path)
        session.info.pop(FILES_DELETE_ON_ROLLBACK, None)

    @event.listens_for(factory, "after_rollback")
    def _after_rollback(session: Session) -> None:
        for path in session.info.pop(FILES_DELETE_ON_ROLLBACK, []):
            _safe_unlink(path)
        session.info.pop(FILES_DELETE_ON_COMMIT, None)


def _safe_unlink(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete file %s", path, exc_info=True)


def init_engine(database_url: str | None = None, echo: bool | None = None) -> Engine:
    """(Re)initialise the global engine and session factories."""
    global _engine, _session_factory, _write_session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = create_app_engine(database_url, echo)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=True)
    _write_session_factory = sessionmaker(
        bind=_engine.execution_options(sqlite_begin_mode="IMMEDIATE"),
        expire_on_commit=False,
        autoflush=True,
    )
    _register_file_hooks(_session_factory)
    _register_file_hooks(_write_session_factory)
    return _engine


def get_engine() -> Engine:
    """Return the global engine, creating it on first use."""
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


@contextmanager
def session_scope(write: bool = False) -> Iterator[Session]:
    """Provide a transactional scope around a series of operations.

    Args:
        write: use ``BEGIN IMMEDIATE`` (SQLite) to serialise writers.

    The transaction is committed when the block exits normally and rolled
    back if any exception escapes.
    """
    get_engine()
    factory = _write_session_factory if write else _session_factory
    assert factory is not None
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
