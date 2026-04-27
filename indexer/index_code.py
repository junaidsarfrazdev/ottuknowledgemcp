"""Code repository indexer.

Walk repo → split (Vue SFC aware, language splitters otherwise) → batch-embed →
write ChromaDB. Writes per-repo .index_metadata.json for incremental re-indexing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import chromadb
from langchain_text_splitters import (
    Language,
    RecursiveCharacterTextSplitter,
)
from rich.console import Console
from rich.progress import Progress, TaskID

from . import config
from .embeddings import OllamaEmbeddings

console = Console()

# Map file extension to a langchain Language for language-aware splitting.
_LANG_FOR_EXT: dict[str, Language] = {
    ".js": Language.JS,
    ".jsx": Language.JS,
    ".ts": Language.TS,
    ".tsx": Language.TS,
    ".html": Language.HTML,
    ".md": Language.MARKDOWN,
    ".mdx": Language.MARKDOWN,
}
_DEFAULT_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP
)


def _splitter_for(ext: str) -> RecursiveCharacterTextSplitter:
    lang = _LANG_FOR_EXT.get(ext)
    if lang is None:
        return _DEFAULT_SPLITTER
    try:
        return RecursiveCharacterTextSplitter.from_language(
            language=lang,
            chunk_size=config.CHUNK_SIZE,
            chunk_overlap=config.CHUNK_OVERLAP,
        )
    except Exception:
        return _DEFAULT_SPLITTER


_VUE_BLOCK_RE = re.compile(
    r"<(template|script|style)([^>]*)>(.*?)</\1>",
    re.DOTALL | re.IGNORECASE,
)


def _split_vue(text: str) -> list[tuple[str, str]]:
    """Return list of (section, content) for a Vue SFC.

    Sections: 'template' -> HTML splitter, 'script' -> JS/TS splitter, 'style' -> default.
    Any leftover text outside blocks is attached as 'root'.
    """
    blocks: list[tuple[str, str]] = []
    consumed = 0
    for m in _VUE_BLOCK_RE.finditer(text):
        if m.start() > consumed:
            leftover = text[consumed : m.start()].strip()
            if leftover:
                blocks.append(("root", leftover))
        section = m.group(1).lower()
        attrs = m.group(2) or ""
        body = m.group(3)
        if section == "script":
            # lang="ts" → use TS splitter; else JS
            blocks.append(("script_ts" if 'lang="ts"' in attrs or "lang='ts'" in attrs else "script", body))
        elif section == "template":
            blocks.append(("template", body))
        elif section == "style":
            blocks.append(("style", body))
        consumed = m.end()
    tail = text[consumed:].strip()
    if tail:
        blocks.append(("root", tail))
    return blocks or [("root", text)]


def _splitter_for_vue_section(section: str) -> RecursiveCharacterTextSplitter:
    if section == "template":
        return _splitter_for(".html")
    if section == "script_ts":
        return _splitter_for(".ts")
    if section == "script":
        return _splitter_for(".js")
    return _DEFAULT_SPLITTER


def _walk_files(repo_root: Path) -> Iterable[Path]:
    """Walk the repo with in-place directory pruning.

    Pruning happens before descent, so excluded trees (node_modules, venv, …)
    aren't enumerated at all — much faster than rglob on big repos.
    """
    root_str = str(repo_root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        dirnames[:] = [d for d in dirnames if d not in config.EXCLUDE_DIRS]
        for name in filenames:
            if name in config.EXCLUDE_FILENAMES:
                continue
            if any(name.endswith(suf) for suf in config.EXCLUDE_SUFFIXES):
                continue
            ext = Path(name).suffix.lower()
            if ext not in config.INCLUDE_EXTENSIONS:
                continue
            p = Path(dirpath) / name
            try:
                if p.stat().st_size > config.MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield p


def _file_sha(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_head(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _metadata_path(repo_root: Path) -> Path:
    return repo_root / config.METADATA_FILENAME


def _load_metadata(repo_root: Path) -> dict:
    p = _metadata_path(repo_root)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_metadata(repo_root: Path, data: dict) -> None:
    _metadata_path(repo_root).write_text(json.dumps(data, indent=2))


def _chunk_file(path: Path, rel_path: str) -> list[tuple[str, dict]]:
    ext = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.strip():
        return []

    chunks: list[tuple[str, dict]] = []
    if ext == ".vue":
        for section, body in _split_vue(text):
            splitter = _splitter_for_vue_section(section)
            for section_idx, piece in enumerate(splitter.split_text(body)):
                if piece.strip():
                    chunks.append(
                        (
                            piece,
                            {
                                "sfc_section": section,
                                "section_index": section_idx,
                                "language": ext.lstrip("."),
                            },
                        )
                    )
    else:
        splitter = _splitter_for(ext)
        for piece in splitter.split_text(text):
            if piece.strip():
                chunks.append((piece, {"language": ext.lstrip(".")}))
    # Assign globally-unique chunk_index (matches the chunk ID scheme)
    for global_idx, (_, meta) in enumerate(chunks):
        meta["chunk_index"] = global_idx
        meta["path"] = rel_path
    return chunks


def index_repo(
    repo: config.CodeRepo,
    client: chromadb.ClientAPI,
    embeddings: OllamaEmbeddings,
    force: bool = False,
) -> dict:
    root = Path(repo["path"])
    head_sha = _git_head(root) or "unknown"
    metadata = _load_metadata(root) if not force else {}
    known_files: dict[str, dict] = metadata.get("files", {}) if not force else {}

    collection = client.get_or_create_collection(
        name=repo["collection_name"], metadata={"ottu_repo": repo["name"]}
    )

    files = list(_walk_files(root))
    current_paths: set[str] = set()

    ids_to_add: list[str] = []
    docs_to_add: list[str] = []
    metas_to_add: list[dict] = []

    changed = 0
    skipped = 0
    removed = 0

    with Progress(console=console) as progress:
        task: TaskID = progress.add_task(
            f"Indexing {repo['name']}", total=len(files)
        )
        for path in files:
            rel = path.relative_to(root).as_posix()
            current_paths.add(rel)
            sha = _file_sha(path)
            prev = known_files.get(rel)
            if prev and prev.get("sha") == sha:
                skipped += 1
                progress.advance(task)
                continue

            # File is new or changed — remove prior chunks if any
            if prev:
                try:
                    collection.delete(where={"path": rel})
                except Exception:
                    pass

            chunks = _chunk_file(path, rel)
            now_iso = datetime.now(timezone.utc).isoformat()
            for piece, meta in chunks:
                chunk_id = f"{repo['name']}:{rel}:{meta['chunk_index']}"
                meta.update(
                    {
                        "repo": repo["name"],
                        "commit_sha": head_sha,
                        "file_sha": sha,
                        "ingested_at": now_iso,
                    }
                )
                # Chroma metadata values must be str/int/float/bool
                meta = {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in meta.items()}
                ids_to_add.append(chunk_id)
                docs_to_add.append(piece)
                metas_to_add.append(meta)

            known_files[rel] = {"sha": sha, "chunks": len(chunks)}
            changed += 1
            progress.advance(task)

    # Remove chunks for files that no longer exist
    stale_paths = set(known_files.keys()) - current_paths
    for rel in stale_paths:
        try:
            collection.delete(where={"path": rel})
        except Exception:
            pass
        known_files.pop(rel, None)
        removed += 1

    # Add new/changed chunks in batches
    if ids_to_add:
        B = config.EMBED_BATCH_SIZE
        for i in range(0, len(ids_to_add), B):
            batch_docs = docs_to_add[i : i + B]
            vectors = embeddings.embed_documents(batch_docs, batch_size=B)
            collection.add(
                ids=ids_to_add[i : i + B],
                documents=batch_docs,
                metadatas=metas_to_add[i : i + B],
                embeddings=vectors,
            )

    metadata = {
        "head_sha": head_sha,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "files": known_files,
    }
    _save_metadata(root, metadata)

    console.print(
        f"[green]\u2713[/green] {repo['name']}: "
        f"{changed} changed, {skipped} unchanged, {removed} removed, "
        f"{len(ids_to_add)} chunks written"
    )
    return {
        "repo": repo["name"],
        "chunks_written": len(ids_to_add),
        "files_changed": changed,
        "files_skipped": skipped,
        "files_removed": removed,
    }


def index_all(
    client: chromadb.ClientAPI,
    embeddings: OllamaEmbeddings,
    only: str | None = None,
    force: bool = False,
) -> list[dict]:
    results = []
    for repo in config.REPOS:
        if only and repo["name"] != only:
            continue
        results.append(index_repo(repo, client, embeddings, force=force))
    return results
