"""Tests for AI 411 free personal pages (opt-in, 24h regen)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import callers
import personal_pages as pp


@pytest.fixture
def stores(tmp_path, monkeypatch):
    callers_path = tmp_path / "callers.json"
    reg = tmp_path / "personal-pages.json"
    pages_dir = tmp_path / "personal-pages"
    pages_dir.mkdir()
    monkeypatch.setattr(callers, "CALLERS_PATH", callers_path)
    monkeypatch.setattr(pp, "PERSONAL_PAGES_REGISTRY", reg)
    monkeypatch.setattr(pp, "PERSONAL_PAGES_DIR", pages_dir)
    monkeypatch.setenv("PERSONAL_PAGES_REGISTRY", str(reg))
    monkeypatch.setenv("PERSONAL_PAGES_DIR", str(pages_dir))
    monkeypatch.setenv("PERSONAL_PAGE_BASE_URL", "https://example.test/me")
    monkeypatch.setenv("PERSONAL_PAGE_TTL_HOURS", "24")
    return {"callers": callers_path, "reg": reg, "dir": pages_dir}


def test_default_off(stores):
    st = pp.get_personal_page_status("+13525550100")
    assert st["ok"] is True
    assert st["enabled"] is False
    assert st["url"] == ""


def test_opt_in_generates_page_without_phone(stores):
    callers.update_profile(
        "+13525550100",
        {
            "preferred_name": "Alex",
            "preferences": {"interests": ["live music", "farmers markets"]},
            "consent": {"memory_ok": True},
        },
    )
    out = pp.opt_in_personal_page(
        "+13525550100",
        preferred_name="Alex",
        headline="GNV nights",
        source="test",
    )
    assert out["ok"] is True
    assert out["enabled"] is True
    assert out["url"].startswith("https://example.test/me/p-")
    slug = out["slug"]
    assert slug.startswith("p-")
    html = pp.render_public(slug)
    assert html is not None
    assert "Alex" in html
    assert "live music" in html
    assert "GNV nights" in html
    assert "+13525550100" not in html
    assert "3525550100" not in html
    # consent flags
    prof = callers.get_profile("+13525550100")
    assert prof["consent"]["personal_page_ok"] is True
    assert prof["consent"]["memory_ok"] is True


def test_opt_out_removes_html(stores):
    pp.opt_in_personal_page("+13525550111", preferred_name="Sam")
    st = pp.get_personal_page_status("+13525550111")
    slug = st["slug"]
    assert (stores["dir"] / f"{slug}.html").exists()
    out = pp.opt_out_personal_page("+13525550111")
    assert out["ok"] is True
    assert out["enabled"] is False
    assert pp.render_public(slug) is None
    assert not (stores["dir"] / f"{slug}.html").exists()


def test_stale_regenerates(stores, monkeypatch):
    out = pp.opt_in_personal_page(
        "+13525550122",
        preferred_name="Jo",
    )
    slug = out["slug"]
    # Force stale timestamp
    with pp._lock:  # noqa: SLF001
        pages = pp._load_registry()  # noqa: SLF001
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).replace(microsecond=0)
        pages["+13525550122"]["last_generated_at"] = old.isoformat()
        pp._save_registry(pages)  # noqa: SLF001
    callers.update_profile(
        "+13525550122",
        {"preferences": {"interests": ["board games"]}},
    )
    html = pp.render_public(slug)
    assert html is not None
    assert "board games" in html
    st = pp.get_personal_page_status("+13525550122")
    assert st["stale"] is False


def test_forget_caller_clears_page(stores):
    pp.opt_in_personal_page("+13525550133", preferred_name="Kit")
    st = pp.get_personal_page_status("+13525550133")
    slug = st["slug"]
    assert slug
    callers.forget_profile("+13525550133")
    assert pp.render_public(slug) is None
    st2 = pp.get_personal_page_status("+13525550133")
    assert st2["enabled"] is False
