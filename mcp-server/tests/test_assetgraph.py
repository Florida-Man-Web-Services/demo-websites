"""Tests for assetgraph.py — closure audit and generation bump."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assetgraph import audit_closure, bump_generation  # noqa: E402


@pytest.fixture()
def clone(tmp_path: Path) -> Path:
    """A minimal closed clone: stem html + entry + lazy chunk."""
    assets = tmp_path / "generated-sites" / "acme" / "assets"
    assets.mkdir(parents=True)
    (assets / "g5-index.js").write_text(
        'import(`./g5-routes.js`);let x=`assets/g5-routes.js`;', encoding="utf-8"
    )
    (assets / "g5-routes.js").write_text(
        'from"./g5-index.js";import(`./g5-lazy.js`)', encoding="utf-8"
    )
    (assets / "g5-lazy.js").write_text("from`./g5-index.js`", encoding="utf-8")
    html = (
        '<html><head>'
        '<link rel="modulepreload" href="acme/assets/g5-index.js"/>'
        '<script type="module" src="acme/assets/g5-index.js"></script>'
        "</head><body>"
        '<script>$_TSR.router={preloads:["acme/assets/g5-routes.js"],'
        'attrs:{src:"acme/assets/g5-index.js"}};</script>'
        "</body></html>"
    )
    (tmp_path / "generated-sites" / "acme.html").write_text(html, encoding="utf-8")
    return tmp_path


def test_audit_closed_graph_passes(clone: Path) -> None:
    html = (clone / "generated-sites" / "acme.html").read_text(encoding="utf-8")
    result = audit_closure(html, clone / "generated-sites" / "acme" / "assets", "acme")
    assert result["ok"] is True
    assert result["missing"] == []
    assert result["escaped"] == []
    assert set(result["js_files"]) == {"g5-index.js", "g5-routes.js", "g5-lazy.js"}


def test_audit_detects_missing_chunk(clone: Path) -> None:
    (clone / "generated-sites" / "acme" / "assets" / "g5-lazy.js").unlink()
    html = (clone / "generated-sites" / "acme.html").read_text(encoding="utf-8")
    result = audit_closure(html, clone / "generated-sites" / "acme" / "assets", "acme")
    assert result["ok"] is False
    assert "g5-lazy.js" in result["missing"]


def test_audit_detects_escaped_reference(clone: Path) -> None:
    assets = clone / "generated-sites" / "acme" / "assets"
    (assets / "g5-lazy.js").write_text('import("/assets/other.js")', encoding="utf-8")
    html = (clone / "generated-sites" / "acme.html").read_text(encoding="utf-8")
    result = audit_closure(html, assets, "acme")
    assert result["ok"] is False
    assert result["escaped"]


def test_bump_generation_renames_and_patches(clone: Path) -> None:
    result = bump_generation("acme", clone, "g6")
    assert result["ok"] is True
    assets = clone / "generated-sites" / "acme" / "assets"
    names = {p.name for p in assets.glob("*.js")}
    assert names == {"g6-g5-index.js", "g6-g5-routes.js", "g6-g5-lazy.js"}
    html = (clone / "generated-sites" / "acme.html").read_text(encoding="utf-8")
    assert "assets/g5-" not in html and "g6-g5-index.js" in html
    for p in assets.glob("*.js"):
        text = p.read_text(encoding="utf-8")
        assert "assets/g5-" not in text
        assert "./g5-" not in text
    # the bumped graph is closed again
    audit = audit_closure(html, assets, "acme")
    assert audit["ok"] is True


def test_bump_refuses_existing_target(clone: Path) -> None:
    assets = clone / "generated-sites" / "acme" / "assets"
    (assets / "g6-g5-index.js").write_text("stale", encoding="utf-8")
    result = bump_generation("acme", clone, "g6")
    assert result["ok"] is False
    assert "already exists" in result["error"]
