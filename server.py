"""MCP server exposing Ottu knowledge-base search tools to Claude Code.

Tools:
  - search_ottu_code: semantic search across indexed code repos.
  - search_ottu_docs: semantic search across indexed docs + internal docs.
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

from indexer import config, freshness
from indexer.embeddings import OllamaEmbeddings

_server = Server("ottu-knowledge")
_embeddings: OllamaEmbeddings | None = None
_client: chromadb.ClientAPI | None = None


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


def _format_code_hit(meta: dict, doc: str) -> str:
    repo = meta.get("repo", "?")
    path = meta.get("path", "?")
    chunk = meta.get("chunk_index", "?")
    section = meta.get("sfc_section")
    header = f"**{repo}:{path}** (chunk {chunk}"
    if section and section != "root":
        header += f", {section}"
    header += ")"
    snippet = doc.strip()
    if len(snippet) > 1200:
        snippet = snippet[:1200] + "\n...[truncated]"
    lang = meta.get("language", "")
    return f"{header}\n```{lang}\n{snippet}\n```"


def _format_docs_hit(meta: dict, doc: str) -> str:
    title = meta.get("title", "")
    url = meta.get("url", "")
    path = meta.get("path", meta.get("filename", ""))
    header = f"**{title or path}**"
    if url:
        header += f" — <{url}>"
    elif path:
        header += f" — `{path}`"
    snippet = doc.strip()
    if len(snippet) > 1200:
        snippet = snippet[:1200] + "\n...[truncated]"
    return f"{header}\n{snippet}"


def _query_collections(
    collections: list[chromadb.Collection],
    query: str,
    limit: int,
) -> list[tuple[dict, str, float]]:
    emb = _get_embeddings().embed_query(query)
    hits: list[tuple[dict, str, float]] = []
    for coll in collections:
        try:
            res = coll.query(
                query_embeddings=[emb],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            continue
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for d, m, dist in zip(docs, metas, dists):
            hits.append((m or {}, d or "", float(dist)))
    hits.sort(key=lambda h: h[2])
    return hits[:limit]


@_server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_ottu_code",
            description=(
                "Semantic search across indexed Ottu code repositories "
                "(checkout_sdk, connect-sdk, onsite_playground, plus any others "
                "added later). Returns snippets with repo:path."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language query"},
                    "repo": {
                        "type": "string",
                        "description": "Optional: restrict to one repo name",
                    },
                    "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 25},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="search_ottu_docs",
            description=(
                "Semantic search across indexed Ottu documentation (docs.ottu.net "
                "via the Docusaurus source) and internal docs (.md/.docx/.xlsx "
                "files configured in MARKDOWN_FILES)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 25},
                },
                "required": ["query"],
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
    if name == "search_ottu_code":
        return await _handle_search_code(arguments)
    if name == "search_ottu_docs":
        return await _handle_search_docs(arguments)
    if name == "list_ottu_sources":
        return await _handle_list_sources()
    if name == "check_ottu_freshness":
        return await _handle_freshness()
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _handle_search_code(args: dict[str, Any]) -> list[TextContent]:
    query = args.get("query", "").strip()
    limit = int(args.get("limit", 5))
    repo_filter = args.get("repo")
    if not query:
        return [TextContent(type="text", text="Missing required `query` argument.")]

    target_collections: list[chromadb.Collection] = []
    for repo in config.REPOS:
        if repo_filter and repo["name"] != repo_filter:
            continue
        coll = _safe_collection(repo["collection_name"])
        if coll is not None:
            target_collections.append(coll)
    if not target_collections:
        msg = (
            f"No indexed collections found"
            + (f" for repo '{repo_filter}'." if repo_filter else ".")
            + " Run `python cli.py index-code` first."
        )
        return [TextContent(type="text", text=msg)]

    hits = _query_collections(target_collections, query, limit)
    if not hits:
        return [TextContent(type="text", text=f"No results for: {query}")]

    body = f"**{len(hits)} result(s) for:** {query}\n\n"
    body += "\n\n---\n\n".join(_format_code_hit(m, d) for m, d, _ in hits)
    return [TextContent(type="text", text=body)]


async def _handle_search_docs(args: dict[str, Any]) -> list[TextContent]:
    query = args.get("query", "").strip()
    limit = int(args.get("limit", 5))
    if not query:
        return [TextContent(type="text", text="Missing required `query` argument.")]

    target_collections: list[chromadb.Collection] = []
    for site in config.DOCS_SITES:
        coll = _safe_collection(site["collection_name"])
        if coll is not None:
            target_collections.append(coll)
    internal = _safe_collection(config.INTERNAL_DOCS_COLLECTION)
    if internal is not None:
        target_collections.append(internal)

    if not target_collections:
        return [
            TextContent(
                type="text",
                text="No indexed docs yet. Run `python cli.py index-docs` (and optionally `index-markdown`).",
            )
        ]

    hits = _query_collections(target_collections, query, limit)
    if not hits:
        return [TextContent(type="text", text=f"No results for: {query}")]

    body = f"**{len(hits)} docs result(s) for:** {query}\n\n"
    body += "\n\n---\n\n".join(_format_docs_hit(m, d) for m, d, _ in hits)
    return [TextContent(type="text", text=body)]


async def _handle_list_sources() -> list[TextContent]:
    client = _get_client()
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
    rows_code = freshness.repo_freshness()
    rows_docs = freshness.docs_freshness()
    payload = {"repos": rows_code, "docs": rows_docs}
    body = "```json\n" + json.dumps(payload, indent=2) + "\n```"
    return [TextContent(type="text", text=body)]


async def main() -> None:
    async with stdio_server() as (reader, writer):
        await _server.run(reader, writer, _server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
