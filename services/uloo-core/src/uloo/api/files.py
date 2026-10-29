# AI生成
"""File attachment and upload endpoints for document processing."""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import FileResponse

from ..errors import ApiError
from ..schemas.file import FileUploadResponse
from ..security import RequestContext, require_service_context

router = APIRouter(prefix="/files", tags=["files"])

_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
_ALLOWED_EXTENSIONS = {".pdf", ".txt", ".csv", ".md", ".json", ".doc", ".docx", ".xlsx"}


def _get_storage_uploads_dir() -> Path:
    """Resolve uploads directory to repo-level storage or local storage folder."""
    cwd = Path.cwd()
    if (cwd.parent / "storage").exists():
        d = (cwd.parent / "storage" / "uploads").resolve()
    else:
        d = (cwd / "storage" / "uploads").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    context: RequestContext = Depends(require_service_context),
) -> FileUploadResponse:
    """Upload a document attachment (PDF, TXT, CSV, etc.) for agent analysis."""
    original_name = file.filename or "uploaded_document"
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="UNSUPPORTED_FILE_TYPE",
            message=f"File extension '{ext}' is not supported. Supported extensions: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

    # Sanitize filename
    clean_stem = re.sub(r"[^\w\.-]", "_", Path(original_name).stem)[:64]
    unique_suffix = uuid.uuid4().hex[:8]
    timestamp = int(time.time())
    dest_filename = f"{timestamp}_{unique_suffix}_{clean_stem}{ext}"

    dest_dir = _get_storage_uploads_dir()
    dest_path = dest_dir / dest_filename

    total_bytes = 0
    try:
        with dest_path.open("wb") as out_f:
            while chunk := await file.read(64 * 1024):
                total_bytes += len(chunk)
                if total_bytes > _MAX_FILE_SIZE_BYTES:
                    out_f.close()
                    dest_path.unlink(missing_ok=True)
                    raise ApiError(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        code="FILE_TOO_LARGE",
                        message=f"File size exceeds maximum allowed limit of {_MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB",
                    )
                out_f.write(chunk)
    except ApiError:
        raise
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        raise ApiError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="FILE_SAVE_FAILED",
            message=f"Failed to store uploaded file: {exc}",
        ) from exc

    # Normalize relative path using forward slashes
    rel_path = f"storage/uploads/{dest_filename}"

    return FileUploadResponse(
        status="ok",
        file_name=original_name,
        file_path=rel_path,
        file_size=total_bytes,
        content_type=file.content_type or "application/octet-stream",
        created_at=datetime.now(UTC).isoformat(),
    )


@router.get("/{file_name:path}")
async def get_file(
    file_name: str,
    context: RequestContext = Depends(require_service_context),
) -> FileResponse:
    """Download or view an uploaded attachment."""
    safe_name = Path(file_name).name
    target_path = _get_storage_uploads_dir() / safe_name
    if not target_path.is_file():
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="FILE_NOT_FOUND",
            message=f"Requested file '{safe_name}' was not found in storage",
        )
    return FileResponse(str(target_path), filename=safe_name)
