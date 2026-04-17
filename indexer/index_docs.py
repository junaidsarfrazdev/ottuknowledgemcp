"""Docusaurus and HTTP docs crawl indexer.

- `docusaurus_repo` mode: walk .md/.mdx under a local Docusaurus repo's `docs/`
  directory, extract frontmatter, strip JSX tags, chunk with markdown splitter.
- `crawl` mode: BeautifulSoup walk of a docs site. Captured Last-Modified for
  freshness checking.
"""
from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import chromadb
import frontmatter
import requests
from bs4 import BeautifulSoup
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter
from rich.console import Console
from rich.progress import Progress

from . import config
from .embeddings import OllamaEmbeddings

console = Console()

_MD_SPLITTER = RecursiveCharacterTextSplitter.from_language(
    language=Language.MARKDOWN,
    chunk_size=config.CHUNK_SIZE,
    chunk_overlap=config.CHUNK_OVERLAP,
)

# Strip <Foo .../> or <Foo>...</Foo> JSX components but keep text inside
_JSX_SELF_CLOSE_RE = re.compile(r"<[A-Z][A-Za-z0-9]*[^>]*/>")
_JSX_OPEN_CLOSE_RE = re.compile(r"<(/?)([A-Z][A-Za-z0-9]*)([^>]*)>")
_IMPORT_EXPORT_RE = re.compile(r"^\s*(import|export) .*?$", re.MULTILINE)


def _strip_mdx(text: str) -> str:
    text = _IMPORT_EXPORT_RE.sub("", text)
    text = _JSX_SELF_CLOSE_RE.sub("", text)
    text = _JSX_OPEN_CLOSE_RE.sub("", text)
    return text


def _slug_from_rel(rel: str, frontmatter_slug: str | None) -> str:
    if frontmatter_slug:
        s = frontmatter_slug
    else:
        s = rel
        for suf in (".mdx", ".md"):
            if s.endswith(suf):
                s = s[: -len(suf)]
        if s.endswith("/index"):
            s = s[: -len("/index")]
    if not s.startswith("/"):
        s = "/" + s
    return s


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


def _index_docusaurus_repo(
    site: config.DocsSite,
    client: chromadb.ClientAPI,
    embeddings: OllamaEmbeddings,
) -> dict:
    root = Path(site["path"])
    docs_dir = root / "docs"
    if not docs_dir.exists():
        console.print(f"[yellow]\u26a0[/yellow]  {site['name']}: no docs/ dir at {docs_dir}")
        return {"site": site["name"], "chunks_written": 0}

    collection = client.get_or_create_collection(
        name=site["collection_name"], metadata={"ottu_docs_site": site["name"]}
    )
    # Drop prior content for this site mode to keep indexing simple/idempotent.
    try:
        collection.delete(where={"source_mode": "docusaurus_repo"})
    except Exception:
        pass

    head_sha = _git_head(root) or "unknown"
    url_base = site.get("url_base", "").rstrip("/")

    files = [p for p in docs_dir.rglob("*") if p.suffix.lower() in (".md", ".mdx")]

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    with Progress(console=console) as progress:
        task = progress.add_task(f"Indexing docs {site['name']}", total=len(files))
        for path in files:
            rel = path.relative_to(docs_dir).as_posix()
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                progress.advance(task)
                continue
            try:
                post = frontmatter.loads(raw)
                fm = dict(post.metadata)
                body = post.content
            except Exception:
                fm = {}
                body = raw
            body = _strip_mdx(body)
            if not body.strip():
                progress.advance(task)
                continue
            slug = _slug_from_rel(rel, fm.get("slug"))
            url = f"{url_base}{slug}" if url_base else slug
            title = fm.get("title") or Path(rel).stem
            for idx, piece in enumerate(_MD_SPLITTER.split_text(body)):
                if not piece.strip():
                    continue
                cid = f"{site['name']}:{rel}:{idx}"
                meta = {
                    "site": site["name"],
                    "source_mode": "docusaurus_repo",
                    "path": rel,
                    "chunk_index": idx,
                    "title": str(title),
                    "url": url,
                    "commit_sha": head_sha,
                    "ingested_at": now_iso,
                }
                ids.append(cid)
                docs.append(piece)
                metas.append(meta)
            progress.advance(task)

    if ids:
        B = config.EMBED_BATCH_SIZE
        for i in range(0, len(ids), B):
            batch = docs[i : i + B]
            vectors = embeddings.embed_documents(batch, batch_size=B)
            collection.add(
                ids=ids[i : i + B],
                documents=batch,
                metadatas=metas[i : i + B],
                embeddings=vectors,
            )

    console.print(
        f"[green]\u2713[/green] docs {site['name']}: {len(files)} files \u2192 {len(ids)} chunks"
    )
    return {"site": site["name"], "chunks_written": len(ids), "head_sha": head_sha}


def _index_crawl(
    site: config.DocsSite,
    client: chromadb.ClientAPI,
    embeddings: OllamaEmbeddings,
    max_pages: int = 500,
    request_delay: float = 0.5,
    max_depth: int = 5,
) -> dict:
    start_url = site["url"].rstrip("/")
    parsed_start = urlparse(start_url)
    allowed_host = parsed_start.netloc

    collection = client.get_or_create_collection(
        name=site["collection_name"], metadata={"ottu_docs_site": site["name"]}
    )
    try:
        collection.delete(where={"source_mode": "crawl"})
    except Exception:
        pass

    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(start_url, 0)]
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    session = requests.Session()
    session.headers.update({"User-Agent": "OttuKnowledgeMCP/1.0 crawler"})

    with Progress(console=console) as progress:
        task = progress.add_task(f"Crawling {site['name']}", total=max_pages)
        while queue and len(seen) < max_pages:
            url, depth = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                r = session.get(url, timeout=30)
                r.raise_for_status()
            except requests.RequestException:
                progress.advance(task)
                continue
            last_modified = r.headers.get("Last-Modified", "")
            soup = BeautifulSoup(r.text, "lxml")
            for sel in ["nav", "header", "footer", "script", "style"]:
                for tag in soup.find_all(sel):
                    tag.decompose()
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else url
            main = soup.find("main") or soup.find("article") or soup.body
            text = main.get_text("\n", strip=True) if main else ""
            if text.strip():
                for idx, piece in enumerate(_MD_SPLITTER.split_text(text)):
                    if not piece.strip():
                        continue
                    cid = f"{site['name']}:{url}:{idx}"
                    metas.append(
                        {
                            "site": site["name"],
                            "source_mode": "crawl",
                            "url": url,
                            "chunk_index": idx,
                            "title": title,
                            "last_modified": last_modified,
                            "ingested_at": now_iso,
                        }
                    )
                    ids.append(cid)
                    docs.append(piece)
            if depth < max_depth:
                for a in soup.find_all("a", href=True):
                    nxt = urljoin(url, a["href"]).split("#", 1)[0]
                    if urlparse(nxt).netloc == allowed_host and nxt not in seen:
                        queue.append((nxt, depth + 1))
            progress.advance(task)
            time.sleep(request_delay)

    if ids:
        B = config.EMBED_BATCH_SIZE
        for i in range(0, len(ids), B):
            batch = docs[i : i + B]
            vectors = embeddings.embed_documents(batch, batch_size=B)
            collection.add(
                ids=ids[i : i + B],
                documents=batch,
                metadatas=metas[i : i + B],
                embeddings=vectors,
            )

    console.print(
        f"[green]\u2713[/green] crawl {site['name']}: {len(seen)} pages \u2192 {len(ids)} chunks"
    )
    return {"site": site["name"], "chunks_written": len(ids), "pages_crawled": len(seen)}


def index_all_docs(
    client: chromadb.ClientAPI, embeddings: OllamaEmbeddings
) -> list[dict]:
    out = []
    for site in config.DOCS_SITES:
        mode = site.get("mode")
        if mode == "docusaurus_repo":
            out.append(_index_docusaurus_repo(site, client, embeddings))
        elif mode == "crawl":
            out.append(_index_crawl(site, client, embeddings))
        else:
            console.print(f"[yellow]\u26a0[/yellow]  unknown mode '{mode}' for {site.get('name')}")
    return out
