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


def test_apply_change_request_hours_and_phone(tmp_store, tiny_site):
    # Enrich fixture with a phone line so phone items can match.
    site = tiny_site / "tiny-cafe.html"
    html = site.read_text(encoding="utf-8")
    html = html.replace(
        "<h1>Welcome to Tiny Cafe</h1>",
        '<h1>Welcome to Tiny Cafe</h1>\n  <a href="tel:3525550100">(352) 555-0100</a>',
    )
    site.write_text(html, encoding="utf-8")

    created = cr.create_change_request(
        business_slug="tiny-cafe",
        summary="Update hours and phone",
        items=[
            {
                "type": "hours",
                "target": "Hours",
                "before": "Mon–Fri 9–5",
                "after": "Mon–Sat 8am–8pm",
            },
            {
                "type": "phone",
                "after": "3525551234",
            },
        ],
    )
    assert created["created"] is True
    rid = created["id"]

    result = cr.apply_change_request(rid)
    assert result["applied"] is True
    assert result["status"] == "shipped"
    assert result["changed"] is True
    assert result["applied_count"] >= 1

    new_html = site.read_text(encoding="utf-8")
    assert "Mon–Sat 8am–8pm" in new_html
    assert "Mon–Fri 9–5" not in new_html
    assert "tel:3525551234" in new_html

    loaded = cr.get_change_request(rid)
    assert loaded["found"] is True
    assert loaded["request"]["status"] == "shipped"

    # No longer open
    open_list = cr.list_open_change_requests("tiny-cafe")
    assert open_list["count"] == 0

    # Idempotent-ish: already shipped
    again = cr.apply_change_request(rid)
    assert again["applied"] is True
    assert again.get("already_shipped") is True


def test_apply_unknown_id_speakable(tmp_store, tiny_site):
    result = cr.apply_change_request("cr-doesnotexist")
    assert result["applied"] is False
    assert "error" in result
    assert "no change request" in result["error"]


def test_apply_missing_file_marks_failed(tmp_store, tiny_site):
    created = cr.create_change_request(
        business_slug="no-such-site",
        summary="ghost",
        items=[{"type": "hours", "after": "never"}],
    )
    rid = created["id"]
    result = cr.apply_change_request(rid)
    assert result["applied"] is False
    assert result["status"] == "failed"
    assert "no site file" in result["error"]
    assert cr.get_change_request(rid)["request"]["status"] == "failed"


def test_apply_path_traversal_slug_rejected(tmp_store, tiny_site):
    created = cr.create_change_request(
        business_slug="../etc/passwd",
        summary="evil",
        items=[{"type": "copy", "before": "x", "after": "y"}],
    )
    rid = created["id"]
    result = cr.apply_change_request(rid)
    assert result["applied"] is False
    assert result["status"] == "failed"
    assert "invalid" in result["error"].lower() or "traversal" in result["error"].lower()


def test_apply_empty_id(tmp_store):
    result = cr.apply_change_request("")
    assert result["applied"] is False
    assert "id" in result["error"]


def test_mark_request_shipped_manual(tmp_store):
    created = cr.create_change_request("ole-barn", "manual ship", items=[])
    rid = created["id"]
    out = cr.mark_request_shipped(rid, note="merged externally")
    assert out["shipped"] is True
    assert out["status"] == "shipped"
    assert cr.get_change_request(rid)["request"]["status"] == "shipped"


def test_apply_copy_item(tmp_store, tiny_site):
    created = cr.create_change_request(
        "tiny-cafe",
        "hero copy",
        items=[
            {
                "type": "copy",
                "before": "Welcome to Tiny Cafe",
                "after": "Tiny Cafe welcomes you",
            }
        ],
    )
    result = cr.apply_change_request(created["id"])
    assert result["applied"] is True
    html = (tiny_site / "tiny-cafe.html").read_text(encoding="utf-8")
    assert "Tiny Cafe welcomes you" in html
