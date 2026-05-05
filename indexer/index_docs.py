"""Docusaurus and HTTP docs crawl indexer.

- `docusaurus_repo` mode: walk .md/.mdx under a local Docusaurus repo's `docs/`
  directory, extract frontmatter, strip JSX tags, chunk with markdown splitter.
- `crawl` mode: BeautifulSoup walk of a docs site. Captured Last-Modified for
  freshness checking.
"""
from __future__ import annotations

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


def _format_operation(path: str, method: str, op: dict, components: dict) -> str:
    lines = [f"# {method.upper()} {path}"]
    if op.get("summary"):
        lines.append(f"\n**Summary:** {op['summary']}")
    if op.get("operationId"):
        lines.append(f"\n**Operation ID:** `{op['operationId']}`")
    if op.get("tags"):
        lines.append(f"\n**Tags:** {', '.join(op['tags'])}")
    if op.get("description"):
        lines.append(f"\n## Description\n\n{op['description']}")
    params = op.get("parameters") or []
    if params:
        lines.append("\n## Parameters\n")
        for p in params:
            loc = p.get("in", "?")
            name = p.get("name", "?")
            req = " (required)" if p.get("required") else ""
            desc = p.get("description", "")
            schema = p.get("schema") or {}
            type_ = schema.get("type") or schema.get("$ref", "")
            lines.append(f"- `{name}` [{loc}]{req} \u2014 {type_}: {desc}")
    body = op.get("requestBody")
    if body:
        lines.append("\n## Request Body\n")
        if body.get("description"):
            lines.append(body["description"])
        for ct, media in (body.get("content") or {}).items():
            schema = media.get("schema") or {}
            lines.append(f"\n- Content-Type: `{ct}`")
            if "$ref" in schema:
                lines.append(f"  - Schema: `{schema['$ref']}`")
            elif schema:
                lines.append(f"  - Schema: ```json\n{json.dumps(schema, indent=2)[:1500]}\n```")
    responses = op.get("responses") or {}
    if responses:
        lines.append("\n## Responses\n")
        for code, resp in responses.items():
            desc = resp.get("description", "")
            lines.append(f"- **{code}** \u2014 {desc}")
            for ct, media in (resp.get("content") or {}).items():
                schema = media.get("schema") or {}
                if "$ref" in schema:
                    lines.append(f"  - `{ct}`: `{schema['$ref']}`")
    return "\n".join(lines)


def _format_schema(name: str, schema: dict) -> str:
    lines = [f"# Schema: {name}"]
    if schema.get("description"):
        lines.append(f"\n{schema['description']}")
    if schema.get("type"):
        lines.append(f"\n**Type:** {schema['type']}")
    if schema.get("required"):
        lines.append(f"\n**Required:** {', '.join(schema['required'])}")
    props = schema.get("properties") or {}
    if props:
        lines.append("\n## Properties\n")
        for pname, pschema in props.items():
            ptype = pschema.get("type") or pschema.get("$ref", "")
            pdesc = pschema.get("description", "")
            lines.append(f"- `{pname}` ({ptype}) \u2014 {pdesc}")
    if schema.get("enum"):
        lines.append(f"\n**Enum:** {schema['enum']}")
    lines.append(f"\n## Raw\n\n```json\n{json.dumps(schema, indent=2)[:2000]}\n```")
    return "\n".join(lines)


def _index_openapi(
    site: config.DocsSite,
    client: chromadb.ClientAPI,
    embeddings: OllamaEmbeddings,
) -> dict:
    spec_url = site["url"].rstrip("/")
    docs_url = site.get("docs_url", spec_url)
    try:
        r = requests.get(spec_url, timeout=30)
        r.raise_for_status()
        spec = r.json()
    except Exception as e:
        console.print(f"[red]\u2717[/red] {site['name']}: failed to fetch {spec_url}: {e}")
        return {"site": site["name"], "chunks_written": 0}

    collection = client.get_or_create_collection(
        name=site["collection_name"], metadata={"ottu_docs_site": site["name"]}
    )
    try:
        collection.delete(where={"source_mode": "openapi"})
    except Exception:
        pass

    components = spec.get("components") or {}
    info = spec.get("info") or {}
    api_title = info.get("title", site["name"])
    api_version = info.get("version", "")
    now_iso = datetime.now(timezone.utc).isoformat()

    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []

    if info.get("description"):
        overview = f"# {api_title} (v{api_version})\n\n{info['description']}"
        for idx, piece in enumerate(_MD_SPLITTER.split_text(overview)):
            if not piece.strip():
                continue
            ids.append(f"{site['name']}:overview:{idx}")
            docs.append(piece)
            metas.append({
                "site": site["name"],
                "source_mode": "openapi",
                "kind": "overview",
                "title": f"{api_title} overview",
                "url": docs_url,
                "chunk_index": idx,
                "ingested_at": now_iso,
            })

    paths = spec.get("paths") or {}
    for path, path_item in paths.items():
        for method, op in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            if not isinstance(op, dict):
                continue
            text = _format_operation(path, method, op, components)
            for idx, piece in enumerate(_MD_SPLITTER.split_text(text)):
                if not piece.strip():
                    continue
                cid = f"{site['name']}:op:{method.upper()}:{path}:{idx}"
                ids.append(cid)
                docs.append(piece)
                metas.append({
                    "site": site["name"],
                    "source_mode": "openapi",
                    "kind": "operation",
                    "method": method.upper(),
                    "path": path,
                    "operation_id": op.get("operationId", ""),
                    "title": op.get("summary") or f"{method.upper()} {path}",
                    "tags": ",".join(op.get("tags", []) or []),
                    "url": f"{docs_url}#operation/{op.get('operationId', '')}" if op.get("operationId") else docs_url,
                    "chunk_index": idx,
                    "ingested_at": now_iso,
                })

    schemas = components.get("schemas") or {}
    for sname, sschema in schemas.items():
        if not isinstance(sschema, dict):
            continue
        text = _format_schema(sname, sschema)
        for idx, piece in enumerate(_MD_SPLITTER.split_text(text)):
            if not piece.strip():
                continue
            cid = f"{site['name']}:schema:{sname}:{idx}"
            ids.append(cid)
            docs.append(piece)
            metas.append({
                "site": site["name"],
                "source_mode": "openapi",
                "kind": "schema",
                "schema_name": sname,
                "title": f"Schema: {sname}",
                "url": f"{docs_url}#schema/{sname}",
                "chunk_index": idx,
                "ingested_at": now_iso,
            })

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
        f"[green]\u2713[/green] openapi {site['name']}: {len(paths)} paths, "
        f"{len(schemas)} schemas \u2192 {len(ids)} chunks"
    )
    return {
        "site": site["name"],
        "chunks_written": len(ids),
        "paths_indexed": len(paths),
        "schemas_indexed": len(schemas),
    }


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
        elif mode == "openapi":
            out.append(_index_openapi(site, client, embeddings))
        else:
            console.print(f"[yellow]\u26a0[/yellow]  unknown mode '{mode}' for {site.get('name')}")
    return out
