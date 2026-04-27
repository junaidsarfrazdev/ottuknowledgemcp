"""Shared search helpers used by the MCP server and CLI.

Fan-out across multiple Chroma collections is parallelized via a thread pool
(SQLite + HNSW reads are safe concurrently).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import chromadb

Hit = tuple[str, dict, str, float]  # (id, meta, doc, distance)


def _query_one(
    coll: chromadb.Collection,
    vec: list[float],
    limit: int,
) -> list[Hit]:
    try:
        res = coll.query(
            query_embeddings=[vec],
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        return []
    ids = (res.get("ids") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    return [
        (rid, m or {}, d or "", float(dist))
        for rid, d, m, dist in zip(ids, docs, metas, dists)
    ]


def query_collections(
    collections: list[chromadb.Collection],
    vec: list[float],
    limit: int,
    max_workers: int = 4,
) -> list[Hit]:
    """Run a query vector against N collections in parallel; return top-limit by distance."""
    if not collections:
        return []
    if len(collections) == 1:
        hits = _query_one(collections[0], vec, limit)
    else:
        workers = min(len(collections), max_workers)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda c: _query_one(c, vec, limit), collections))
        hits = [h for sub in results for h in sub]
    hits.sort(key=lambda h: h[3])
    return hits[:limit]


def format_code_hit(
    meta: dict,
    doc: str,
    max_chars: int = 600,
    score: float | None = None,
) -> str:
    """Compact code-hit format: `repo/path#N [section] d=0.XX` + fenced snippet."""
    repo = meta.get("repo", "?")
    path = meta.get("path", "?")
    chunk = meta.get("chunk_index", "?")
    section = meta.get("sfc_section")
    header = f"**{repo}/{path}**#{chunk}"
    if section and section != "root":
        header += f" {section}"
    if score is not None:
        header += f" d={score:.2f}"
    snippet = doc.strip()
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "\n...[truncated]"
    lang = meta.get("language", "")
    return f"{header}\n```{lang}\n{snippet}\n```"


def format_docs_hit(
    meta: dict,
    doc: str,
    max_chars: int = 600,
    score: float | None = None,
) -> str:
    title = meta.get("title", "")
    url = meta.get("url", "")
    path = meta.get("path", meta.get("filename", ""))
    header = f"**{title or path}**"
    if url:
        header += f" <{url}>"
    elif path:
        header += f" `{path}`"
    if score is not None:
        header += f" d={score:.2f}"
    snippet = doc.strip()
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars] + "\n...[truncated]"
    return f"{header}\n{snippet}"


def dedupe_queries(queries: list[str]) -> list[str]:
    """Preserve order, drop empty/duplicate queries."""
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        q = (q or "").strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def resolve_collection_name(
    client: chromadb.ClientAPI,
    repo: str,
    repos_cfg: list[dict[str, Any]],
    docs_cfg: list[dict[str, Any]],
    internal_coll: str,
) -> chromadb.Collection | None:
    """Map a user-supplied repo identifier to a Chroma collection.

    Accepts a repo name, a docs-site name, 'docs' (first docs site), or 'internal'.
    """
    repo_norm = repo.strip().lower()
    for r in repos_cfg:
        if r["name"].lower() == repo_norm:
            try:
                return client.get_collection(name=r["collection_name"])
            except Exception:
                return None
    for s in docs_cfg:
        if s["name"].lower() == repo_norm:
            try:
                return client.get_collection(name=s["collection_name"])
            except Exception:
                return None
    if repo_norm == "docs" and docs_cfg:
        try:
            return client.get_collection(name=docs_cfg[0]["collection_name"])
        except Exception:
            return None
    if repo_norm in ("internal", "internal_docs"):
        try:
            return client.get_collection(name=internal_coll)
        except Exception:
            return None
    return None
