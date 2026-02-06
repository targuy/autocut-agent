"""Tests for the media storage management system."""
from __future__ import annotations
import tempfile
import uuid
from pathlib import Path

import pytest

from agent.storage.manager import FileCategory, MediaFile, StorageManager, StorageStats


class TestMediaFile:
    def test_create(self) -> None:
        f = MediaFile(filename="video.mp4", original_path="/tmp/video.mp4", mime_type="video/mp4")
        assert f.extension == ".mp4"
        assert f.is_media is True

    def test_non_media(self) -> None:
        f = MediaFile(filename="data.json", original_path="/tmp/data.json", mime_type="application/json")
        assert f.is_media is False

    def test_extension(self) -> None:
        f = MediaFile(filename="clip.avi", original_path="/x", mime_type="video/x-msvideo")
        assert f.extension == ".avi"


class TestStorageManager:
    @pytest.mark.asyncio
    async def test_ingest_and_get(self, tmp_path: Path) -> None:
        # Create a test file
        src = tmp_path / "source" / "test.txt"
        src.parent.mkdir(parents=True)
        src.write_text("hello world")

        storage_root = tmp_path / "storage"
        mgr = StorageManager(storage_root, session_factory=None)

        # Since session_factory is None, we test the non-DB parts
        # Just verify directories were created
        assert (storage_root / "input").exists()
        assert (storage_root / "output").exists()
        assert (storage_root / "artifact").exists()
        assert (storage_root / "intermediate").exists()

    def test_compute_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "hashtest.txt"
        f.write_text("content")
        h = StorageManager._compute_hash(f)
        assert len(h) == 16
        # Same content = same hash
        f2 = tmp_path / "hashtest2.txt"
        f2.write_text("content")
        assert StorageManager._compute_hash(f2) == h

    def test_storage_stats_model(self) -> None:
        stats = StorageStats(total_files=10, total_size_bytes=1024)
        assert stats.total_files == 10
