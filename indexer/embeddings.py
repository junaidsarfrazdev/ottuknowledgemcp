"""Direct HTTP client for Ollama embeddings.

Uses /api/embed directly rather than the langchain wrapper for stability and
a small LRU cache on query embeddings.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Iterable

import requests

from . import config


class OllamaEmbeddings:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        cache_size: int = 1000,
        cache_ttl_seconds: int = 3600,
        request_timeout: int = 120,
    ):
        self.base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or config.EMBEDDING_MODEL
        self.cache_size = cache_size
        self.cache_ttl = cache_ttl_seconds
        self.timeout = request_timeout
        self._cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()

    def health_check(self) -> tuple[bool, str]:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=10)
            r.raise_for_status()
            models = {m["name"] for m in r.json().get("models", [])}
            if self.model in models or any(m.startswith(self.model + ":") for m in models):
                return True, f"Ollama OK, model {self.model} present"
            return False, (
                f"Ollama reachable but model '{self.model}' not pulled. "
                f"Run: ollama pull {self.model}"
            )
        except requests.RequestException as e:
            return False, (
                f"Cannot reach Ollama at {self.base_url}: {e}. "
                f"Install from https://ollama.ai or run `ollama serve`."
            )

    def embed_batch(self, texts: list[str], max_retries: int = 3) -> list[list[float]]:
        if not texts:
            return []
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                r = requests.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": texts},
                    timeout=self.timeout,
                )
                r.raise_for_status()
                return r.json()["embeddings"]
            except (requests.RequestException, ValueError, KeyError) as e:
                last_exc = e
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
        raise RuntimeError(f"Ollama embed failed after {max_retries} attempts: {last_exc}")

    def embed_documents(self, texts: list[str], batch_size: int | None = None) -> list[list[float]]:
        batch_size = batch_size or config.EMBED_BATCH_SIZE
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            out.extend(self.embed_batch(texts[i : i + batch_size]))
        return out

    def embed_query(self, text: str) -> list[float]:
        now = time.time()
        cached = self._cache.get(text)
        if cached:
            if now - cached[0] < self.cache_ttl:
                self._cache.move_to_end(text)
                return cached[1]
            # Stale — drop it so we don't keep stale entries in capacity.
            self._cache.pop(text, None)
        vec = self.embed_batch([text])[0]
        self._cache[text] = (now, vec)
        self._cache.move_to_end(text)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return vec


class ChromaEmbeddingFunction:
    """Adapter so ChromaDB collections use our embeddings class."""

    def __init__(self, embeddings: OllamaEmbeddings):
        self._e = embeddings

    def __call__(self, input: Iterable[str]) -> list[list[float]]:
        return self._e.embed_documents(list(input))

    def name(self) -> str:
        return f"ollama-{self._e.model}"
