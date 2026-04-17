# OttuKnowledgeMCP

Local MCP server that gives Claude Code semantic search over Ottu code repos and docs.

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.ai/) with `nomic-embed-text` model pulled
- `git-lfs` (only needed if you'll ship pre-built embeddings to teammates)
- Ottu repos cloned under `/Users/dev/projects/ottu/junaid/` (checkout_sdk, connect-sdk, onsite_playground, docs)

## Install

```bash
brew install ollama git-lfs
ollama pull nomic-embed-text
git lfs install

cd /Users/dev/projects/ottu/OttuKnowledgeMCP
bash setup.sh
```

## Index

```bash
source venv/bin/activate
python cli.py doctor         # verify repos, Ollama, LFS
python cli.py index          # code + docs + internal files
python cli.py stats          # chunk counts per collection
python cli.py search "3DS tokenization"
```

## Claude Code config

Create `/Users/dev/projects/ottu/junaid/.mcp.json` (project-scoped):

```json
{
  "mcpServers": {
    "ottu-knowledge": {
      "type": "stdio",
      "command": "/Users/dev/projects/ottu/OttuKnowledgeMCP/venv/bin/python",
      "args": ["/Users/dev/projects/ottu/OttuKnowledgeMCP/server.py"]
    }
  }
}
```

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

- **Ollama not reachable**: `ollama serve` (or restart the menu-bar app).
- **Model missing**: `ollama pull nomic-embed-text`.
- **Repo not found**: `python cli.py doctor` prints exact clone commands.
- **Tiny `chroma_db/chroma.sqlite3`**: `git lfs pull`.
