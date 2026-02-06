"""Media and file storage management for pipeline inputs/outputs."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from agent.core.database import MediaFileRow

logger = structlog.get_logger()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


class FileCategory(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    ARTIFACT = "artifact"
    INTERMEDIATE = "intermediate"


class MediaFile(BaseModel):
    """Metadata for a tracked file."""
    id: uuid.UUID = Field(default_factory=_new_id)
    filename: str
    original_path: str
    storage_path: str = ""
    file_size: int = 0
    mime_type: str = ""
    file_hash: str = ""
    category: FileCategory = FileCategory.ARTIFACT
    pipeline_id: uuid.UUID | None = None
    step_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def extension(self) -> str:
        return Path(self.filename).suffix.lower()

    @property
    def is_media(self) -> bool:
        return self.mime_type.startswith(("video/", "audio/", "image/"))


class StorageStats(BaseModel):
    total_files: int = 0
    total_size_bytes: int = 0
    by_category: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)


class StorageManager:
    """Manages pipeline file storage with metadata tracking."""

    def __init__(self, storage_root: str | Path, session_factory: Any) -> None:
        self._root = Path(storage_root)
        self._session_factory = session_factory
        self._root.mkdir(parents=True, exist_ok=True)
        # Create category subdirectories
        for cat in FileCategory:
            (self._root / cat.value).mkdir(exist_ok=True)

    @property
    def storage_root(self) -> Path:
        return self._root

    async def ingest(
        self,
        source_path: str | Path,
        *,
        category: FileCategory = FileCategory.INPUT,
        pipeline_id: uuid.UUID | None = None,
        step_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        copy: bool = True,
    ) -> MediaFile:
        """Ingest a file into managed storage.

        If copy=True, copies the file to storage. Otherwise just tracks it.
        """
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Source file not found: {source}")

        file_id = _new_id()
        filename = source.name
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        file_size = source.stat().st_size
        file_hash = self._compute_hash(source)

        if copy:
            dest_dir = self._root / category.value
            dest_path = dest_dir / f"{file_id}_{filename}"
            shutil.copy2(str(source), str(dest_path))
            storage_path = str(dest_path)
        else:
            storage_path = str(source.resolve())

        entry = MediaFile(
            id=file_id,
            filename=filename,
            original_path=str(source.resolve()),
            storage_path=storage_path,
            file_size=file_size,
            mime_type=mime_type,
            file_hash=file_hash,
            category=category,
            pipeline_id=pipeline_id,
            step_id=step_id,
            tags=tags or [],
            metadata=metadata or {},
        )

        await self._save(entry)
        logger.info("storage.ingested", file_id=str(file_id), filename=filename, category=category.value)
        return entry

    async def register_external(
        self,
        path: str,
        *,
        category: FileCategory = FileCategory.INPUT,
        pipeline_id: uuid.UUID | None = None,
        step_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MediaFile:
        """Register an external file without copying it into managed storage."""
        return await self.ingest(
            path, category=category, pipeline_id=pipeline_id,
            step_id=step_id, tags=tags, metadata=metadata, copy=False,
        )

    async def get(self, file_id: uuid.UUID) -> MediaFile | None:
        """Get file metadata by ID."""
        async with self._session_factory() as session:
            row = await session.get(MediaFileRow, str(file_id))
            if row is None:
                return None
            return self._row_to_entry(row)

    async def search(
        self,
        *,
        query: str | None = None,
        category: FileCategory | None = None,
        pipeline_id: uuid.UUID | None = None,
        mime_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MediaFile]:
        """Search files by various criteria."""
        async with self._session_factory() as session:
            stmt = select(MediaFileRow).order_by(MediaFileRow.created_at.desc())

            if category is not None:
                stmt = stmt.where(MediaFileRow.category == category.value)
            if pipeline_id is not None:
                stmt = stmt.where(MediaFileRow.pipeline_id == str(pipeline_id))
            if mime_type is not None:
                stmt = stmt.where(MediaFileRow.mime_type.like(f"{mime_type}%"))
            if query:
                pattern = f"%{query}%"
                stmt = stmt.where(
                    or_(
                        MediaFileRow.filename.like(pattern),
                        MediaFileRow.original_path.like(pattern),
                    )
                )

            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()

            entries = [self._row_to_entry(r) for r in rows]

            # Filter by tags in Python (JSON column)
            if tags:
                entries = [
                    e for e in entries
                    if any(t in e.tags for t in tags)
                ]

            return entries

    async def list_all(
        self, limit: int = 100, offset: int = 0
    ) -> list[MediaFile]:
        """List all tracked files."""
        return await self.search(limit=limit, offset=offset)

    async def delete_file(self, file_id: uuid.UUID) -> bool:
        """Delete a file from storage and DB."""
        entry = await self.get(file_id)
        if entry is None:
            return False

        # Remove physical file if it's in managed storage
        storage_path = Path(entry.storage_path)
        if storage_path.exists() and str(self._root) in str(storage_path):
            storage_path.unlink(missing_ok=True)

        async with self._session_factory() as session:
            row = await session.get(MediaFileRow, str(file_id))
            if row:
                await session.delete(row)
                await session.commit()

        logger.info("storage.deleted", file_id=str(file_id), filename=entry.filename)
        return True

    async def export_file(self, file_id: uuid.UUID, dest_path: str | Path) -> str:
        """Copy a managed file to a destination path."""
        entry = await self.get(file_id)
        if entry is None:
            raise FileNotFoundError(f"File not found: {file_id}")

        source = Path(entry.storage_path)
        if not source.exists():
            raise FileNotFoundError(f"Storage file missing: {source}")

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(dest))
        return str(dest)

    async def get_stats(self) -> StorageStats:
        """Compute storage statistics."""
        files = await self.search(limit=10000)
        stats = StorageStats()
        stats.total_files = len(files)
        for f in files:
            stats.total_size_bytes += f.file_size
            cat = f.category.value
            stats.by_category[cat] = stats.by_category.get(cat, 0) + 1
            ext = f.extension or "unknown"
            stats.by_type[ext] = stats.by_type.get(ext, 0) + 1
        return stats

    async def get_pipeline_files(self, pipeline_id: uuid.UUID) -> list[MediaFile]:
        """Get all files associated with a pipeline."""
        return await self.search(pipeline_id=pipeline_id, limit=10000)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _save(self, entry: MediaFile) -> None:
        async with self._session_factory() as session:
            row = MediaFileRow(
                id=str(entry.id),
                filename=entry.filename,
                original_path=entry.original_path,
                storage_path=entry.storage_path,
                file_size=entry.file_size,
                mime_type=entry.mime_type,
                file_hash=entry.file_hash,
                category=entry.category.value,
                pipeline_id=str(entry.pipeline_id) if entry.pipeline_id else None,
                step_id=str(entry.step_id) if entry.step_id else None,
                tags=entry.tags,
                file_metadata=entry.metadata,
                created_at=entry.created_at,
            )
            session.add(row)
            await session.commit()

    @staticmethod
    def _row_to_entry(row: MediaFileRow) -> MediaFile:
        return MediaFile(
            id=uuid.UUID(row.id),
            filename=row.filename or "",
            original_path=row.original_path or "",
            storage_path=row.storage_path or "",
            file_size=row.file_size or 0,
            mime_type=row.mime_type or "",
            file_hash=row.file_hash or "",
            category=FileCategory(row.category) if row.category else FileCategory.ARTIFACT,
            pipeline_id=uuid.UUID(row.pipeline_id) if row.pipeline_id else None,
            step_id=uuid.UUID(row.step_id) if row.step_id else None,
            tags=row.tags or [],
            metadata=row.file_metadata or {},
            created_at=row.created_at,
        )

    @staticmethod
    def _compute_hash(path: Path, chunk_size: int = 8192) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(chunk_size):
                h.update(chunk)
        return h.hexdigest()[:16]
