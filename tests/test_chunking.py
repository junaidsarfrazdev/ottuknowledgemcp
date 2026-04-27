"""Sanity tests for the indexing pipeline.

Focused on the bugs we've hit: duplicate chunk IDs from Vue SFCs and directory
exclusion not pruning before descent.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from indexer import index_code


def _write(dirpath: Path, rel: str, content: str) -> Path:
    p = dirpath / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_vue_chunks_produce_unique_ids():
    """A Vue SFC with template + script + style must produce unique (path, chunk_index) pairs."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(
            Path(tmp),
            "App.vue",
            "<template>\n<div>{{ msg }}</div>\n</template>\n"
            "<script>\nexport default { data() { return { msg: 'hi' } } }\n</script>\n"
            "<style>\n.x { color: red; }\n</style>\n",
        )
        chunks = index_code._chunk_file(path, "App.vue")
        assert chunks, "Expected at least one chunk"
        indices = [m["chunk_index"] for _, m in chunks]
        assert len(set(indices)) == len(indices), f"Duplicate chunk_index values: {indices}"
        # Section labels should still be preserved in metadata
        sections = {m.get("sfc_section") for _, m in chunks if m.get("sfc_section")}
        assert sections & {"template", "script", "style"}, f"No SFC sections found: {sections}"


def test_non_vue_chunk_metadata_has_chunk_index():
    """JS file chunks should carry a chunk_index."""
    with tempfile.TemporaryDirectory() as tmp:
        big_js = "// big js file\n" + ("const x = 1;\n" * 500)
        path = _write(Path(tmp), "big.js", big_js)
        chunks = index_code._chunk_file(path, "big.js")
        assert chunks
        assert all("chunk_index" in m for _, m in chunks)
        indices = [m["chunk_index"] for _, m in chunks]
        assert indices == sorted(indices)
        assert indices[0] == 0


def test_walk_files_prunes_node_modules():
    """os.walk pruning means we never yield paths inside EXCLUDE_DIRS."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "src/app.js", "export const x = 1;")
        _write(root, "node_modules/foo/index.js", "module.exports = 1;")
        _write(root, "dist/build.js", "compiled();")
        _write(root, ".git/hooks/pre-commit", "#!/bin/sh")

        files = list(index_code._walk_files(root))
        rels = {p.relative_to(root).as_posix() for p in files}

        assert "src/app.js" in rels
        for r in rels:
            assert "node_modules" not in r, f"node_modules leaked: {r}"
            assert not r.startswith("dist/"), f"dist/ leaked: {r}"
            assert not r.startswith(".git/"), f".git leaked: {r}"


def test_walk_files_respects_include_extensions():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "a.js", "x")
        _write(root, "b.py", "x")  # not in INCLUDE_EXTENSIONS
        _write(root, "c.log", "x")  # not in INCLUDE_EXTENSIONS
        _write(root, "d.min.js", "x")  # excluded suffix

        rels = {p.name for p in index_code._walk_files(root)}
        assert "a.js" in rels
        assert "b.py" not in rels
        assert "c.log" not in rels
        assert "d.min.js" not in rels
