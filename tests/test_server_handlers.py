"""Tests for the MCP server tool handlers — using small in-memory fakes.

These don't need Chroma or Ollama. We patch the module-level _get_client and
_get_embeddings, plus the config.REPOS / config.DOCS_SITES, then invoke the
async handlers via asyncio.run.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import server
from indexer import config


# ---------- Fakes -----------------------------------------------------------


class FakeCollection:
    def __init__(self, rows: list[tuple[str, dict, str, float]]):
        # rows: list of (id, metadata, document, distance-when-queried)
        self._rows = rows

    def count(self) -> int:
        return len(self._rows)

    def get(self, where=None, include=None, limit=None, offset=None):
        ids, docs, metas = [], [], []
        for rid, meta, doc, _ in self._rows:
            if where:
                # Simple equality match across the keys provided
                if not all(meta.get(k) == v for k, v in where.items()):
                    continue
            ids.append(rid)
            docs.append(doc)
            metas.append(meta)
        if offset is not None:
            ids, docs, metas = ids[offset:], docs[offset:], metas[offset:]
        if limit is not None:
            ids, docs, metas = ids[:limit], docs[:limit], metas[:limit]
        out = {"ids": ids}
        if include is None or "documents" in include:
            out["documents"] = docs
        if include is None or "metadatas" in include:
            out["metadatas"] = metas
        return out

    def query(self, query_embeddings, n_results, include=None):
        # Distance is the row's pre-baked score; we don't actually do vector math.
        sorted_rows = sorted(self._rows, key=lambda r: r[3])[:n_results]
        return {
            "ids": [[r[0] for r in sorted_rows]],
            "documents": [[r[2] for r in sorted_rows]],
            "metadatas": [[r[1] for r in sorted_rows]],
            "distances": [[r[3] for r in sorted_rows]],
        }


class FakeClient:
    def __init__(self, by_name: dict[str, FakeCollection]):
        self._by_name = by_name

    def get_collection(self, name):
        if name in self._by_name:
            return self._by_name[name]
        raise RuntimeError(f"no collection {name}")


class FakeEmb:
    def embed_query(self, text):
        return [0.0]

    def embed_documents(self, texts, batch_size=None):
        return [[0.0] for _ in texts]


# ---------- Helpers ---------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def patched_server(monkeypatch):
    """Wire fakes into the module-level globals + config."""

    state: dict = {"client": None, "embeddings": FakeEmb()}

    def get_client():
        return state["client"]

    def get_emb():
        return state["embeddings"]

    monkeypatch.setattr(server, "_get_client", get_client)
    monkeypatch.setattr(server, "_get_embeddings", get_emb)

    repos = [
        {
            "name": "checkout_sdk",
            "path": "/tmp/checkout_sdk",
            "description": "x",
            "collection_name": "ottu_checkout_sdk",
            "priority": 10,
        }
    ]
    sites = [
        {
            "name": "ottu_docs_site",
            "mode": "crawl",
            "url": "https://docs.example.com",
            "url_base": "https://docs.example.com",
            "collection_name": "ottu_docs",
        }
    ]
    monkeypatch.setattr(config, "REPOS", repos)
    monkeypatch.setattr(config, "DOCS_SITES", sites)
    monkeypatch.setattr(config, "INTERNAL_DOCS_COLLECTION", "ottu_internal_docs")
    monkeypatch.setattr(server.config, "REPOS", repos)
    monkeypatch.setattr(server.config, "DOCS_SITES", sites)

    return state


# ---------- Tests -----------------------------------------------------------


def test_get_file_chunks_falls_back_to_url(patched_server):
    """Crawl-mode docs only carry `url`, no `path` — fallback should resolve them."""
    coll = FakeCollection(
        [
            ("docs:1", {"site": "ottu_docs_site", "url": "https://docs.example.com/onsite", "chunk_index": 0}, "body 0", 0.0),
            ("docs:2", {"site": "ottu_docs_site", "url": "https://docs.example.com/onsite", "chunk_index": 1}, "body 1", 0.0),
            ("docs:3", {"site": "ottu_docs_site", "url": "https://docs.example.com/other", "chunk_index": 0}, "elsewhere", 0.0),
        ]
    )
    patched_server["client"] = FakeClient({"ottu_docs": coll})

    out = _run(
        server._handle_get_file_chunks(
            {"repo": "ottu_docs_site", "path": "https://docs.example.com/onsite"}
        )
    )
    text = out[0].text
    assert "2 chunk(s)" in text or "2 chunk" in text
    assert "body 0" in text
    assert "body 1" in text
    assert "elsewhere" not in text


def test_get_file_chunks_path_match_on_code_repo(patched_server):
    coll = FakeCollection(
        [
            ("code:0", {"repo": "checkout_sdk", "path": "src/foo.js", "chunk_index": 0, "language": "js"}, "let x = 1;", 0.0),
            ("code:1", {"repo": "checkout_sdk", "path": "src/foo.js", "chunk_index": 1, "language": "js"}, "let y = 2;", 0.0),
            ("code:2", {"repo": "checkout_sdk", "path": "src/bar.js", "chunk_index": 0, "language": "js"}, "let z = 3;", 0.0),
        ]
    )
    patched_server["client"] = FakeClient({"ottu_checkout_sdk": coll})

    out = _run(
        server._handle_get_file_chunks({"repo": "checkout_sdk", "path": "src/foo.js"})
    )
    text = out[0].text
    assert "src/foo.js" in text
    assert "let x = 1" in text
    assert "let z = 3" not in text


def test_find_file_paginates_and_truncates(patched_server, monkeypatch):
    """Verify the pagination loop produces matches and surfaces truncation."""
    rows = [
        (f"id:{i}", {"repo": "checkout_sdk", "path": f"src/file_{i}.js"}, "doc", 0.0)
        for i in range(50)
    ]
    rows.append(("id:hit", {"repo": "checkout_sdk", "path": "src/special_target.js"}, "doc", 0.0))
    coll = FakeCollection(rows)
    patched_server["client"] = FakeClient({"ottu_checkout_sdk": coll})

    # Tighten paging so the test exercises the loop.
    monkeypatch.setattr(server, "FIND_FILE_PAGE_SIZE", 10)
    monkeypatch.setattr(server, "MAX_METADATAS_SCAN", 1000)

    out = _run(server._handle_find_file({"pattern": "special_target"}))
    text = out[0].text
    assert "src/special_target.js" in text


def test_find_file_truncation_warning(patched_server, monkeypatch):
    """When scanned > MAX_METADATAS_SCAN, the response includes a truncation note."""
    rows = [
        (f"id:{i}", {"repo": "checkout_sdk", "path": f"src/{i}.js"}, "doc", 0.0)
        for i in range(200)
    ]
    coll = FakeCollection(rows)
    patched_server["client"] = FakeClient({"ottu_checkout_sdk": coll})

    monkeypatch.setattr(server, "FIND_FILE_PAGE_SIZE", 10)
    monkeypatch.setattr(server, "MAX_METADATAS_SCAN", 30)

    out = _run(server._handle_find_file({"pattern": "no-such-pattern"}))
    text = out[0].text
    assert "Scanned the cap" in text or "scan capped" in text


def test_search_multi_attribution_lowest_distance_wins(patched_server, monkeypatch):
    """Same chunk found by two queries — should appear only under the lower-distance query."""

    class TwoQueryColl:
        """Returns different distances depending on which query vector is asked.

        Vectors are scalar lists; we use the value to disambiguate which query.
        """

        def query(self, query_embeddings, n_results, include=None):
            v = query_embeddings[0][0]
            # Same chunk, but query A (vec 0.1) gets dist 0.2; query B (vec 0.9) gets dist 0.4
            if v < 0.5:
                d = 0.2
            else:
                d = 0.4
            return {
                "ids": [["chunk:1"]],
                "documents": [["the body"]],
                "metadatas": [[{"repo": "checkout_sdk", "path": "src/x.js", "chunk_index": 0, "language": "js"}]],
                "distances": [[d]],
            }

        def count(self):
            return 1

    patched_server["client"] = FakeClient({"ottu_checkout_sdk": TwoQueryColl()})

    class TwoVecEmb:
        def embed_documents(self, texts, batch_size=None):
            # First query embeds to a low scalar; second to a high one.
            return [[0.1], [0.9]]

    patched_server["embeddings"] = TwoVecEmb()

    out = _run(
        server._handle_search_multi(
            {"queries": ["query A", "query B"], "sources": "code", "max_distance": 1.0}
        )
    )
    text = out[0].text
    # Query A wins attribution; query B should report no results.
    a_idx = text.find("**query A**")
    b_idx = text.find("**query B**")
    assert a_idx >= 0 and b_idx >= 0
    a_block = text[a_idx:b_idx]
    b_block = text[b_idx:]
    assert "src/x.js" in a_block
    assert "src/x.js" not in b_block
    assert "no results" in b_block


def test_list_sources_reports_chunk_counts(patched_server):
    code = FakeCollection([("c:1", {}, "x", 0.0)] * 3)
    docs = FakeCollection([("d:1", {}, "y", 0.0)] * 5)
    internal = FakeCollection([])
    patched_server["client"] = FakeClient(
        {"ottu_checkout_sdk": code, "ottu_docs": docs, "ottu_internal_docs": internal}
    )
    out = _run(server._handle_list_sources())
    text = out[0].text
    assert "checkout_sdk" in text
    assert "3 chunks" in text
    assert "5 chunks" in text
