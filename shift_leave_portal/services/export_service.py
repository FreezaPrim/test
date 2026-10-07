"""CSV and Excel export of submissions (admin only).

* The export re-uses the exact query builder behind the admin table, so it
  always respects the filters currently selected.
* Text cells are protected against CSV/Excel formula injection.
* Every export is audit-logged with its filters, format and row count.
* Nothing here is cached: exports contain personal data and must always
  reflect the current permissions and data.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from config.settings import get_settings
from database.schemas import AuthUser, SubmissionFilters
from services import audit_service
from services.audit_service import AuditAction
from services.errors import ValidationError
from services.submission_service import (
    SORT_OPTIONS,
    SubmissionView,
    build_filtered_query,
    to_view,
)
from utils.datetime_utils import now_local, to_local
from utils.permissions import require_admin
from utils.security import neutralize_spreadsheet_formula

ExportFormat = Literal["csv", "xlsx"]

EXPORT_COLUMNS: list[str] = [
    "Submission ID",
    "Employee ID",
    "Username",
    "Full name",
    "Team",
    "Submission type",
    "Status",
    "Start date/time",
    "End date/time",
    "Reason category",
    "Impact level",
    "Outage channel",
    "Ticket reference",
    "Description",
    "Admin comment",
    "Reviewer",
    "Created date",
    "Reviewed date",
]
DATETIME_COLUMNS = ("Start date/time", "End date/time", "Created date", "Reviewed date")
TYPE_LABELS = {"outage": "Outage", "annual_leave": "Annual leave"}
MIME_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@dataclass(frozen=True)
class ExportFile:
    """A generated export ready for ``st.download_button``."""

    filename: str
    mime: str
    data: bytes
    row_count: int


def _local_naive(value: datetime | None) -> datetime | None:
    local = to_local(value)
    return local.replace(tzinfo=None) if local else None


def _row(view: SubmissionView) -> dict[str, object]:
    return {
        "Submission ID": view.id,
        "Employee ID": view.employee_id,
        "Username": view.username,
        "Full name": view.full_name,
        "Team": view.team,
        "Submission type": TYPE_LABELS.get(view.submission_type, view.submission_type),
        "Status": view.status.capitalize(),
        "Start date/time": _local_naive(view.start),
        "End date/time": _local_naive(view.end),
        "Reason category": view.reason_category,
        "Impact level": view.impact_level.capitalize() if view.impact_level else None,
        "Outage channel": view.outage_channel,
        "Ticket reference": view.ticket_reference,
        "Description": view.description,
        "Admin comment": view.admin_comment,
        "Reviewer": view.reviewer_name,
        "Created date": _local_naive(view.created_at),
        "Reviewed date": _local_naive(view.reviewed_at),
    }


def build_export_dataframe(session: Session, actor: AuthUser, filters: SubmissionFilters) -> pd.DataFrame:
    """Return a DataFrame of submissions matching ``filters``. Admin only.

    Datetimes are converted to naive values in the application timezone
    (Excel cannot store timezone-aware datetimes).
    """
    require_admin(actor)
    limit = get_settings().export_row_limit
    stmt = build_filtered_query(filters).order_by(*SORT_OPTIONS["start_desc"])
    rows = session.scalars(stmt.limit(limit + 1)).unique().all()
    if len(rows) > limit:
        raise ValidationError(
            f"The export would contain more than {limit:,} rows. Please narrow the filters."
        )
    df = pd.DataFrame([_row(to_view(r)) for r in rows], columns=EXPORT_COLUMNS)
    for col in DATETIME_COLUMNS:
        df[col] = pd.to_datetime(df[col])
    return df


def _neutralize(df: pd.DataFrame) -> pd.DataFrame:
    safe = df.copy()
    for col in safe.columns:
        if col not in DATETIME_COLUMNS and col != "Submission ID":
            safe[col] = safe[col].map(neutralize_spreadsheet_formula).astype(object)
    return safe


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """Serialise to UTF-8 CSV (with BOM so Excel detects the encoding)."""
    safe = _neutralize(df)
    return safe.to_csv(index=False, date_format="%Y-%m-%d %H:%M").encode("utf-8-sig")


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Serialise to an XLSX workbook with a formatted header row."""
    safe = _neutralize(df)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl", datetime_format="yyyy-mm-dd hh:mm") as writer:
        safe.to_excel(writer, index=False, sheet_name="Submissions")
        sheet = writer.sheets["Submissions"]
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for idx, col in enumerate(safe.columns, start=1):
            longest = max([len(str(col))] + [len(str(v)) for v in safe[col].head(200) if v is not None])
            sheet.column_dimensions[get_column_letter(idx)].width = min(max(12, longest + 2), 60)
    return buffer.getvalue()


def export_submissions(
    session: Session,
    actor: AuthUser,
    filters: SubmissionFilters,
    fmt: ExportFormat,
    ip_address: str | None = None,
) -> ExportFile:
    """Generate an export file for the current filters and audit it. Admin only."""
    require_admin(actor)
    if fmt not in MIME_TYPES:
        raise ValidationError("Unsupported export format.")
    df = build_export_dataframe(session, actor, filters)
    data = to_csv_bytes(df) if fmt == "csv" else to_excel_bytes(df)
    stamp = now_local().strftime("%Y%m%d_%H%M")
    audit_service.record(
        session,
        action=AuditAction.DATA_EXPORTED,
        entity_type="submission",
        actor_id=actor.id,
        new_values={"format": fmt, "row_count": len(df), "filters": filters.as_audit_dict()},
        ip_address=ip_address,
    )
    return ExportFile(
        filename=f"submissions_{stamp}.{fmt}",
        mime=MIME_TYPES[fmt],
        data=data,
        row_count=len(df),
    )
