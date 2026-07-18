"""site_content: demo-page text extraction injected into the call prompt."""

import json

import config
import site_content
from site_content import site_text

PAGE = """<!DOCTYPE html>
<html><head>
<title>39 Nail Salon &ndash; Gainesville</title>
<style>body { color: red; }</style>
<script>console.log("tracking");</script>
</head><body>
<h1>39 Nail Salon</h1>
<p>Polished &amp; perfect. <b>Gel Nails</b> that last for weeks.</p>
<div>Hours: Mon&#8211;Sat 9am&ndash;7pm</div>
<noscript>Enable JS</noscript>
</body></html>"""


def _write_page(tmp_path, monkeypatch, slug="test-biz", html=PAGE):
    monkeypatch.setattr(config, "GENERATED_SITES_DIR", tmp_path)
    (tmp_path / f"{slug}.html").write_text(html, encoding="utf-8")
    return slug


def test_extracts_visible_text_only(tmp_path, monkeypatch):
    slug = _write_page(tmp_path, monkeypatch)
    text = site_text(slug)
    assert "39 Nail Salon" in text
    assert "Gel Nails that last for weeks" in text
    assert "Hours: Mon–Sat 9am–7pm" in text  # entities decoded
    # markup, styles, scripts, and noscript fallbacks never reach the prompt
    for leak in ("<", "color: red", "tracking", "Enable JS", "&amp;"):
        assert leak not in text


def test_missing_page_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GENERATED_SITES_DIR", tmp_path)
    assert site_text("no-such-biz") is None


def test_facts_sidecar_preferred_over_html(tmp_path, monkeypatch):
    slug = _write_page(tmp_path, monkeypatch)
    facts = {
        "summary": "Family nail salon in NW Gainesville.",
        "deals": ["10% off first visit", "Free art with full set"],
        "hours": {"Mon-Sat": "9am-7pm", "Sun": "closed"},
    }
    (tmp_path / f"{slug}.facts.json").write_text(json.dumps(facts))
    text = site_text(slug)
    assert "Family nail salon" in text
    assert "10% off first visit; Free art with full set" in text
    assert "Mon-Sat: 9am-7pm" in text
    assert "Gel Nails" not in text  # sidecar replaces raw page text


def test_corrupt_facts_sidecar_falls_back_to_html(tmp_path, monkeypatch):
    slug = _write_page(tmp_path, monkeypatch)
    (tmp_path / f"{slug}.facts.json").write_text("{not json")
    assert "Gel Nails" in site_text(slug)


def test_long_pages_are_capped(tmp_path, monkeypatch):
    body = "<p>deal</p>" * 20_000
    slug = _write_page(tmp_path, monkeypatch, html=f"<html><body>{body}</body></html>")
    text = site_text(slug)
    assert len(text) <= site_content.MAX_CHARS


def test_system_prompt_includes_site_text(tmp_path, monkeypatch):
    import agent
    from businesses import Business

    slug = _write_page(tmp_path, monkeypatch)
    biz = Business(name="Test Biz", slug=slug, demo_url="https://x.test/d/")
    prompt = agent.system_prompt(biz, "inbound", "+13525550100")
    assert "Gel Nails that last for weeks" in prompt
    assert "WHAT'S ON THEIR DEMO SITE" in prompt


def test_system_prompt_survives_missing_site(tmp_path, monkeypatch):
    import agent
    from businesses import Business

    monkeypatch.setattr(config, "GENERATED_SITES_DIR", tmp_path)
    biz = Business(name="Ghost Biz", demo_url="https://x.test/d/")
    prompt = agent.system_prompt(biz, "inbound", "+13525550100")
    assert "WHAT'S ON THEIR DEMO SITE" not in prompt
    assert "Ghost Biz" in prompt  # rest of the prompt intact
