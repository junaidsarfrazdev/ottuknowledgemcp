"""Unit tests for search helpers that don't need ChromaDB or Ollama."""
from __future__ import annotations

from indexer import search


def test_dedupe_queries_preserves_order():
    qs = ["foo", "bar", "foo", "  baz  ", "", "bar", "qux"]
    assert search.dedupe_queries(qs) == ["foo", "bar", "baz", "qux"]


def test_dedupe_queries_empty():
    assert search.dedupe_queries([]) == []
    assert search.dedupe_queries(["", "   ", None]) == []  # type: ignore[list-item]


def test_format_code_hit_includes_score_and_truncates():
    meta = {"repo": "jazz_sdk", "path": "src/jazzSDK.ts", "chunk_index": 3, "language": "ts"}
    body = "a" * 2000
    out = search.format_code_hit(meta, body, max_chars=100, score=0.42)
    assert "jazz_sdk/src/jazzSDK.ts" in out
    assert "#3" in out
    assert "d=0.42" in out
    assert "[truncated]" in out
    assert "```ts" in out


def test_format_code_hit_includes_sfc_section():
    meta = {
        "repo": "frontend_public",
        "path": "src/App.vue",
        "chunk_index": 2,
        "sfc_section": "script_ts",
        "language": "vue",
    }
    out = search.format_code_hit(meta, "x", score=0.1)
    assert "script_ts" in out


def test_format_docs_hit_prefers_url_over_path():
    meta = {"title": "Onsite", "url": "https://docs.ottu.net/onsite", "path": "onsite.md"}
    out = search.format_docs_hit(meta, "body", score=0.2)
    assert "https://docs.ottu.net/onsite" in out
    assert "d=0.20" in out
