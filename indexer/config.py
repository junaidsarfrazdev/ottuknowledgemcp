"""Single source of truth for what gets indexed and how.

Add a new code repo, docs site, or internal document by appending to REPOS,
DOCS_SITES, or MARKDOWN_FILES. No other code changes required.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


OTTU_WORKSPACE = "/Users/dev/projects/ottu/junaid"

REPOS: list[CodeRepo] = [
    {
        "name": "checkout_sdk",
        "path": f"{OTTU_WORKSPACE}/checkout_sdk",
        "description": "Ottu checkout payment SDK (Webpack, JS)",
        "collection_name": "ottu_checkout_sdk",
        "priority": 10,
    },
    {
        "name": "connect-sdk",
        "path": f"{OTTU_WORKSPACE}/connect-sdk",
        "description": "Ottu Connect SDK v4 (TypeScript, Vite)",
        "collection_name": "ottu_connect_sdk",
        "priority": 10,
    },
    {
        "name": "onsite_playground",
        "path": f"{OTTU_WORKSPACE}/onsite_playground",
        "description": "Onsite integration HTML/JS demos",
        "collection_name": "ottu_onsite_playground",
        "priority": 5,
    },
]

DOCS_SITES: list[DocsSite] = [
    {
        "name": "ottu_docs_site",
        "mode": "docusaurus_repo",
        "path": f"{OTTU_WORKSPACE}/docs",
        "url_base": "https://docs.ottu.net",
        "collection_name": "ottu_docs",
    },
]

# Absolute paths to .md / .docx / .xlsx files living under docs_local/ (or anywhere).
# User extends this list as they add internal documents.
MARKDOWN_FILES: list[str] = []
INTERNAL_DOCS_COLLECTION = "ottu_internal_docs"

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
