"""MCP server exposing Ottu knowledge-base search tools to Claude Code.

Tools:
  - search_multi: batch multiple queries in one call (preferred — saves turns/tokens).
  - search_ottu_code: single-query semantic search across indexed code repos.
  - search_ottu_docs: single-query semantic search across docs + internal docs.
  - get_file_chunks: return all indexed chunks for a known repo + path (no embedding).
  - find_file: path-substring lookup across collections.
  - list_ottu_sources: enumerate indexed repos / docs / internal docs with stats.
  - check_ottu_freshness: report which sources are stale vs current state.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import chromadb
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from indexer import config, freshness, search
from indexer.embeddings import OllamaEmbeddings

_server = Server("ottu-knowledge")
_embeddings: OllamaEmbeddings | None = None
_client: chromadb.ClientAPI | None = None

DEFAULT_SNIPPET_CHARS = 600
# L2 distance ceiling for search_multi. For nomic-embed-text on unit vectors,
# dist≈0.5 ≈ cosine_sim≈0.875. Raise to 0.7 if results feel too sparse.
DISTANCE_THRESHOLD = 0.5
# Cap total metadatas scanned across all collections in find_file. Chunks are
# fetched in pages of FIND_FILE_PAGE_SIZE so we never materialize the full set.
MAX_METADATAS_SCAN = 50_000
FIND_FILE_PAGE_SIZE = 2_000


def _get_embeddings() -> OllamaEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = OllamaEmbeddings()
    return _embeddings


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=config.CHROMA_DB_PATH)
    return _client


def _safe_collection(name: str) -> chromadb.Collection | None:
    try:
        return _get_client().get_collection(name=name)
    except Exception:
        return None


def _code_collections(repo_filter: str | None = None) -> list[chromadb.Collection]:
    out: list[chromadb.Collection] = []
    for r in config.REPOS:
        if repo_filter and r["name"] != repo_filter:
            continue
        c = _safe_collection(r["collection_name"])
        if c is not None:
            out.append(c)
    return out


def _docs_collections() -> list[chromadb.Collection]:
    out: list[chromadb.Collection] = []
    for s in config.DOCS_SITES:
        c = _safe_collection(s["collection_name"])
        if c is not None:
            out.append(c)
    internal = _safe_collection(config.INTERNAL_DOCS_COLLECTION)
    if internal is not None:
        out.append(internal)
    return out


@_server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_multi",
            description=(
                "Batch search — run multiple queries against Ottu code and/or docs in one call. "
                "Prefer this over calling search_ottu_code/search_ottu_docs repeatedly. "
                "All queries are embedded in one Ollama round-trip, results are deduplicated "
                "across queries, and low-relevance hits (dist > max_distance) are dropped."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more natural-language queries.",
                        "minItems": 1,
                        "maxItems": 8,
                    },
                    "sources": {
                        "type": "string",
                        "enum": ["code", "docs", "both"],
                        "default": "code",
                    },
                    "repo": {"type": "string", "description": "Restrict code search to one repo name."},
                    "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                    "snippet_chars": {
                        "type": "integer",
                        "default": 600,
                        "minimum": 100,
                        "maximum": 2000,
                    },
                    "max_distance": {
                        "type": "number",
                        "default": 0.5,
                        "description": "Hits above this distance are dropped. Raise to 0.7 if results are sparse.",
                    },
                },
                "required": ["queries"],
            },
        ),
        Tool(
            name="search_ottu_code",
            description=(
                "Single-query semantic search across indexed Ottu code repos. "
                "For multiple queries, prefer search_multi."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "repo": {"type": "string"},
                    "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 25},
                    "snippet_chars": {"type": "integer", "default": 600, "minimum": 100, "maximum": 2000},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="search_ottu_docs",
            description=(
                "Single-query semantic search across Ottu docs (Docusaurus) + internal docs. "
                "For multiple queries, prefer search_multi."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 25},
                    "snippet_chars": {"type": "integer", "default": 600, "minimum": 100, "maximum": 2000},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_file_chunks",
            description=(
                "Fetch all indexed chunks for a specific file — no semantic search, no embedding. "
                "Use when you already know the repo+path (e.g. from a prior search hit) and want "
                "more context from that file. Much cheaper than re-running search."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "Repo name, docs site name, 'docs', or 'internal'.",
                    },
                    "path": {"type": "string", "description": "Path relative to the repo/site root."},
                    "snippet_chars": {
                        "type": "integer",
                        "default": 1500,
                        "minimum": 100,
                        "maximum": 5000,
                    },
                },
                "required": ["repo", "path"],
            },
        ),
        Tool(
            name="find_file",
            description=(
                "Find indexed files whose path contains a substring. Returns repo:path with "
                "chunk counts. Use when you know a filename but not its location. No embedding cost."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Case-insensitive substring of the path."},
                    "repo": {"type": "string", "description": "Optional: restrict to one repo."},
                    "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                },
                "required": ["pattern"],
            },
        ),
        Tool(
            name="list_ottu_sources",
            description="List indexed code repos, docs sites, and internal docs with chunk counts.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="check_ottu_freshness",
            description="Check whether indexed content is stale vs current repo HEAD / docs state.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "search_multi":
        return await _handle_search_multi(arguments)
    if name == "search_ottu_code":
        return await _handle_search_code(arguments)
    if name == "search_ottu_docs":
        return await _handle_search_docs(arguments)
    if name == "get_file_chunks":
        return await _handle_get_file_chunks(arguments)
    if name == "find_file":
        return await _handle_find_file(arguments)
    if name == "list_ottu_sources":
        return await _handle_list_sources()
    if name == "check_ottu_freshness":
        return await _handle_freshness()
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _handle_search_multi(args: dict[str, Any]) -> list[TextContent]:
    raw_queries = args.get("queries") or []
    queries = search.dedupe_queries(raw_queries if isinstance(raw_queries, list) else [])
    if not queries:
        return [TextContent(type="text", text="Missing required `queries` argument.")]

    sources = args.get("sources", "code")
    repo_filter = args.get("repo")
    limit = min(int(args.get("limit", 5)), 20)
    snippet_chars = min(int(args.get("snippet_chars", DEFAULT_SNIPPET_CHARS)), 2000)
    max_dist = float(args.get("max_distance", DISTANCE_THRESHOLD))

    code_colls = _code_collections(repo_filter) if sources in ("code", "both") else []
    docs_colls = _docs_collections() if sources in ("docs", "both") else []
    if not code_colls and not docs_colls:
        return [TextContent(type="text", text="No indexed collections found. Run `python cli.py index` first.")]

    vectors = _get_embeddings().embed_documents(queries)

    seen_ids: set[str] = set()
    sections: list[str] = []

    for query, vec in zip(queries, vectors):
        code_hits = search.query_collections(code_colls, vec, limit) if code_colls else []
        docs_hits = search.query_collections(docs_colls, vec, limit) if docs_colls else []

        merged: list[tuple[search.Hit, bool]] = [(h, True) for h in code_hits] + [
            (h, False) for h in docs_hits
        ]
        merged.sort(key=lambda x: x[0][3])

        lines: list[str] = []
        kept = 0
        for (rid, meta, doc, dist), is_code in merged:
            if kept >= limit:
                break
            if dist > max_dist or rid in seen_ids:
                continue
            seen_ids.add(rid)
            if is_code:
                lines.append(search.format_code_hit(meta, doc, max_chars=snippet_chars, score=dist))
            else:
                lines.append(search.format_docs_hit(meta, doc, max_chars=snippet_chars, score=dist))
            kept += 1

        if not lines:
            sections.append(f"**{query}** — no results within max_distance={max_dist:.2f}")
        else:
            sections.append(f"**{query}** — {len(lines)} result(s)\n\n" + "\n\n".join(lines))

    return [TextContent(type="text", text=("\n\n" + "─" * 50 + "\n\n").join(sections))]


async def _handle_search_code(args: dict[str, Any]) -> list[TextContent]:
    query = args.get("query", "").strip()
    limit = int(args.get("limit", 5))
    repo_filter = args.get("repo")
    snippet_chars = min(int(args.get("snippet_chars", DEFAULT_SNIPPET_CHARS)), 2000)
    if not query:
        return [TextContent(type="text", text="Missing required `query` argument.")]

    colls = _code_collections(repo_filter)
    if not colls:
        msg = "No indexed code collections found"
        if repo_filter:
            msg += f" for repo '{repo_filter}'"
        return [TextContent(type="text", text=msg + ". Run `python cli.py index-code` first.")]

    vec = _get_embeddings().embed_query(query)
    hits = search.query_collections(colls, vec, limit)
    if not hits:
        return [TextContent(type="text", text=f"No results for: {query}")]

    body = f"**{len(hits)} result(s) for:** {query}\n\n"
    body += "\n\n".join(
        search.format_code_hit(m, d, max_chars=snippet_chars, score=dist) for _, m, d, dist in hits
    )
    return [TextContent(type="text", text=body)]


async def _handle_search_docs(args: dict[str, Any]) -> list[TextContent]:
    query = args.get("query", "").strip()
    limit = int(args.get("limit", 5))
    snippet_chars = min(int(args.get("snippet_chars", DEFAULT_SNIPPET_CHARS)), 2000)
    if not query:
        return [TextContent(type="text", text="Missing required `query` argument.")]

    colls = _docs_collections()
    if not colls:
        return [
            TextContent(
                type="text",
                text="No indexed docs yet. Run `python cli.py index-docs` (and optionally `index-markdown`).",
            )
        ]

    vec = _get_embeddings().embed_query(query)
    hits = search.query_collections(colls, vec, limit)
    if not hits:
        return [TextContent(type="text", text=f"No results for: {query}")]

    body = f"**{len(hits)} docs result(s) for:** {query}\n\n"
    body += "\n\n".join(
        search.format_docs_hit(m, d, max_chars=snippet_chars, score=dist) for _, m, d, dist in hits
    )
    return [TextContent(type="text", text=body)]


async def _handle_get_file_chunks(args: dict[str, Any]) -> list[TextContent]:
    repo = (args.get("repo") or "").strip()
    path = (args.get("path") or "").strip()
    snippet_chars = min(int(args.get("snippet_chars", 1500)), 5000)
    if not repo or not path:
        return [TextContent(type="text", text="Both `repo` and `path` are required.")]

    coll = search.resolve_collection_name(
        _get_client(),
        repo,
        list(config.REPOS),
        list(config.DOCS_SITES),
        config.INTERNAL_DOCS_COLLECTION,
    )
    if coll is None:
        return [TextContent(type="text", text=f"Unknown repo/site: '{repo}'. Try list_ottu_sources.")]

    # Crawl-mode docs have only `url` (no `path`), so fall back to a url match
    # when the path lookup turns up empty.
    try:
        res = coll.get(where={"path": path}, include=["documents", "metadatas"])
    except Exception as e:
        return [TextContent(type="text", text=f"Chroma get failed: {e}")]

    ids = res.get("ids") or []
    docs = res.get("documents") or []
    metas = res.get("metadatas") or []

    if not ids:
        try:
            res = coll.get(where={"url": path}, include=["documents", "metadatas"])
            ids = res.get("ids") or []
            docs = res.get("documents") or []
            metas = res.get("metadatas") or []
        except Exception:
            pass

    if not ids:
        return [
            TextContent(
                type="text",
                text=f"No chunks indexed for {repo}:{path}. Try find_file with a substring.",
            )
        ]

    # Sort by chunk_index for stable ordering
    rows = sorted(
        zip(ids, docs, metas),
        key=lambda r: r[2].get("chunk_index", 0) if r[2] else 0,
    )
    parts = [f"**{repo}/{path}** — {len(rows)} chunk(s)"]
    for _, doc, meta in rows:
        parts.append(search.format_code_hit(meta or {}, doc or "", max_chars=snippet_chars))
    return [TextContent(type="text", text="\n\n".join(parts))]


async def _handle_find_file(args: dict[str, Any]) -> list[TextContent]:
    pattern = (args.get("pattern") or "").strip().lower()
    repo_filter = args.get("repo")
    limit = min(int(args.get("limit", 20)), 100)
    if not pattern:
        return [TextContent(type="text", text="Missing required `pattern` argument.")]

    targets: list[tuple[str, chromadb.Collection]] = []
    for r in config.REPOS:
        if repo_filter and r["name"] != repo_filter:
            continue
        c = _safe_collection(r["collection_name"])
        if c is not None:
            targets.append((r["name"], c))
    if not repo_filter:
        for s in config.DOCS_SITES:
            c = _safe_collection(s["collection_name"])
            if c is not None:
                targets.append((s["name"], c))
        internal = _safe_collection(config.INTERNAL_DOCS_COLLECTION)
        if internal is not None:
            targets.append(("internal", internal))

    if not targets:
        return [TextContent(type="text", text="No indexed collections to search.")]

    matches: dict[str, int] = {}
    scanned = 0
    truncated = False
    for label, coll in targets:
        offset = 0
        while scanned < MAX_METADATAS_SCAN:
            try:
                page = coll.get(
                    include=["metadatas"],
                    limit=FIND_FILE_PAGE_SIZE,
                    offset=offset,
                )
            except Exception:
                break
            metas = page.get("metadatas") or []
            if not metas:
                break
            for meta in metas:
                scanned += 1
                if scanned > MAX_METADATAS_SCAN:
                    truncated = True
                    break
                p = (meta or {}).get("path") or (meta or {}).get("url") or ""
                if pattern in p.lower():
                    key = f"{label}:{p}"
                    matches[key] = matches.get(key, 0) + 1
            if len(metas) < FIND_FILE_PAGE_SIZE:
                break
            offset += FIND_FILE_PAGE_SIZE
        if scanned >= MAX_METADATAS_SCAN:
            truncated = True
            break

    if not matches:
        msg = f"No files matching '{pattern}'."
        if truncated:
            msg += f" (Scanned the cap of {MAX_METADATAS_SCAN} chunks; narrow the pattern or pass `repo` to search further.)"
        return [TextContent(type="text", text=msg)]

    sorted_hits = sorted(matches.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    lines = [f"**{len(sorted_hits)} file(s) matching `{pattern}`:**"]
    for label_path, count in sorted_hits:
        lines.append(f"- `{label_path}` — {count} chunk(s)")
    if truncated:
        lines.append(
            f"\n_Note: scan capped at {MAX_METADATAS_SCAN} chunks; results may be incomplete. "
            "Pass `repo` to narrow the search._"
        )
    return [TextContent(type="text", text="\n".join(lines))]


async def _handle_list_sources() -> list[TextContent]:
    lines = ["# Ottu Knowledge Sources\n", "## Code repos"]
    for repo in config.REPOS:
        coll = _safe_collection(repo["collection_name"])
        count = coll.count() if coll else 0
        lines.append(
            f"- **{repo['name']}** — {repo['description']}  "
            f"\n  collection `{repo['collection_name']}` · {count} chunks · path `{repo['path']}`"
        )
    lines.append("\n## Docs sites")
    for site in config.DOCS_SITES:
        coll = _safe_collection(site["collection_name"])
        count = coll.count() if coll else 0
        src = site.get("path") or site.get("url", "")
        lines.append(
            f"- **{site['name']}** ({site.get('mode')}) — {src}  "
            f"\n  collection `{site['collection_name']}` · {count} chunks"
        )
    lines.append("\n## Internal docs")
    internal = _safe_collection(config.INTERNAL_DOCS_COLLECTION)
    lines.append(
        f"- collection `{config.INTERNAL_DOCS_COLLECTION}` · "
        f"{internal.count() if internal else 0} chunks · "
        f"{len(config.MARKDOWN_FILES)} file(s) configured"
    )
    return [TextContent(type="text", text="\n".join(lines))]


async def _handle_freshness() -> list[TextContent]:
    payload = {"repos": freshness.repo_freshness(), "docs": freshness.docs_freshness()}
    return [TextContent(type="text", text="```json\n" + json.dumps(payload, indent=2) + "\n```")]


async def main() -> None:
    async with stdio_server() as (reader, writer):
        await _server.run(reader, writer, _server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
