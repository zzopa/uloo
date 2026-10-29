# AI生成
"""Schemas for file attachment and upload operations."""

from pydantic import BaseModel, Field


class FileUploadResponse(BaseModel):
    """Metadata response for an uploaded file."""

    status: str = "ok"
    file_name: str = Field(description="Original filename uploaded by the user")
    file_path: str = Field(description="Relative storage path used by tools and runtime")
    file_size: int = Field(description="File size in bytes")
    content_type: str = Field(default="application/octet-stream", description="MIME content type")
    created_at: str = Field(description="ISO-8601 creation timestamp")
