# AI生成
"""Unit tests for file upload and attachment management endpoints."""

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_upload_and_get_file(client):
    file_content = b"%PDF-1.4 sample pdf content for upload test"
    files = {"file": ("test_doc.pdf", file_content, "application/pdf")}

    resp = await client.post("/api/v1/files/upload", files=files)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "ok"
    assert data["file_name"] == "test_doc.pdf"
    assert data["file_path"].startswith("storage/uploads/")
    assert data["file_size"] == len(file_content)

    uploaded_filename = Path(data["file_path"]).name

    # Download / retrieve the file
    get_resp = await client.get(f"/api/v1/files/{uploaded_filename}")
    assert get_resp.status_code == 200
    assert get_resp.content == file_content

    # Clean up test file from storage
    Path(data["file_path"]).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_upload_unsupported_extension(client):
    files = {"file": ("malicious.exe", b"MZ...", "application/octet-stream")}
    resp = await client.post("/api/v1/files/upload", files=files)
    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "UNSUPPORTED_FILE_TYPE"


@pytest.mark.asyncio
async def test_get_nonexistent_file(client):
    resp = await client.get("/api/v1/files/non_existent_file_999.pdf")
    assert resp.status_code == 404
    data = resp.json()
    assert data["code"] == "FILE_NOT_FOUND"
