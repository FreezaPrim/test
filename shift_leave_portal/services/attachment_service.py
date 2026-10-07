"""Evidence attachments for outage submissions.

Security measures:

* Only PNG, JPEG and PDF are accepted, verified by *file signature* (magic
  bytes), not just by extension or the browser-supplied MIME type.
* Files are stored under random names outside any web-served directory; the
  original filename is sanitised and kept only as metadata.
* Size and count limits are enforced.
* Files written during a failed transaction are removed on rollback, and files
  of deleted submissions are removed only after the deletion commits.
* Downloads go through :func:`get_attachment_content`, which re-checks
  authorisation and records an audit entry.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from config.settings import get_settings
from database.connection import FILES_DELETE_ON_COMMIT, FILES_DELETE_ON_ROLLBACK
from database.models import Attachment, Submission
from database.schemas import AuthUser
from services import audit_service
from services.audit_service import AuditAction
from services.errors import NotFoundError, ValidationError
from utils.permissions import require_view_submission

_SIGNATURES: dict[str, tuple[bytes, tuple[str, ...]]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n", (".png",)),
    "image/jpeg": (b"\xff\xd8\xff", (".jpg", ".jpeg")),
    "application/pdf": (b"%PDF-", (".pdf",)),
}
ALLOWED_EXTENSIONS: tuple[str, ...] = tuple(ext.lstrip(".") for _, exts in _SIGNATURES.values() for ext in exts)


@dataclass(frozen=True)
class UploadedFileData:
    """A file received from the UI (decoupled from Streamlit's UploadedFile)."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class AttachmentView:
    """Attachment metadata safe to show in the UI."""

    id: int
    filename: str
    content_type: str
    size_bytes: int


def _sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", Path(name or "file").name)
    name = re.sub(r"[^\w.\- ]", "_", name).strip(" .") or "file"
    return name[-120:]


def _detect_content_type(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    for content_type, (signature, extensions) in _SIGNATURES.items():
        if content.startswith(signature) and suffix in extensions:
            return content_type
    raise ValidationError(
        f"'{_sanitize_filename(filename)}' is not an allowed file. "
        "Please upload PNG, JPG or PDF files only."
    )


def validate_uploads(files: list[UploadedFileData]) -> None:
    """Validate count, size and type of uploads before anything is written."""
    settings = get_settings()
    if len(files) > settings.max_attachments_per_submission:
        raise ValidationError(
            f"You can attach at most {settings.max_attachments_per_submission} files."
        )
    for item in files:
        if not item.content:
            raise ValidationError(f"'{_sanitize_filename(item.filename)}' is empty.")
        if len(item.content) > settings.max_upload_bytes:
            raise ValidationError(
                f"'{_sanitize_filename(item.filename)}' is larger than {settings.max_upload_mb} MB."
            )
        _detect_content_type(item.filename, item.content)


def _storage_dir() -> Path:
    directory = get_settings().upload_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def store_attachments(
    session: Session, submission: Submission, files: list[UploadedFileData], uploaded_by: int
) -> list[Attachment]:
    """Persist uploads for ``submission`` (file on disk + metadata row)."""
    validate_uploads(files)
    created: list[Attachment] = []
    directory = _storage_dir()
    for item in files:
        content_type = _detect_content_type(item.filename, item.content)
        extension = _SIGNATURES[content_type][1][0]
        stored_name = f"{secrets.token_hex(16)}{extension}"
        path = directory / stored_name
        with open(path, "xb") as handle:  # "x": never overwrite an existing file
            handle.write(item.content)
        session.info.setdefault(FILES_DELETE_ON_ROLLBACK, []).append(path)
        attachment = Attachment(
            submission=submission,
            original_filename=_sanitize_filename(item.filename),
            stored_filename=stored_name,
            content_type=content_type,
            size_bytes=len(item.content),
            sha256=hashlib.sha256(item.content).hexdigest(),
            uploaded_by=uploaded_by,
        )
        session.add(attachment)
        created.append(attachment)
    return created


def schedule_file_removal(session: Session, submission: Submission) -> None:
    """Delete a submission's files from disk once the surrounding transaction commits."""
    directory = get_settings().upload_dir
    for attachment in submission.attachments:
        session.info.setdefault(FILES_DELETE_ON_COMMIT, []).append(directory / attachment.stored_filename)


def to_view(attachment: Attachment) -> AttachmentView:
    """Convert an ORM attachment to its view model."""
    return AttachmentView(
        id=attachment.id,
        filename=attachment.original_filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
    )


def get_attachment_content(
    session: Session, actor: AuthUser, attachment_id: int, ip_address: str | None = None
) -> tuple[str, str, bytes]:
    """Return ``(filename, content_type, bytes)`` if ``actor`` may view the parent submission."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise NotFoundError("Attachment not found.")
    require_view_submission(actor, attachment.submission)
    directory = get_settings().upload_dir.resolve()
    path = (directory / attachment.stored_filename).resolve()
    if path.parent != directory or not path.is_file():
        raise NotFoundError("The attachment file is no longer available.")
    content = path.read_bytes()
    audit_service.record(
        session,
        action=AuditAction.ATTACHMENT_DOWNLOADED,
        entity_type="attachment",
        entity_id=attachment.id,
        actor_id=actor.id,
        new_values={"submission_id": attachment.submission_id},
        ip_address=ip_address,
    )
    return attachment.original_filename, attachment.content_type, content
