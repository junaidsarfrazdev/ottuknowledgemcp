"""Compare indexed state vs current repo/site state."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import chromadb
import requests

from . import config


def _git_head(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return None


def _collection_count(client: chromadb.ClientAPI | None, name: str) -> int:
    if client is None:
        return 0
    try:
        return client.get_collection(name=name).count()
    except Exception:
        return 0


def _safe_client() -> chromadb.ClientAPI | None:
    try:
        return chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    except Exception:
        return None


def _status_from_shas(indexed: str | None, current: str | None) -> str:
    if indexed and current and indexed == current:
        return "fresh"
    if indexed and current:
        return "stale"
    if not indexed:
        return "not-indexed"
    return "unknown"


def repo_freshness() -> list[dict]:
    """Per-repo freshness rows.

    Cross-checks the on-disk `.index_metadata.json` against ChromaDB so a
    user who has chunks in Chroma but a deleted/missing metadata sidecar
    sees `indexed-no-metadata` (re-index will rebuild the sidecar) instead
    of a misleading `not-indexed`.
    """
    rows: list[dict] = []
    client = _safe_client()
    for repo in config.REPOS:
        root = Path(repo["path"])
        chunks = _collection_count(client, repo["collection_name"])
        if not root.exists():
            rows.append(
                {
                    "name": repo["name"],
                    "status": "missing",
                    "indexed_sha": None,
                    "current_sha": None,
                    "chunks": chunks,
                }
            )
            continue
        meta_path = root / config.METADATA_FILENAME
        if not meta_path.exists():
            rows.append(
                {
                    "name": repo["name"],
                    "status": "indexed-no-metadata" if chunks > 0 else "not-indexed",
                    "indexed_sha": None,
                    "current_sha": _git_head(root),
                    "chunks": chunks,
                }
            )
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            rows.append(
                {
                    "name": repo["name"],
                    "status": "corrupt-metadata",
                    "indexed_sha": None,
                    "current_sha": _git_head(root),
                    "chunks": chunks,
                }
            )
            continue
        indexed = meta.get("head_sha")
        current = _git_head(root)
        rows.append(
            {
                "name": repo["name"],
                "status": _status_from_shas(indexed, current),
                "indexed_sha": indexed,
                "current_sha": current,
                "indexed_at": meta.get("indexed_at"),
                "chunks": chunks,
            }
        )
    return rows


def docs_freshness() -> list[dict]:
    rows: list[dict] = []
    client = _safe_client()
    for site in config.DOCS_SITES:
        mode = site.get("mode")
        chunks = _collection_count(client, site.get("collection_name", ""))
        if mode == "docusaurus_repo":
            path = Path(site.get("path", ""))
            if not path.exists():
                rows.append(
                    {"name": site["name"], "mode": mode, "status": "missing", "chunks": chunks}
                )
                continue
            meta_path = path / config.DOCS_METADATA_FILENAME
            indexed_sha = None
            indexed_at = None
            if meta_path.exists():
                try:
                    m = json.loads(meta_path.read_text())
                    indexed_sha = m.get("head_sha")
                    indexed_at = m.get("indexed_at")
                except json.JSONDecodeError:
                    pass
            current = _git_head(path)
            if indexed_sha:
                status = _status_from_shas(indexed_sha, current)
            elif chunks > 0:
                status = "indexed-no-metadata"
            else:
                status = "not-indexed"
            rows.append(
                {
                    "name": site["name"],
                    "mode": mode,
                    "status": status,
                    "path": str(path),
                    "indexed_sha": indexed_sha,
                    "current_sha": current,
                    "indexed_at": indexed_at,
                    "chunks": chunks,
                }
            )
        elif mode == "crawl":
            url = site.get("url", "")
            try:
                r = requests.head(url, timeout=10, allow_redirects=True)
                last_modified = r.headers.get("Last-Modified", "")
            except requests.RequestException as e:
                last_modified = f"error: {e}"
            rows.append(
                {
                    "name": site["name"],
                    "mode": mode,
                    "status": "unknown",
                    "url": url,
                    "last_modified": last_modified,
                    "chunks": chunks,
                }
            )
    return rows
