"""Storage management API endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.storage.manager import FileCategory, StorageManager

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    path: str
    category: str = "input"
    pipeline_id: str | None = None
    step_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExportRequest(BaseModel):
    dest_path: str


class MediaFileResponse(BaseModel):
    id: str
    filename: str
    original_path: str
    storage_path: str
    file_size: int
    mime_type: str
    file_hash: str
    category: str
    pipeline_id: str | None
    step_id: str | None
    tags: list[str]
    metadata: dict[str, Any]
    created_at: str
    extension: str
    is_media: bool


class StorageStatsResponse(BaseModel):
    total_files: int
    total_size_bytes: int
    by_category: dict[str, int]
    by_type: dict[str, int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_storage(request: Request) -> StorageManager:
    storage = getattr(request.app.state, "storage_manager", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Storage manager not initialized")
    return storage


def _entry_to_response(entry: Any) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "filename": entry.filename,
        "original_path": entry.original_path,
        "storage_path": entry.storage_path,
        "file_size": entry.file_size,
        "mime_type": entry.mime_type,
        "file_hash": entry.file_hash,
        "category": entry.category.value,
        "pipeline_id": str(entry.pipeline_id) if entry.pipeline_id else None,
        "step_id": str(entry.step_id) if entry.step_id else None,
        "tags": entry.tags,
        "metadata": entry.metadata,
        "created_at": entry.created_at.isoformat(),
        "extension": entry.extension,
        "is_media": entry.is_media,
    }


def _parse_category(value: str) -> FileCategory:
    try:
        return FileCategory(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category: {value}. Must be one of: {[c.value for c in FileCategory]}",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/storage/ingest", response_model=MediaFileResponse)
async def ingest_file(body: IngestRequest, request: Request) -> dict[str, Any]:
    """Ingest a file into managed storage."""
    storage = _get_storage(request)
    category = _parse_category(body.category)

    pipeline_id = uuid.UUID(body.pipeline_id) if body.pipeline_id else None
    step_id = uuid.UUID(body.step_id) if body.step_id else None

    try:
        entry = await storage.ingest(
            body.path,
            category=category,
            pipeline_id=pipeline_id,
            step_id=step_id,
            tags=body.tags,
            metadata=body.metadata,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return _entry_to_response(entry)


@router.get("/storage/files", response_model=list[MediaFileResponse])
async def list_files(
    request: Request,
    query: str | None = None,
    category: str | None = None,
    pipeline_id: str | None = None,
    mime_type: str | None = None,
    tag: list[str] = Query(default=[]),
    limit: int = Query(default=100, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List and search tracked files."""
    storage = _get_storage(request)

    cat = _parse_category(category) if category else None
    pid = uuid.UUID(pipeline_id) if pipeline_id else None

    files = await storage.search(
        query=query,
        category=cat,
        pipeline_id=pid,
        mime_type=mime_type,
        tags=tag if tag else None,
        limit=limit,
        offset=offset,
    )
    return [_entry_to_response(f) for f in files]


@router.get("/storage/files/{file_id}", response_model=MediaFileResponse)
async def get_file(file_id: str, request: Request) -> dict[str, Any]:
    """Get file metadata by ID."""
    storage = _get_storage(request)
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    entry = await storage.get(fid)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")
    return _entry_to_response(entry)


@router.get("/storage/files/{file_id}/download")
async def download_file(file_id: str, request: Request) -> FileResponse:
    """Download/stream file content."""
    storage = _get_storage(request)
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    entry = await storage.get(fid)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")

    from pathlib import Path

    path = Path(entry.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Physical file not found on disk")

    return FileResponse(
        path=str(path),
        filename=entry.filename,
        media_type=entry.mime_type or "application/octet-stream",
    )


@router.delete("/storage/files/{file_id}")
async def delete_file(file_id: str, request: Request) -> dict[str, str]:
    """Delete a tracked file from storage."""
    storage = _get_storage(request)
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    deleted = await storage.delete_file(fid)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")
    return {"status": "deleted", "file_id": file_id}


@router.post("/storage/files/{file_id}/export")
async def export_file(
    file_id: str, body: ExportRequest, request: Request
) -> dict[str, str]:
    """Export (copy) a managed file to a destination path."""
    storage = _get_storage(request)
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    try:
        dest = await storage.export_file(fid, body.dest_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"status": "exported", "file_id": file_id, "dest_path": dest}


@router.get("/storage/stats", response_model=StorageStatsResponse)
async def get_storage_stats(request: Request) -> dict[str, Any]:
    """Get storage statistics."""
    storage = _get_storage(request)
    stats = await storage.get_stats()
    return {
        "total_files": stats.total_files,
        "total_size_bytes": stats.total_size_bytes,
        "by_category": stats.by_category,
        "by_type": stats.by_type,
    }


@router.get(
    "/storage/pipelines/{pipeline_id}/files",
    response_model=list[MediaFileResponse],
)
async def get_pipeline_files(
    pipeline_id: str, request: Request
) -> list[dict[str, Any]]:
    """List all files associated with a pipeline."""
    storage = _get_storage(request)
    try:
        pid = uuid.UUID(pipeline_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pipeline ID format")

    files = await storage.get_pipeline_files(pid)
    return [_entry_to_response(f) for f in files]
