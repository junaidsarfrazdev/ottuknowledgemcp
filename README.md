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

Pick a workspace directory and clone the repos you want indexed. Example:

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

### Tell the MCP where your repos are

`setup.sh` / `setup.ps1` will prompt for the workspace path and write it to `.env` (gitignored). You can also set it manually:

```bash
# .env
OTTU_WORKSPACE=/absolute/path/to/your/ottu-workspace
```

**Default repo list** (in `indexer/config.py`): `checkout_sdk`, `connect-sdk`, `onsite_playground`, docs site `ottu_docs_site`. Paths are built as `${OTTU_WORKSPACE}/<name>`.

**Custom repo list**: copy `ottu_config.example.json` to `ottu_config.json` (gitignored), edit it, then point `OTTU_CONFIG` at it:

```bash
# .env
OTTU_CONFIG=/absolute/path/to/OttuKnowledgeMCP/ottu_config.json
```

The JSON can override `workspace`, `repos`, `docs_sites`, `markdown_files`. Use `${WORKSPACE}` in paths for substitution. Only sources in the resolved config are indexed.

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

Restart Claude Code. `/mcp` should show `ottu-knowledge` with its tools (`search_multi`, `search_ottu_code`, `search_ottu_docs`, `get_file_chunks`, `find_file`, `list_ottu_sources`, `check_ottu_freshness`).

Approve the server on first prompt — Claude Code asks for trust when it sees a new `.mcp.json`. You can also opt in globally via `enableAllProjectMcpServers: true` in your user `~/.claude.json`.

## Adding a new source

Two options:

**Quickest — use the default list.** If the repo is in `indexer/config.py` `DEFAULT_REPOS`, just clone it into `$OTTU_WORKSPACE` and re-run `python cli.py index`. Paths resolve automatically.

**Custom list — use `ottu_config.json`.** Copy `ottu_config.example.json` → `ottu_config.json`, add/remove entries, set `OTTU_CONFIG` in `.env`:

```json
{
  "workspace": "/Users/you/ottu-workspace",
  "repos": [
    {"name": "jazz_sdk", "path": "${WORKSPACE}/jazz_sdk",
     "description": "Jazz iframe SDK", "collection_name": "ottu_jazz_sdk", "priority": 10}
  ],
  "docs_sites": [...],
  "markdown_files": ["${WORKSPACE}/notes.md"]
}
```

Then: `python cli.py index-code <name>` (or `index-docs`, `index-markdown`).

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
- **Repo not found**: `python cli.py doctor` prints which paths are missing — fix `OTTU_WORKSPACE` in `.env`, or edit your `ottu_config.json` if you're using one.
- **Tiny `chroma_db/chroma.sqlite3`**: `git lfs pull`.
- **Windows: `Activate.ps1` blocked**: run PowerShell as admin once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
