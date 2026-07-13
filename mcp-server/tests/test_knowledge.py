"""Tests for knowledge.py — local generated-sites knowledge MVP (#47)."""

import os
from pathlib import Path

import pytest

import knowledge


@pytest.fixture
def knowledge_fixtures(tmp_path, monkeypatch):
    """Tiny HTML corpus under tmp_path; points KNOWLEDGE_DIR + clears cache."""
    knowledge.clear_index_cache()
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    (tmp_path / "cool-cafe.html").write_text(
        """<!DOCTYPE html>
<html><head><title>Cool Cafe | Coffee – Gainesville, FL</title></head>
<body>
  <h1>Cool Cafe</h1>
  <p>Artisan espresso and pour-over coffee in downtown Gainesville.</p>
  <p>We serve pastries, breakfast sandwiches, and free Wi-Fi all day.</p>
  <h2>Hours</h2>
  <p>Open Monday through Friday 7am to 6pm. Closed Sunday.</p>
  <h2>Location</h2>
  <p>123 University Avenue, Gainesville, FL 32601. Call 352-555-0100.</p>
</body></html>
""",
        encoding="utf-8",
    )

    (tmp_path / "speedy-plumbing.html").write_text(
        """<!DOCTYPE html>
<html><head><title>Speedy Plumbing | Emergency Plumber – Gainesville</title></head>
<body>
  <h1>Speedy Plumbing</h1>
  <p>24/7 emergency plumbing for Gainesville and Alachua County.</p>
  <p>Drain cleaning, water heater repair, leak detection, and repiping.</p>
  <script>var ignore = "secret";</script>
  <style>.hide{display:none}</style>
</body></html>
""",
        encoding="utf-8",
    )

    (tmp_path / "emptyish.html").write_text(
        """<!DOCTYPE html><html><head><title>Emptyish Biz</title></head>
<body><div></div></body></html>
""",
        encoding="utf-8",
    )

    yield tmp_path
    knowledge.clear_index_cache()


def test_strip_html_title_and_body(knowledge_fixtures):
    html = (knowledge_fixtures / "cool-cafe.html").read_text(encoding="utf-8")
    title, body = knowledge.strip_html(html)
    assert "Cool Cafe" in title
    assert "Artisan espresso" in body
    assert "University Avenue" in body
    assert "secret" not in body  # script stripped (on other file)
    html2 = (knowledge_fixtures / "speedy-plumbing.html").read_text(encoding="utf-8")
    _, body2 = knowledge.strip_html(html2)
    assert "secret" not in body2
    assert "drain cleaning" in body2.lower() or "Drain cleaning" in body2


def test_chunk_text_produces_chunks():
    long = "word " * 300
    chunks = knowledge.chunk_text(long, chunk_words=50)
    assert len(chunks) >= 3
    assert knowledge.chunk_text("") == []
    assert knowledge.chunk_text("short") == ["short"]


def test_search_finds_relevant_business(knowledge_fixtures):
    result = knowledge.search_business_knowledge("espresso coffee Wi-Fi", limit=3)
    assert result["ok"] is True
    assert result["results"]
    assert result["results"][0]["slug"] == "cool-cafe"
    assert "fetched_at" in result["results"][0]
    assert result["scorer"] == "keyword-tfidf-v1"
    assert result["indexed_docs"] >= 2


def test_search_plumbing_query(knowledge_fixtures):
    result = knowledge.search_business_knowledge("emergency plumber water heater")
    assert result["ok"] is True
    assert result["results"]
    assert result["results"][0]["slug"] == "speedy-plumbing"


def test_search_empty_query(knowledge_fixtures):
    result = knowledge.search_business_knowledge("  ")
    assert result["ok"] is False
    assert result["results"] == []
    assert "query" in result["error"].lower() or "required" in result["error"].lower()


def test_search_limit_clamped(knowledge_fixtures):
    result = knowledge.search_business_knowledge("gainesville", limit=100)
    assert result["ok"] is True
    assert len(result["results"]) <= 20


def test_search_missing_dir(monkeypatch, tmp_path):
    knowledge.clear_index_cache()
    missing = tmp_path / "nope"
    monkeypatch.setenv("KNOWLEDGE_DIR", str(missing))
    result = knowledge.search_business_knowledge("anything")
    assert result["ok"] is False
    assert "not found" in result["error"].lower() or "unavailable" in result["error"].lower()
    knowledge.clear_index_cache()


def test_get_business_snapshot(knowledge_fixtures):
    snap = knowledge.get_business_snapshot("cool-cafe")
    assert snap["found"] is True
    assert snap["slug"] == "cool-cafe"
    assert "Cool Cafe" in snap["title"]
    assert "espresso" in snap["text"].lower() or "espresso" in snap["preview"].lower()
    assert snap["fetched_at"]
    assert snap["word_count"] > 0
    assert snap["source"] == "generated-sites"


def test_get_business_snapshot_unknown(knowledge_fixtures):
    snap = knowledge.get_business_snapshot("zzzz-no-such-biz")
    assert snap["found"] is False
    assert "suggestions" in snap


def test_get_business_snapshot_normalizes_slug(knowledge_fixtures):
    snap = knowledge.get_business_snapshot("Cool Cafe")
    assert snap["found"] is True
    assert snap["slug"] == "cool-cafe"


def test_get_business_snapshot_empty_slug(knowledge_fixtures):
    snap = knowledge.get_business_snapshot("")
    assert snap["found"] is False


def test_index_uses_mtime_as_fetched_at(knowledge_fixtures):
    path = knowledge_fixtures / "cool-cafe.html"
    mtime = path.stat().st_mtime
    idx = knowledge.build_index(knowledge_fixtures)
    doc = idx.docs["cool-cafe"]
    # ISO string should parse and be close to file mtime
    from datetime import datetime

    fetched = datetime.fromisoformat(doc.fetched_at)
    assert abs(fetched.timestamp() - mtime) < 2.0


def test_index_cache_invalidates_on_change(knowledge_fixtures):
    idx1 = knowledge.get_index()
    n1 = len(idx1.docs)
    (knowledge_fixtures / "brand-new.html").write_text(
        "<html><head><title>Brand New</title></head>"
        "<body><p>Brand new roofing company Gainesville.</p></body></html>",
        encoding="utf-8",
    )
    idx2 = knowledge.get_index()
    assert len(idx2.docs) == n1 + 1
    assert "brand-new" in idx2.docs


def test_server_tools_call_helpers(monkeypatch, knowledge_fixtures):
    """Async MCP tools return knowledge helper payloads."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", "t")
    import importlib
    import anyio
    import server

    importlib.reload(server)
    search = anyio.run(server.search_business_knowledge, "espresso coffee", 5)
    assert search.get("ok") is True
    assert search["results"]
    snap = anyio.run(server.get_business_snapshot, "cool-cafe")
    assert snap.get("found") is True
