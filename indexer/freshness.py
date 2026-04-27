"""Compare indexed state vs current repo/site state."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

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


def _status_from_shas(indexed: str | None, current: str | None) -> str:
    if indexed and current and indexed == current:
        return "fresh"
    if indexed and current:
        return "stale"
    if not indexed:
        return "not-indexed"
    return "unknown"


def repo_freshness() -> list[dict]:
    rows: list[dict] = []
    for repo in config.REPOS:
        root = Path(repo["path"])
        if not root.exists():
            rows.append({"name": repo["name"], "status": "missing", "indexed_sha": None, "current_sha": None})
            continue
        meta_path = root / config.METADATA_FILENAME
        if not meta_path.exists():
            rows.append({"name": repo["name"], "status": "not-indexed", "indexed_sha": None, "current_sha": _git_head(root)})
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            rows.append({"name": repo["name"], "status": "corrupt-metadata", "indexed_sha": None, "current_sha": _git_head(root)})
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
            }
        )
    return rows


def docs_freshness() -> list[dict]:
    rows: list[dict] = []
    for site in config.DOCS_SITES:
        mode = site.get("mode")
        if mode == "docusaurus_repo":
            path = Path(site.get("path", ""))
            if not path.exists():
                rows.append({"name": site["name"], "mode": mode, "status": "missing"})
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
            rows.append(
                {
                    "name": site["name"],
                    "mode": mode,
                    "status": _status_from_shas(indexed_sha, current),
                    "path": str(path),
                    "indexed_sha": indexed_sha,
                    "current_sha": current,
                    "indexed_at": indexed_at,
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
                {"name": site["name"], "mode": mode, "status": "unknown", "url": url, "last_modified": last_modified}
            )
    return rows
