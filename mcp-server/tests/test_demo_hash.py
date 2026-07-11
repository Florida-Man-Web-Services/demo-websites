"""Hash-style demo URLs (floridamanweb.online/<sha256(page)[:12]>/)."""
import hashlib

import businesses
import config


def test_demo_site_hash_matches_file_bytes():
    page = config.GENERATED_SITES_DIR / "ole-barn.html"
    expected = hashlib.sha256(page.read_bytes()).hexdigest()[:12]
    assert businesses.demo_site_hash("ole-barn") == expected


def test_demo_site_hash_missing_page_is_none():
    assert businesses.demo_site_hash("zzzz-no-such-site") is None


def test_hash_mode_overrides_csv_url(monkeypatch):
    monkeypatch.setattr(config, "DEMO_URL_STYLE", "hash")
    monkeypatch.setattr(config, "DEMO_BASE_URL", "https://floridamanweb.online")
    b = businesses.Business(
        name="Ole Barn", demo_url="https://old.example/ole-barn.html"
    )
    page_hash = businesses.demo_site_hash("ole-barn")
    assert b.demo_url == f"https://floridamanweb.online/{page_hash}/"


def test_hash_mode_falls_back_to_slug_url_without_page(monkeypatch):
    monkeypatch.setattr(config, "DEMO_URL_STYLE", "hash")
    b = businesses.Business(name="Zzzz No Such Site")
    assert b.demo_url.endswith("/zzzz-no-such-site.html")


def test_slug_mode_unchanged():
    b = businesses.Business(name="Ole Barn")
    assert b.demo_url.endswith("/ole-barn.html")
