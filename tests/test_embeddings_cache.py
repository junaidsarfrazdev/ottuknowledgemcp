"""Cache behavior of OllamaEmbeddings: hit, TTL eviction, capacity eviction."""
from __future__ import annotations

import time
from unittest.mock import patch

from indexer.embeddings import OllamaEmbeddings


def _stub_post_factory(call_log: list[list[str]]):
    """Stub /api/embed: returns one fake vector per input text, logs each call."""

    class FakeResp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def fake_post(url, json, timeout):  # noqa: A002
        texts = json["input"]
        call_log.append(list(texts))
        return FakeResp({"embeddings": [[float(len(t))] for t in texts]})

    return fake_post


def test_query_cache_hits_skip_http():
    log: list[list[str]] = []
    e = OllamaEmbeddings()
    with patch("indexer.embeddings.requests.post", side_effect=_stub_post_factory(log)):
        v1 = e.embed_query("foo")
        v2 = e.embed_query("foo")
    assert v1 == v2
    assert log == [["foo"]], "second call should be a cache hit"


def test_query_cache_evicts_on_ttl_expiry():
    log: list[list[str]] = []
    e = OllamaEmbeddings(cache_ttl_seconds=0)  # everything stale immediately
    with patch("indexer.embeddings.requests.post", side_effect=_stub_post_factory(log)):
        e.embed_query("foo")
        # Force a tiny gap so now - cached[0] > 0
        time.sleep(0.01)
        e.embed_query("foo")
    assert len(log) == 2, "stale entry should be re-fetched"
    assert "foo" in e._cache  # repopulated, not orphaned


def test_query_cache_capacity_eviction_oldest_first():
    log: list[list[str]] = []
    e = OllamaEmbeddings(cache_size=2)
    with patch("indexer.embeddings.requests.post", side_effect=_stub_post_factory(log)):
        e.embed_query("a")
        e.embed_query("b")
        e.embed_query("c")  # evicts "a"
    assert "a" not in e._cache
    assert "b" in e._cache
    assert "c" in e._cache


def test_embed_documents_batches_to_size_cap():
    log: list[list[str]] = []
    e = OllamaEmbeddings()
    with patch("indexer.embeddings.requests.post", side_effect=_stub_post_factory(log)):
        e.embed_documents([f"d{i}" for i in range(7)], batch_size=3)
    # 7 docs at batch=3 → batches of 3, 3, 1
    assert [len(c) for c in log] == [3, 3, 1]


def test_embed_batch_retries_then_raises():
    e = OllamaEmbeddings()

    class Boom:
        def raise_for_status(self):
            import requests as r
            raise r.RequestException("boom")

    with patch("indexer.embeddings.requests.post", return_value=Boom()):
        # No sleeps — patch time.sleep to keep the test fast.
        with patch("indexer.embeddings.time.sleep"):
            try:
                e.embed_batch(["x"], max_retries=3)
                assert False, "should have raised"
            except RuntimeError as err:
                assert "after 3 attempts" in str(err)
