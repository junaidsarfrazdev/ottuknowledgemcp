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


OTTU_WORKSPACE = "/home/dev/projects/ottu"

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
    {
        "name": "ottu-js-sdk",
        "path": f"{OTTU_WORKSPACE}/ottu-js-sdk",
        "description": "Ottu JavaScript SDK",
        "collection_name": "ottu_js_sdk",
        "priority": 10,
    },
    {
        "name": "core_frontend",
        "path": f"{OTTU_WORKSPACE}/core_frontend",
        "description": "Core admin/dashboard frontend",
        "collection_name": "ottu_core_frontend",
        "priority": 10,
    },
    {
        "name": "frontend_public",
        "path": f"{OTTU_WORKSPACE}/frontend_public",
        "description": "Public-facing frontend (checkout flow)",
        "collection_name": "ottu_frontend_public",
        "priority": 10,
    },
    {
        "name": "echo_frontend",
        "path": f"{OTTU_WORKSPACE}/echo_frontend",
        "description": "Echo product frontend",
        "collection_name": "ottu_echo_frontend",
        "priority": 5,
    },
    {
        "name": "event_frontend",
        "path": f"{OTTU_WORKSPACE}/event_frontend",
        "description": "Event ticketing frontend",
        "collection_name": "ottu_event_frontend",
        "priority": 5,
    },
    {
        "name": "fnb_frontend",
        "path": f"{OTTU_WORKSPACE}/fnb_frontend",
        "description": "Food & beverage frontend",
        "collection_name": "ottu_fnb_frontend",
        "priority": 5,
    },
    {
        "name": "payout_frontend",
        "path": f"{OTTU_WORKSPACE}/payout_frontend",
        "description": "Payout product frontend",
        "collection_name": "ottu_payout_frontend",
        "priority": 5,
    },
    {
        "name": "real_estate_frontend",
        "path": f"{OTTU_WORKSPACE}/real_estate_frontend",
        "description": "Real estate vertical frontend",
        "collection_name": "ottu_real_estate_frontend",
        "priority": 5,
    },
    {
        "name": "navigation_v2",
        "path": f"{OTTU_WORKSPACE}/navigation_v2",
        "description": "Navigation shell v2 (microfrontend host)",
        "collection_name": "ottu_navigation_v2",
        "priority": 10,
    },
    {
        "name": "root_conf",
        "path": f"{OTTU_WORKSPACE}/root_conf",
        "description": "Root configuration / orchestration",
        "collection_name": "ottu_root_conf",
        "priority": 5,
    },
    {
        "name": "toolbox",
        "path": f"{OTTU_WORKSPACE}/toolbox",
        "description": "Shared toolbox utilities",
        "collection_name": "ottu_toolbox",
        "priority": 5,
    },
    {
        "name": "pdf-renderer",
        "path": f"{OTTU_WORKSPACE}/pdf-renderer",
        "description": "PDF rendering service",
        "collection_name": "ottu_pdf_renderer",
        "priority": 3,
    },
    {
        "name": "docusaurus-openapi-docs",
        "path": f"{OTTU_WORKSPACE}/docusaurus-openapi-docs",
        "description": "OpenAPI Docusaurus plugin/customization",
        "collection_name": "ottu_docusaurus_openapi_docs",
        "priority": 3,
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
