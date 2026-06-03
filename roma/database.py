"""SQLite-backed data layer for Roma.

Tabular files become tables. Unstructured files (PDF/TXT and free-text JSON)
become rows in a `documents` table. This module also introspects the schema,
detects likely join keys between tables, runs read-only SQL, and searches
document text.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from . import config

# Internal bookkeeping tables Roma uses that should be hidden from the catalog.
INTERNAL_TABLES = {"documents", "_roma_sources", "sqlite_sequence"}

_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|replace|"
    r"truncate|pragma|vacuum|reindex|grant|revoke)\b",
    re.IGNORECASE,
)


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            title TEXT,
            chunk_index INTEGER DEFAULT 0,
            content TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _roma_sources (
            name TEXT,
            kind TEXT,          -- 'table' or 'document'
            origin_file TEXT,
            rows INTEGER,
            added_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    # Upgrade documents table to FTS5 for ranked full-text search.
    # Inspired by OpenJarvis SQLiteMemory (src/openjarvis/tools/storage/sqlite.py).
    _upgrade_fts5(conn)


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        opts = conn.execute("PRAGMA compile_options").fetchall()
        return any("FTS5" in (o[0] or "").upper() for o in opts)
    except Exception:  # noqa: BLE001
        return False


def _upgrade_fts5(conn: sqlite3.Connection) -> None:
    """Create documents_fts (FTS5 virtual table) if FTS5 is available.

    The FTS5 table is a content table backed by `documents`, so it stays
    in sync automatically on INSERT and can be rebuilt with `INSERT INTO
    documents_fts(documents_fts) VALUES('rebuild')`.
    """
    if not _fts5_available(conn):
        return
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "documents_fts" not in existing:
        try:
            conn.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts
                    USING fts5(
                        source, title, content,
                        content='documents', content_rowid='id',
                        tokenize='porter unicode61'
                    );
                CREATE TRIGGER IF NOT EXISTS docs_ai
                    AFTER INSERT ON documents BEGIN
                    INSERT INTO documents_fts(rowid, source, title, content)
                        VALUES (new.id, new.source, new.title, new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS docs_ad
                    AFTER DELETE ON documents BEGIN
                    INSERT INTO documents_fts(documents_fts, rowid, source, title, content)
                        VALUES ('delete', old.id, old.source, old.title, old.content);
                END;
            """)
            conn.commit()
        except Exception:  # noqa: BLE001
            pass  # older SQLite without FTS5 — graceful degrade


def sanitize_identifier(name: str) -> str:
    """Turn an arbitrary string into a safe SQL identifier."""
    safe = re.sub(r"\W+", "_", str(name).strip()).strip("_")
    if not safe:
        safe = "col"
    if safe[0].isdigit():
        safe = "_" + safe
    return safe


def _unique_table_name(conn: sqlite3.Connection, base: str) -> str:
    existing = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    name = base
    i = 2
    while name in existing:
        name = f"{base}_{i}"
        i += 1
    return name


def load_dataframe(conn: sqlite3.Connection, base_name: str, df: pd.DataFrame,
                   origin_file: str) -> dict[str, Any]:
    """Store a DataFrame as a new SQLite table; return a summary."""
    df = df.copy()
    df.columns = [sanitize_identifier(c) for c in df.columns]
    # De-duplicate any column names that collided after sanitizing.
    seen: dict[str, int] = {}
    new_cols = []
    for c in df.columns:
        if c in seen:
            seen[c] += 1
            new_cols.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            new_cols.append(c)
    df.columns = new_cols

    table = _unique_table_name(conn, sanitize_identifier(base_name))
    df.to_sql(table, conn, if_exists="fail", index=False)
    conn.execute(
        "INSERT INTO _roma_sources (name, kind, origin_file, rows) VALUES (?,?,?,?)",
        (table, "table", origin_file, len(df)),
    )
    conn.commit()
    return {"kind": "table", "name": table, "rows": len(df),
            "columns": list(df.columns)}


def add_document(conn: sqlite3.Connection, source: str, title: str,
                 text: str, origin_file: str, chunk_chars: int = 4000) -> dict[str, Any]:
    """Store free text, split into chunks, in the documents table."""
    chunks = [text[i:i + chunk_chars] for i in range(0, len(text), chunk_chars)] or [""]
    for idx, chunk in enumerate(chunks):
        conn.execute(
            "INSERT INTO documents (source, title, chunk_index, content) VALUES (?,?,?,?)",
            (source, title, idx, chunk),
        )
    conn.execute(
        "INSERT INTO _roma_sources (name, kind, origin_file, rows) VALUES (?,?,?,?)",
        (source, "document", origin_file, len(chunks)),
    )
    conn.commit()
    return {"kind": "document", "name": source, "chunks": len(chunks)}


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    return [r["name"] for r in rows if r["name"] not in INTERNAL_TABLES]


def list_tables(conn: sqlite3.Connection, sample_rows: int = 3) -> list[dict[str, Any]]:
    """Return schema + a small sample for every user table."""
    out = []
    for table in _table_names(conn):
        cols = [
            {"name": r["name"], "type": r["type"] or "TEXT"}
            for r in conn.execute(f'PRAGMA table_info("{table}")')
        ]
        count = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
        sample = [
            dict(r) for r in conn.execute(
                f'SELECT * FROM "{table}" LIMIT {int(sample_rows)}'
            )
        ]
        out.append({"table": table, "row_count": count,
                    "columns": cols, "sample": sample})
    return out


def detect_links(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Find columns sharing the same (sanitized) name across tables.

    These are candidate keys for joining files together.
    """
    col_map: dict[str, list[str]] = {}
    for table in _table_names(conn):
        for r in conn.execute(f'PRAGMA table_info("{table}")'):
            col_map.setdefault(r["name"].lower(), []).append(table)
    links = []
    for col, tables in col_map.items():
        if len(set(tables)) > 1:
            links.append({"column": col, "shared_by": sorted(set(tables))})
    # Sort so id-like keys surface first.
    links.sort(key=lambda l: (0 if re.search(r"id|key|no|number|code", l["column"])
                              else 1, l["column"]))
    return links


def document_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT source, title, COUNT(*) AS chunks FROM documents GROUP BY source, title"
    )
    return [dict(r) for r in rows]


def run_sql(conn: sqlite3.Connection, query: str, max_rows: int = 200) -> dict[str, Any]:
    """Run a single read-only SELECT/WITH query and return rows."""
    q = query.strip().rstrip(";").strip()
    if ";" in q:
        return {"error": "Only one statement is allowed (no semicolons)."}
    head = q.lower().lstrip("(")
    if not (head.startswith("select") or head.startswith("with")):
        return {"error": "Only SELECT / WITH queries are allowed."}
    if _WRITE_KEYWORDS.search(q):
        return {"error": "Query contains a forbidden keyword; reads only."}
    # Belt and suspenders: force the connection read-only for this call.
    conn.execute("PRAGMA query_only = ON")
    try:
        cur = conn.execute(q)
        rows = [dict(r) for r in cur.fetchmany(max_rows + 1)]
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        cols = [d[0] for d in cur.description] if cur.description else []
        return {"columns": cols, "rows": rows, "row_count": len(rows),
                "truncated": truncated}
    except Exception as exc:  # noqa: BLE001 - surface the DB error to the model
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.execute("PRAGMA query_only = OFF")


def search_documents(conn: sqlite3.Connection, keywords: str,
                     limit: int = 10) -> dict[str, Any]:
    """Full-text search across stored document chunks.

    Uses FTS5 BM25 ranking when available (fast, relevance-ranked),
    falls back to LIKE-based search for older SQLite builds.
    Approach inspired by OpenJarvis SQLiteMemory.retrieve().
    """
    keywords = keywords.strip()
    if not keywords:
        return {"matches": []}

    # ── FTS5 path ────────────────────────────────────────────────────────
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "documents_fts" in existing:
        try:
            # Porter-stem query: each word is an implicit AND prefix match
            fts_query = " ".join(
                f'"{t}"*' if len(t) > 3 else f'"{t}"'
                for t in re.split(r"\s+", keywords) if t
            )
            rows = conn.execute(
                """
                SELECT d.source, d.title, d.chunk_index, d.content,
                       bm25(documents_fts) AS score
                FROM documents_fts
                JOIN documents d ON documents_fts.rowid = d.id
                WHERE documents_fts MATCH ?
                ORDER BY score           -- lower bm25 = more relevant
                LIMIT ?
                """,
                (fts_query, int(limit)),
            ).fetchall()
            matches = []
            for r in rows:
                snippet = r["content"]
                if len(snippet) > 600:
                    snippet = snippet[:600] + " …"
                matches.append({"source": r["source"], "title": r["title"],
                                 "chunk": r["chunk_index"], "text": snippet,
                                 "score": round(float(r["score"]), 3)})
            return {"matches": matches, "engine": "fts5"}
        except Exception:  # noqa: BLE001
            pass  # fall through to LIKE

    # ── LIKE fallback ────────────────────────────────────────────────────
    terms = [t for t in re.split(r"\s+", keywords) if t]
    where = " AND ".join("content LIKE ?" for _ in terms)
    params = [f"%{t}%" for t in terms]
    rows = conn.execute(
        f"SELECT source, title, chunk_index, content FROM documents "
        f"WHERE {where} LIMIT {int(limit)}",
        params,
    ).fetchall()
    matches = []
    for r in rows:
        snippet = r["content"]
        if len(snippet) > 600:
            snippet = snippet[:600] + " …"
        matches.append({"source": r["source"], "title": r["title"],
                         "chunk": r["chunk_index"], "text": snippet})
    return {"matches": matches, "engine": "like"}


def reset(conn: sqlite3.Connection) -> None:
    """Drop every user table and clear documents."""
    for table in _table_names(conn):
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute("DELETE FROM documents")
    conn.execute("DELETE FROM _roma_sources")
    conn.commit()
