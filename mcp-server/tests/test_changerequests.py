"""Tests for ChangeRequest store + get_site_outline."""

import json

import pytest

import changerequests as cr


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    store = tmp_path / "change-requests.jsonl"
    monkeypatch.setattr(cr, "CHANGE_REQUESTS_PATH", store)
    monkeypatch.delenv("CHANGE_REQUESTS_PATH", raising=False)
    return store


@pytest.fixture
def tiny_site(tmp_path, monkeypatch):
    sites = tmp_path / "generated-sites"
    sites.mkdir()
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Tiny Cafe | Gainesville, FL</title>
  <style>.x { color: red; }</style>
</head>
<body>
  <h1>Welcome to Tiny Cafe</h1>
  <script>var x = "<h2>ignored</h2>";</script>
  <h2>Hours</h2>
  <p>Mon–Fri 9–5</p>
  <h2>Menu</h2>
  <h3>Coffee</h3>
  <h3>Pastries</h3>
</body>
</html>
"""
    (sites / "tiny-cafe.html").write_text(html, encoding="utf-8")
    monkeypatch.setattr(cr, "GENERATED_SITES_DIR", sites)
    monkeypatch.delenv("GENERATED_SITES_DIR", raising=False)
    return sites


def test_create_change_request_appends_jsonl(tmp_store):
    result = cr.create_change_request(
        business_slug="ole-barn",
        summary="Update happy hour hours",
        items=[
            {
                "type": "hours",
                "target": "happy hour",
                "after": "Mon–Fri 4–6pm",
            }
        ],
        caller_phone="+13525550199",
        source="voice",
        confirmation_spoken=True,
        priority="normal",
        call_sid="CA123",
    )
    assert result["created"] is True
    assert result["id"].startswith("cr-")
    assert result["status"] == "pending"
    assert result["item_count"] == 1
    assert result["request"]["call_sid"] == "CA123"

    lines = tmp_store.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["business_slug"] == "ole-barn"
    assert row["status"] == "pending"
    assert row["items"][0]["type"] == "hours"
    assert row["items"][0]["after"] == "Mon–Fri 4–6pm"
    assert row["confirmation_spoken"] is True
    assert row["caller_phone"] == "+13525550199"


def test_create_requires_slug_and_summary(tmp_store):
    assert cr.create_change_request("", "x")["created"] is False
    assert cr.create_change_request("slug", "")["created"] is False
    assert not tmp_store.exists()


def test_create_accepts_items_json_string(tmp_store):
    items = json.dumps([{"type": "copy", "target": "hero", "after": "New headline"}])
    result = cr.create_change_request("tiny-cafe", "New hero", items=items)
    assert result["created"] is True
    assert result["request"]["items"][0]["after"] == "New headline"


def test_create_rejects_bad_items_shape(tmp_store):
    result = cr.create_change_request("x", "y", items="not-json")
    assert result["created"] is False
    assert "items" in result["error"]


def test_list_open_filters_slug_and_status(tmp_store):
    a = cr.create_change_request("ole-barn", "one", items=[])
    b = cr.create_change_request("salty-dog", "two", items=[])
    c = cr.create_change_request("ole-barn", "three", items=[])
    assert a["created"] and b["created"] and c["created"]

    all_open = cr.list_open_change_requests()
    assert all_open["count"] == 3

    ole = cr.list_open_change_requests("ole-barn")
    assert ole["count"] == 2
    assert all(r["business_slug"] == "ole-barn" for r in ole["requests"])

    cancel = cr.cancel_change_request(a["id"])
    assert cancel["cancelled"] is True
    ole2 = cr.list_open_change_requests("ole-barn")
    assert ole2["count"] == 1
    assert ole2["requests"][0]["id"] == c["id"]


def test_cancel_unknown_and_idempotent(tmp_store):
    created = cr.create_change_request("ole-barn", "undo me", items=[])
    rid = created["id"]
    assert cr.cancel_change_request(rid)["cancelled"] is True
    again = cr.cancel_change_request(rid)
    assert again["cancelled"] is True
    assert again.get("already_cancelled") is True
    missing = cr.cancel_change_request("cr-doesnotexist")
    assert missing["cancelled"] is False
    assert "no change request" in missing["error"]


def test_cancel_requires_id(tmp_store):
    assert cr.cancel_change_request("")["cancelled"] is False


def test_env_path_override(tmp_path, monkeypatch):
    env_store = tmp_path / "via-env.jsonl"
    monkeypatch.setenv("CHANGE_REQUESTS_PATH", str(env_store))
    # Module-level default may still be old; _store_path prefers env.
    r = cr.create_change_request("env-biz", "via env", items=[])
    assert r["created"] is True
    assert env_store.exists()
    assert env_store.read_text(encoding="utf-8").count("\n") == 1


def test_get_site_outline_parses_title_and_headings(tiny_site):
    out = cr.get_site_outline("tiny-cafe")
    assert out["found"] is True
    assert out["slug"] == "tiny-cafe"
    assert "Tiny Cafe" in out["title"]
    levels = [h["level"] for h in out["headings"]]
    texts = [h["text"] for h in out["headings"]]
    assert "h1" in levels
    assert "Welcome to Tiny Cafe" in texts
    assert "Hours" in texts
    assert "Menu" in texts
    assert "Coffee" in texts
    # script content must not become a heading
    assert not any("ignored" in t for t in texts)
    assert out["heading_count"] == len(out["headings"])


def test_get_site_outline_missing_and_unsafe(tiny_site):
    miss = cr.get_site_outline("no-such-site")
    assert miss["found"] is False
    assert "no site file" in miss["error"]

    bad = cr.get_site_outline("../etc/passwd")
    assert bad["found"] is False
    assert "invalid" in bad["error"]

    empty = cr.get_site_outline("")
    assert empty["found"] is False


def test_get_site_outline_strips_html_suffix(tiny_site):
    out = cr.get_site_outline("tiny-cafe.html")
    assert out["found"] is True
    assert out["slug"] == "tiny-cafe"
