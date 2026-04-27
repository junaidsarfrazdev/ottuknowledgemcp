"""Freshness reporting: status transitions and chunk-count fallback."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from indexer import config, freshness


@pytest.fixture
def fake_chroma_counts():
    """Replace freshness._safe_client with a stub that returns fixed counts per collection."""

    class FakeColl:
        def __init__(self, count):
            self._count = count

        def count(self):
            return self._count

    class FakeClient:
        def __init__(self, counts):
            self._counts = counts

        def get_collection(self, name):
            if name in self._counts:
                return FakeColl(self._counts[name])
            raise RuntimeError(f"no collection {name}")

    def _set(counts):
        return patch.object(freshness, "_safe_client", lambda: FakeClient(counts))

    return _set


def test_repo_freshness_indexed_no_metadata_when_chunks_exist(tmp_path, monkeypatch, fake_chroma_counts):
    """No sidecar but Chroma has chunks → indexed-no-metadata, not not-indexed."""
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()  # so the path looks like a real repo

    repos = [
        {
            "name": "myrepo",
            "path": str(repo_dir),
            "description": "x",
            "collection_name": "ottu_myrepo",
            "priority": 1,
        }
    ]
    monkeypatch.setattr(config, "REPOS", repos)
    with fake_chroma_counts({"ottu_myrepo": 42}):
        with patch.object(freshness, "_git_head", lambda p: "deadbeef"):
            rows = freshness.repo_freshness()
    assert len(rows) == 1
    assert rows[0]["status"] == "indexed-no-metadata"
    assert rows[0]["chunks"] == 42


def test_repo_freshness_not_indexed_when_no_chunks(tmp_path, monkeypatch, fake_chroma_counts):
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()

    repos = [
        {
            "name": "myrepo",
            "path": str(repo_dir),
            "description": "x",
            "collection_name": "ottu_myrepo",
            "priority": 1,
        }
    ]
    monkeypatch.setattr(config, "REPOS", repos)
    with fake_chroma_counts({"ottu_myrepo": 0}):
        with patch.object(freshness, "_git_head", lambda p: "deadbeef"):
            rows = freshness.repo_freshness()
    assert rows[0]["status"] == "not-indexed"
    assert rows[0]["chunks"] == 0


def test_repo_freshness_fresh_when_metadata_matches(tmp_path, monkeypatch, fake_chroma_counts):
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    (repo_dir / config.METADATA_FILENAME).write_text(
        json.dumps({"head_sha": "deadbeef", "indexed_at": "now", "files": {}})
    )

    repos = [
        {
            "name": "myrepo",
            "path": str(repo_dir),
            "description": "x",
            "collection_name": "ottu_myrepo",
            "priority": 1,
        }
    ]
    monkeypatch.setattr(config, "REPOS", repos)
    with fake_chroma_counts({"ottu_myrepo": 100}):
        with patch.object(freshness, "_git_head", lambda p: "deadbeef"):
            rows = freshness.repo_freshness()
    assert rows[0]["status"] == "fresh"
    assert rows[0]["chunks"] == 100


def test_repo_freshness_stale_when_metadata_mismatches(tmp_path, monkeypatch, fake_chroma_counts):
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    (repo_dir / config.METADATA_FILENAME).write_text(
        json.dumps({"head_sha": "old", "indexed_at": "earlier", "files": {}})
    )

    repos = [
        {
            "name": "myrepo",
            "path": str(repo_dir),
            "description": "x",
            "collection_name": "ottu_myrepo",
            "priority": 1,
        }
    ]
    monkeypatch.setattr(config, "REPOS", repos)
    with fake_chroma_counts({"ottu_myrepo": 5}):
        with patch.object(freshness, "_git_head", lambda p: "new"):
            rows = freshness.repo_freshness()
    assert rows[0]["status"] == "stale"
    assert rows[0]["indexed_sha"] == "old"
    assert rows[0]["current_sha"] == "new"


def test_repo_freshness_corrupt_metadata(tmp_path, monkeypatch, fake_chroma_counts):
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    (repo_dir / config.METADATA_FILENAME).write_text("{not json")

    repos = [
        {
            "name": "myrepo",
            "path": str(repo_dir),
            "description": "x",
            "collection_name": "ottu_myrepo",
            "priority": 1,
        }
    ]
    monkeypatch.setattr(config, "REPOS", repos)
    with fake_chroma_counts({"ottu_myrepo": 7}):
        with patch.object(freshness, "_git_head", lambda p: "x"):
            rows = freshness.repo_freshness()
    assert rows[0]["status"] == "corrupt-metadata"
    assert rows[0]["chunks"] == 7


def test_repo_freshness_missing_path(tmp_path, monkeypatch, fake_chroma_counts):
    repos = [
        {
            "name": "myrepo",
            "path": str(tmp_path / "doesnotexist"),
            "description": "x",
            "collection_name": "ottu_myrepo",
            "priority": 1,
        }
    ]
    monkeypatch.setattr(config, "REPOS", repos)
    with fake_chroma_counts({"ottu_myrepo": 0}):
        rows = freshness.repo_freshness()
    assert rows[0]["status"] == "missing"
