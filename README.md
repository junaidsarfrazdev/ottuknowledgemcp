# OttuKnowledgeMCP

Local MCP server that gives Claude Code semantic search over Ottu code repos and docs.

Runs on **macOS, Linux, and Windows**.

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.ai/) with `nomic-embed-text` model pulled
- [GitHub CLI](https://cli.github.com/) (`gh`) — used to clone Ottu repos over HTTPS/SSO
- `git-lfs` (only needed if you'll ship pre-built embeddings to teammates)
- Ottu repos cloned locally (see "Add your repos" below)

### Install prerequisites

**macOS (Homebrew):**
```bash
brew install ollama git-lfs gh
```

**Linux (Debian/Ubuntu):**
```bash
# Ollama
curl -fsSL https://ollama.com/install.sh | sh
# git-lfs + gh
sudo apt update && sudo apt install -y git-lfs gh
```

**Windows (winget, PowerShell):**
```powershell
winget install Ollama.Ollama
winget install GitHub.GitLFS
winget install GitHub.cli
```

Then on any OS:
```bash
ollama pull nomic-embed-text
git lfs install
gh auth login   # once, so `gh repo clone` works
```

## Install

Clone this repo wherever you like (referred to as `<mcp-root>` below), then:

**macOS / Linux / WSL:**
```bash
cd <mcp-root>
bash setup.sh
```

**Windows (PowerShell):**
```powershell
cd <mcp-root>
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

## Add your repos

Pick a workspace directory (referred to as `<workspace>` below) and clone the repos you want indexed. Example:

**macOS / Linux:**
```bash
mkdir -p ~/ottu-workspace
cd ~/ottu-workspace

gh repo clone ottuco/checkout_sdk
gh repo clone ottuco/connect-sdk
gh repo clone ottuco/onsite_playground
gh repo clone ottuco/docs
# …add any others you care about
```

**Windows (PowerShell):**
```powershell
mkdir $HOME\ottu-workspace
cd $HOME\ottu-workspace

gh repo clone ottuco/checkout_sdk
gh repo clone ottuco/connect-sdk
gh repo clone ottuco/onsite_playground
gh repo clone ottuco/docs
```

Then open `indexer/config.py` and point it at your workspace:

- `OTTU_WORKSPACE` → absolute path to the directory you cloned into
- `REPOS` → one entry per code repo (name, path, collection_name)
- `DOCS_SITES` → one entry per Docusaurus/docs repo

Only repos listed in `config.py` are indexed — remove entries you don't need, or append new ones.

## Index

**macOS / Linux:**
```bash
source venv/bin/activate
python cli.py doctor         # verify repos, Ollama, LFS
python cli.py index          # code + docs + internal files
python cli.py stats          # chunk counts per collection
python cli.py search "3DS tokenization"
```

**Windows (PowerShell):**
```powershell
.\venv\Scripts\Activate.ps1
python cli.py doctor
python cli.py index
python cli.py stats
python cli.py search "3DS tokenization"
```

## Claude Code config

Create `<workspace>/.mcp.json` (project-scoped):

**macOS / Linux:**
```json
{
  "mcpServers": {
    "ottu-knowledge": {
      "type": "stdio",
      "command": "<mcp-root>/venv/bin/python",
      "args": ["<mcp-root>/server.py"]
    }
  }
}
```

**Windows:**
```json
{
  "mcpServers": {
    "ottu-knowledge": {
      "type": "stdio",
      "command": "<mcp-root>\\venv\\Scripts\\python.exe",
      "args": ["<mcp-root>\\server.py"]
    }
  }
}
```

Replace `<mcp-root>` with the absolute path where you cloned OttuKnowledgeMCP.

Restart Claude Code. `/mcp` should show `ottu-knowledge` with 4 tools.

Approve the server on first prompt — Claude Code asks for trust when it sees a new `.mcp.json`. You can also opt in globally via `enableAllProjectMcpServers: true` in your user `~/.claude.json`.

## Adding a new source

Edit `indexer/config.py`:

- New code repo → append to `REPOS` list
- New docs site → append to `DOCS_SITES`
- New internal `.md` / `.docx` / `.xlsx` → drop in `docs_local/` and append its path to `MARKDOWN_FILES`

Then `python cli.py index-code <name>` (or `index-docs`, `index-markdown`).

## CLI reference

| Command | Purpose |
|---------|---------|
| `python cli.py doctor` | Pre-flight: repos present, Ollama OK, LFS status |
| `python cli.py index` | Index code + docs + internal |
| `python cli.py index-code [repo]` | Code only (optionally one repo) |
| `python cli.py index-docs` | Docs sites only |
| `python cli.py index-markdown` | Internal .md/.docx/.xlsx |
| `python cli.py reindex` | Drop everything and rebuild |
| `python cli.py stats` | Chunk counts |
| `python cli.py search "query" [--repo X] [--docs]` | Semantic search |
| `python cli.py freshness` | Stale vs fresh per source |

## Shipping pre-built embeddings to teammates

```bash
git add chroma_db
git commit -m "Update embeddings"
git push  # uploads chroma_db via LFS
```

Teammates: `git lfs pull` after cloning.

## Troubleshooting

- **Ollama not reachable**: run `ollama serve` (Linux/Windows) or restart the menu-bar app (macOS).
- **Model missing**: `ollama pull nomic-embed-text`.
- **Repo not found**: `python cli.py doctor` prints which paths are missing — fix `OTTU_WORKSPACE` / `REPOS` in `indexer/config.py`.
- **Tiny `chroma_db/chroma.sqlite3`**: `git lfs pull`.
- **Windows: `Activate.ps1` blocked**: run PowerShell as admin once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
