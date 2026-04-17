"""Pre-flight checks — run before any indexing or server start.

Asks the user to set up missing repos; does not auto-clone.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from rich.console import Console

from . import config
from .embeddings import OllamaEmbeddings

console = Console()


def _clone_hint(repo: config.CodeRepo) -> str:
    return (
        f"  git clone git@github.com:ottuco/{repo['name']}.git {repo['path']}\n"
        f"  # or via SSH alias:\n"
        f"  git clone git@junaid_ottu:ottuco/{repo['name']}.git {repo['path']}"
    )


def check_repos(strict: bool = True) -> list[str]:
    """Return list of missing repo names. If strict, exits non-zero when any are missing."""
    missing: list[str] = []
    for repo in config.REPOS:
        path = Path(repo["path"])
        if not path.exists():
            missing.append(repo["name"])
            console.print(
                f"[red]\u2717[/red] {repo['name']} not found at {repo['path']}\n"
                f"    Set it up:\n{_clone_hint(repo)}"
            )
            continue
        if not (path / ".git").exists():
            missing.append(repo["name"])
            console.print(
                f"[red]\u2717[/red] {repo['name']} at {repo['path']} is not a git repo.\n"
                f"    Set it up:\n{_clone_hint(repo)}"
            )
            continue
        console.print(f"[green]\u2713[/green] {repo['name']} ({repo['path']})")

    for site in config.DOCS_SITES:
        if site.get("mode") == "docusaurus_repo":
            p = Path(site.get("path", ""))
            if not p.exists():
                missing.append(site["name"])
                console.print(
                    f"[red]\u2717[/red] docs site '{site['name']}' not found at {p}"
                )
            else:
                console.print(f"[green]\u2713[/green] docs site {site['name']} ({p})")

    if missing and strict:
        console.print(
            "\n[red]Aborting.[/red] Set up the missing repos above, then re-run."
        )
        sys.exit(1)
    return missing


def check_ollama(strict: bool = True) -> bool:
    e = OllamaEmbeddings()
    ok, msg = e.health_check()
    if ok:
        console.print(f"[green]\u2713[/green] {msg}")
    else:
        console.print(f"[red]\u2717[/red] {msg}")
        if strict:
            sys.exit(1)
    return ok


def check_chroma_lfs() -> None:
    """Warn if chroma_db sqlite looks like an LFS pointer instead of real data."""
    sqlite_path = Path(config.CHROMA_DB_PATH) / "chroma.sqlite3"
    if sqlite_path.exists() and sqlite_path.stat().st_size < 500:
        console.print(
            "[yellow]\u26a0[/yellow]  chroma_db/chroma.sqlite3 is tiny — looks like an unresolved "
            "Git LFS pointer. Run: [cyan]git lfs pull[/cyan]"
        )


def run_all(strict: bool = True) -> None:
    console.print("[bold]Pre-flight checks[/bold]")
    check_repos(strict=strict)
    check_ollama(strict=strict)
    check_chroma_lfs()
    console.print()
