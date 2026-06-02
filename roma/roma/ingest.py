"""Ingestion: read files of many formats into Roma's database.

Tabular data (Excel sheets, CSV/TSV, list-of-records JSON) becomes SQLite
tables. Everything else (PDF, TXT/MD, free-form JSON) becomes searchable
documents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from . import database

TABULAR_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm"}
TEXT_EXT = {".txt", ".md", ".log", ".rst"}

# Everything Roma knows how to ingest - used when scanning a folder.
SUPPORTED_EXT = TABULAR_EXT | TEXT_EXT | {".json", ".pdf"}


def ingest_file(conn, path: str | Path) -> dict[str, Any]:
    """Ingest one file. Returns a summary dict (or {'error': ...})."""
    p = Path(path)
    if not p.exists():
        return {"file": str(p), "error": "File not found."}

    ext = p.suffix.lower()
    stem = p.stem
    try:
        if ext in {".xlsx", ".xls", ".xlsm"}:
            return _ingest_excel(conn, p, stem)
        if ext == ".csv":
            return _ingest_table(conn, p, stem, pd.read_csv(p))
        if ext == ".tsv":
            return _ingest_table(conn, p, stem, pd.read_csv(p, sep="\t"))
        if ext == ".json":
            return _ingest_json(conn, p, stem)
        if ext == ".pdf":
            return _ingest_pdf(conn, p, stem)
        if ext in TEXT_EXT:
            return _ingest_text(conn, p, stem, p.read_text(encoding="utf-8",
                                                           errors="replace"))
        return {"file": str(p),
                "error": f"Unsupported extension '{ext}'. "
                         f"Supported: xlsx, xls, csv, tsv, json, pdf, txt, md."}
    except Exception as exc:  # noqa: BLE001
        return {"file": str(p), "error": f"{type(exc).__name__}: {exc}"}


def _ingest_table(conn, p: Path, base: str, df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"file": str(p), "error": "No rows found."}
    res = database.load_dataframe(conn, base, df, origin_file=p.name)
    res["file"] = p.name
    return res


def _ingest_excel(conn, p: Path, base: str) -> dict[str, Any]:
    xl = pd.ExcelFile(p)
    loaded = []
    for sheet in xl.sheet_names:
        df = xl.parse(sheet)
        if df.empty:
            continue
        name = base if len(xl.sheet_names) == 1 else f"{base}_{sheet}"
        res = database.load_dataframe(conn, name, df, origin_file=p.name)
        loaded.append(res)
    if not loaded:
        return {"file": str(p), "error": "Workbook had no non-empty sheets."}
    return {"file": p.name, "kind": "excel", "tables": loaded}


def _ingest_json(conn, p: Path, base: str) -> dict[str, Any]:
    data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    # A list of flat records -> a table.
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        df = pd.json_normalize(data)
        return _ingest_table(conn, p, base, df)
    # A dict whose top value is a list of records -> a table.
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
                df = pd.json_normalize(value)
                return _ingest_table(conn, p, f"{base}_{key}", df)
    # Otherwise keep it as a searchable document.
    res = database.add_document(conn, source=p.name, title=base,
                                text=json.dumps(data, indent=2, ensure_ascii=False),
                                origin_file=p.name)
    res["file"] = p.name
    return res


def _ingest_pdf(conn, p: Path, base: str) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"file": str(p),
                "error": "pypdf is not installed. Run: pip install pypdf"}
    reader = PdfReader(str(p))
    text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if not text.strip():
        return {"file": str(p),
                "error": "No extractable text (PDF may be scanned images)."}
    res = database.add_document(conn, source=p.name, title=base, text=text,
                                origin_file=p.name)
    res["file"] = p.name
    res["pages"] = len(reader.pages)
    return res


def _ingest_text(conn, p: Path, base: str, text: str) -> dict[str, Any]:
    res = database.add_document(conn, source=p.name, title=base, text=text,
                                origin_file=p.name)
    res["file"] = p.name
    return res
