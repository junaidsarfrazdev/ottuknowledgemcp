"""Pure-function tests for the MDX stripper and slug builder."""
from __future__ import annotations

from indexer.index_docs import _slug_from_rel, _strip_mdx


def test_strip_mdx_removes_imports_exports():
    src = "import Foo from './foo';\nexport const x = 1;\n# Heading\nbody text"
    out = _strip_mdx(src)
    assert "import" not in out
    assert "export" not in out
    assert "# Heading" in out
    assert "body text" in out


def test_strip_mdx_drops_self_closing_components():
    src = "before <MyComponent prop='x' /> after"
    out = _strip_mdx(src)
    assert "<MyComponent" not in out
    assert "before" in out and "after" in out


def test_strip_mdx_drops_open_close_tags_keeps_inner_text():
    src = "see <Tabs><TabItem>hello</TabItem></Tabs> end"
    out = _strip_mdx(src)
    assert "<Tabs" not in out
    assert "<TabItem" not in out
    assert "</Tabs>" not in out
    assert "hello" in out
    assert "see" in out and "end" in out


def test_strip_mdx_keeps_lowercase_html_tags():
    """Lowercase tags are real HTML, not JSX components — leave them alone."""
    src = "<p>paragraph</p>"
    out = _strip_mdx(src)
    assert "<p>" in out
    assert "</p>" in out


def test_slug_from_rel_strips_md_and_mdx():
    assert _slug_from_rel("guide/intro.md", None) == "/guide/intro"
    assert _slug_from_rel("guide/intro.mdx", None) == "/guide/intro"


def test_slug_from_rel_strips_index():
    assert _slug_from_rel("guide/index.md", None) == "/guide"


def test_slug_from_rel_uses_frontmatter_slug_when_provided():
    assert _slug_from_rel("guide/intro.md", "/custom/path") == "/custom/path"


def test_slug_from_rel_prefixes_leading_slash():
    assert _slug_from_rel("intro.md", None).startswith("/")
    assert _slug_from_rel("intro.md", "no-slash").startswith("/")
