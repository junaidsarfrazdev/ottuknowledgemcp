"""Docusaurus and HTTP docs crawl indexer.

- `docusaurus_repo` mode: walk .md/.mdx under a local Docusaurus repo's `docs/`
  directory, extract frontmatter, strip JSX tags, chunk with markdown splitter.
  Incremental: tracks per-file SHA in `.index_metadata.json`, skips unchanged.
- `crawl` mode: BeautifulSoup walk of a docs site. Captures Last-Modified for
  freshness checking. Crawl mode re-crawls fully each run.
"""
from __future__ import annotations

import hashlib
import json
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


def _file_sha(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_site_metadata(root: Path) -> dict:
    mp = root / config.DOCS_METADATA_FILENAME
    if not mp.exists():
        return {}
    try:
        return json.loads(mp.read_text())
    except json.JSONDecodeError:
        return {}


def _save_site_metadata(root: Path, data: dict) -> None:
    (root / config.DOCS_METADATA_FILENAME).write_text(json.dumps(data, indent=2))


def _index_docusaurus_repo(
    site: config.DocsSite,
    client: chromadb.ClientAPI,
    embeddings: OllamaEmbeddings,
) -> dict:
    root = Path(site["path"])
    docs_dir = root / "docs"
    if not docs_dir.exists():
        console.print(f"[yellow]⚠[/yellow]  {site['name']}: no docs/ dir at {docs_dir}")
        return {"site": site["name"], "chunks_written": 0}

    collection = client.get_or_create_collection(
        name=site["collection_name"], metadata={"ottu_docs_site": site["name"]}
    )

    head_sha = _git_head(root) or "unknown"
    url_base = site.get("url_base", "").rstrip("/")

    site_meta = _load_site_metadata(root)
    known_files: dict[str, dict] = site_meta.get("files", {})

    files = [p for p in docs_dir.rglob("*") if p.suffix.lower() in (".md", ".mdx")]
    current_paths: set[str] = set()

    ids_to_add: list[str] = []
    docs_to_add: list[str] = []
    metas_to_add: list[dict] = []

    changed = 0
    skipped = 0
    removed = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    with Progress(console=console) as progress:
        task = progress.add_task(f"Indexing docs {site['name']}", total=len(files))
        for path in files:
            rel = path.relative_to(docs_dir).as_posix()
            current_paths.add(rel)
            sha = _file_sha(path)
            prev = known_files.get(rel)
            if prev and prev.get("sha") == sha:
                skipped += 1
                progress.advance(task)
                continue

            if prev:
                try:
                    collection.delete(where={"path": rel})
                except Exception:
                    pass

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
                known_files[rel] = {"sha": sha, "chunks": 0}
                changed += 1
                progress.advance(task)
                continue

            slug = _slug_from_rel(rel, fm.get("slug"))
            url = f"{url_base}{slug}" if url_base else slug
            title = fm.get("title") or Path(rel).stem

            chunk_count = 0
            for idx, piece in enumerate(_MD_SPLITTER.split_text(body)):
                if not piece.strip():
                    continue
                cid = f"{site['name']}:{rel}:{idx}"
                ids_to_add.append(cid)
                docs_to_add.append(piece)
                metas_to_add.append(
                    {
                        "site": site["name"],
                        "source_mode": "docusaurus_repo",
                        "path": rel,
                        "chunk_index": idx,
                        "title": str(title),
                        "url": url,
                        "commit_sha": head_sha,
                        "file_sha": sha,
                        "ingested_at": now_iso,
                    }
                )
                chunk_count += 1
            known_files[rel] = {"sha": sha, "chunks": chunk_count}
            changed += 1
            progress.advance(task)

    # Remove chunks for files no longer present
    stale = set(known_files.keys()) - current_paths
    for rel in stale:
        try:
            collection.delete(where={"path": rel})
        except Exception:
            pass
        known_files.pop(rel, None)
        removed += 1

    if ids_to_add:
        B = config.EMBED_BATCH_SIZE
        for i in range(0, len(ids_to_add), B):
            batch = docs_to_add[i : i + B]
            vectors = embeddings.embed_documents(batch, batch_size=B)
            collection.add(
                ids=ids_to_add[i : i + B],
                documents=batch,
                metadatas=metas_to_add[i : i + B],
                embeddings=vectors,
            )

    _save_site_metadata(
        root,
        {
            "site": site["name"],
            "mode": "docusaurus_repo",
            "head_sha": head_sha,
            "indexed_at": now_iso,
            "files": known_files,
        },
    )

    console.print(
        f"[green]✓[/green] docs {site['name']}: "
        f"{changed} changed, {skipped} unchanged, {removed} removed, "
        f"{len(ids_to_add)} chunks written"
    )
    return {"site": site["name"], "chunks_written": len(ids_to_add), "head_sha": head_sha}


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
        f"[green]✓[/green] crawl {site['name']}: {len(seen)} pages → {len(ids)} chunks"
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
            console.print(f"[yellow]⚠[/yellow]  unknown mode '{mode}' for {site.get('name')}")
    return out
