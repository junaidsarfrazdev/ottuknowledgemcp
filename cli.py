"""CLI for indexing and inspecting the Ottu knowledge base."""
from __future__ import annotations

import sys
from pathlib import Path

import chromadb
import click
from rich.console import Console
from rich.table import Table

from indexer import (
    config,
    freshness,
    index_code,
    index_docs,
    index_markdown,
    preflight,
    search,
)
from indexer.embeddings import OllamaEmbeddings

console = Console()


def _client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=config.CHROMA_DB_PATH)


def _embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings()


@click.group()
def cli():
    """Ottu Knowledge MCP — indexing and inspection tools."""


@cli.command()
def doctor() -> None:
    """Run pre-flight checks: repos, Ollama, LFS."""
    preflight.run_all(strict=False)


@cli.command("index")
@click.option("--force", is_flag=True, help="Re-index all files (ignore incremental metadata)")
def index_all_cmd(force: bool) -> None:
    """Index everything: code repos, docs sites, internal docs."""
    preflight.run_all(strict=True)
    client = _client()
    emb = _embeddings()
    index_code.index_all(client, emb, force=force)
    index_docs.index_all_docs(client, emb)
    index_markdown.index_internal_docs(client, emb)


@cli.command("index-code")
@click.argument("repo", required=False)
@click.option("--force", is_flag=True)
def index_code_cmd(repo: str | None, force: bool) -> None:
    """Index code repos; optionally one by name."""
    preflight.run_all(strict=True)
    client = _client()
    emb = _embeddings()
    index_code.index_all(client, emb, only=repo, force=force)


@cli.command("index-docs")
def index_docs_cmd() -> None:
    """Index docs sites (Docusaurus source or crawl)."""
    preflight.run_all(strict=True)
    index_docs.index_all_docs(_client(), _embeddings())


@cli.command("index-markdown")
def index_markdown_cmd() -> None:
    """Index standalone markdown / docx / xlsx files listed in config.MARKDOWN_FILES."""
    preflight.run_all(strict=True)
    index_markdown.index_internal_docs(_client(), _embeddings())


@cli.command()
def reindex() -> None:
    """Drop all collections and re-index from scratch."""
    preflight.run_all(strict=True)
    client = _client()
    for name in config.all_collection_names():
        try:
            client.delete_collection(name=name)
            console.print(f"dropped {name}")
        except Exception:
            pass
    # Remove incremental metadata so everything re-embeds
    for repo in config.REPOS:
        mp = Path(repo["path"]) / config.METADATA_FILENAME
        if mp.exists():
            mp.unlink()
    for site in config.DOCS_SITES:
        if site.get("mode") == "docusaurus_repo" and site.get("path"):
            mp = Path(site["path"]) / config.DOCS_METADATA_FILENAME
            if mp.exists():
                mp.unlink()
    internal_mp = config.PROJECT_ROOT / ".internal_docs_metadata.json"
    if internal_mp.exists():
        internal_mp.unlink()
    emb = _embeddings()
    index_code.index_all(client, emb)
    index_docs.index_all_docs(client, emb)
    index_markdown.index_internal_docs(client, emb)


@cli.command()
def stats() -> None:
    """Show per-collection chunk counts and last-indexed times."""
    client = _client()
    table = Table(title="Ottu Knowledge Base")
    table.add_column("Collection")
    table.add_column("Chunks", justify="right")
    table.add_column("Source")
    for repo in config.REPOS:
        try:
            c = client.get_collection(name=repo["collection_name"])
            count = c.count()
        except Exception:
            count = 0
        table.add_row(repo["collection_name"], str(count), f"repo:{repo['name']}")
    for site in config.DOCS_SITES:
        try:
            c = client.get_collection(name=site["collection_name"])
            count = c.count()
        except Exception:
            count = 0
        table.add_row(site["collection_name"], str(count), f"docs:{site['name']}")
    try:
        c = client.get_collection(name=config.INTERNAL_DOCS_COLLECTION)
        count = c.count()
    except Exception:
        count = 0
    table.add_row(config.INTERNAL_DOCS_COLLECTION, str(count), "internal")
    console.print(table)


@cli.command()
@click.argument("query")
@click.option("--repo", default=None, help="Restrict to one repo")
@click.option("--limit", default=5)
@click.option("--docs", is_flag=True, help="Search docs collections instead of code")
def search_cmd(query: str, repo: str | None, limit: int, docs: bool) -> None:
    """Ad-hoc semantic search from the CLI."""
    client = _client()
    emb = _embeddings()
    vec = emb.embed_query(query)

    targets: list[chromadb.Collection] = []
    if docs:
        for site in config.DOCS_SITES:
            try:
                targets.append(client.get_collection(name=site["collection_name"]))
            except Exception:
                pass
        try:
            targets.append(client.get_collection(name=config.INTERNAL_DOCS_COLLECTION))
        except Exception:
            pass
    else:
        for r in config.REPOS:
            if repo and r["name"] != repo:
                continue
            try:
                targets.append(client.get_collection(name=r["collection_name"]))
            except Exception:
                pass

    if not targets:
        console.print("[red]No matching collections. Run an index command first.[/red]")
        sys.exit(1)

    hits = search.query_collections(targets, vec, limit)
    for _, m, d, dist in hits:
        repo_or_site = m.get("repo") or m.get("site") or m.get("filename") or "?"
        path = m.get("path", m.get("url", ""))
        console.print(f"[cyan]{repo_or_site}[/cyan] {path}  [dim](distance {dist:.3f})[/dim]")
        snippet = d.strip().replace("\n", "\n  ")
        console.print(f"  {snippet[:500]}{'...' if len(snippet) > 500 else ''}\n")


# Keep the historical command name too
cli.add_command(search_cmd, name="search")


@cli.command("freshness")
def freshness_cmd() -> None:
    """Show freshness status for all indexed sources."""
    rows_code = freshness.repo_freshness()
    rows_docs = freshness.docs_freshness()
    table = Table(title="Freshness")
    table.add_column("Source")
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Indexed SHA")
    table.add_column("Current SHA")
    for r in rows_code:
        table.add_row(
            r["name"],
            "repo",
            r["status"],
            (r.get("indexed_sha") or "-")[:10],
            (r.get("current_sha") or "-")[:10],
        )
    for r in rows_docs:
        table.add_row(
            r["name"],
            "docs:" + r.get("mode", "?"),
            r["status"],
            (r.get("indexed_sha") or "-")[:10],
            (r.get("current_sha") or r.get("last_modified") or "-")[:40],
        )
    console.print(table)


if __name__ == "__main__":
    cli()
