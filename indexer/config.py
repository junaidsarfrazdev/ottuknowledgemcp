"""Single source of truth for what gets indexed and how.

Configuration precedence (highest wins):
  1. OTTU_CONFIG env var pointing at a JSON file (see ottu_config.example.json).
  2. OTTU_WORKSPACE env var (used to resolve default repo/doc paths).
  3. Hardcoded defaults below.

The JSON file may define `workspace`, `repos`, `docs_sites`, `markdown_files`.
Use `${WORKSPACE}` inside paths for substitution.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Auto-load project-root .env so OTTU_WORKSPACE / OTTU_CONFIG set by setup.sh
# are picked up by both CLI and the MCP server (which starts without a shell).
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # python-dotenv missing → fall back to real env only
    pass


class CodeRepo(TypedDict):
    name: str
    path: str
    description: str
    collection_name: str
    priority: int


class DocsSite(TypedDict, total=False):
    name: str
    mode: str  # "docusaurus_repo" | "crawl"
    path: str  # for docusaurus_repo
    url: str  # for crawl
    url_base: str
    collection_name: str


_DEFAULT_WORKSPACE = str(Path.home() / "projects" / "ottu")


# Defaults. Override via OTTU_CONFIG JSON. Paths support ${WORKSPACE}.
DEFAULT_REPOS: list[CodeRepo] = [
    {
        "name": "checkout_sdk",
        "path": "${WORKSPACE}/checkout_sdk",
        "description": "Ottu checkout payment SDK (Webpack, JS)",
        "collection_name": "ottu_checkout_sdk",
        "priority": 10,
    },
    {
        "name": "connect-sdk",
        "path": "${WORKSPACE}/connect-sdk",
        "description": "Ottu Connect SDK v4 (TypeScript, Vite)",
        "collection_name": "ottu_connect_sdk",
        "priority": 10,
    },
    {
        "name": "onsite_playground",
        "path": "${WORKSPACE}/onsite_playground",
        "description": "Onsite integration HTML/JS demos",
        "collection_name": "ottu_onsite_playground",
        "priority": 5,
    },
]

DEFAULT_DOCS_SITES: list[DocsSite] = [
    {
        "name": "ottu_docs_site",
        "mode": "docusaurus_repo",
        "path": "${WORKSPACE}/docs",
        "url_base": "https://docs.ottu.net",
        "collection_name": "ottu_docs",
    },
]

DEFAULT_MARKDOWN_FILES: list[str] = []

INTERNAL_DOCS_COLLECTION = "ottu_internal_docs"


def _subst(value: str, workspace: str) -> str:
    return value.replace("${WORKSPACE}", workspace)


def _load_config() -> tuple[str, list[CodeRepo], list[DocsSite], list[str]]:
    workspace = os.environ.get("OTTU_WORKSPACE", _DEFAULT_WORKSPACE)
    repos: list[CodeRepo] = [dict(r) for r in DEFAULT_REPOS]  # type: ignore[misc]
    docs_sites: list[DocsSite] = [dict(d) for d in DEFAULT_DOCS_SITES]  # type: ignore[misc]
    markdown_files: list[str] = list(DEFAULT_MARKDOWN_FILES)

    cfg_path = os.environ.get("OTTU_CONFIG")
    if cfg_path:
        p = Path(cfg_path)
        if not p.exists():
            raise SystemExit(f"OTTU_CONFIG points to missing file: {p}")
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            raise SystemExit(f"Invalid JSON in {p}: {e}")
        if isinstance(data.get("workspace"), str):
            workspace = data["workspace"]
        if isinstance(data.get("repos"), list):
            repos = data["repos"]
        if isinstance(data.get("docs_sites"), list):
            docs_sites = data["docs_sites"]
        if isinstance(data.get("markdown_files"), list):
            markdown_files = data["markdown_files"]

    for r in repos:
        r["path"] = _subst(r["path"], workspace)
    for d in docs_sites:
        if "path" in d:
            d["path"] = _subst(d["path"], workspace)
    markdown_files = [_subst(m, workspace) for m in markdown_files]

    return workspace, repos, docs_sites, markdown_files


OTTU_WORKSPACE, REPOS, DOCS_SITES, MARKDOWN_FILES = _load_config()


INCLUDE_EXTENSIONS = {
    ".js", ".ts", ".tsx", ".jsx", ".vue",
    ".html", ".css", ".scss",
    ".md", ".mdx",
    ".json",
}
EXCLUDE_DIRS = {
    "node_modules", "dist", "build", ".git", "coverage",
    "__pycache__", ".cache", "venv", ".venv", ".docusaurus",
    ".next", ".nuxt", ".output", "out",
    ".pytest_cache", ".mypy_cache", ".tox", "target",
}
EXCLUDE_SUFFIXES = {".min.js", ".min.css", ".map"}
EXCLUDE_FILENAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
EMBED_BATCH_SIZE = 50
MAX_FILE_BYTES = 500_000

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
CHROMA_DB_PATH = os.environ.get("CHROMA_DB_PATH", str(PROJECT_ROOT / "chroma_db"))

METADATA_FILENAME = ".index_metadata.json"
DOCS_METADATA_FILENAME = ".index_metadata.json"  # sits at docs repo root


def get_repo(name: str) -> CodeRepo | None:
    for r in REPOS:
        if r["name"] == name:
            return r
    return None


def all_collection_names() -> list[str]:
    names = [r["collection_name"] for r in REPOS]
    names += [d["collection_name"] for d in DOCS_SITES]
    names.append(INTERNAL_DOCS_COLLECTION)
    return names
