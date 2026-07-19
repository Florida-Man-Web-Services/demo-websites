"""Unit tests for surgical HTML transforms (hours/phone/address/copy)."""

import siteedit as se


TINY = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Tiny Cafe | Gainesville, FL</title>
</head>
<body>
  <h1>Welcome to Tiny Cafe</h1>
  <a href="tel:3525550100">(352) 555-0100</a>
  <h2>Hours</h2>
  <p>Mon–Fri 9–5</p>
  <h2>Address</h2>
  <p>100 Main St</p>
  <h2>Menu</h2>
  <p>Coffee and pastries</p>
</body>
</html>
"""


def test_before_after_hours():
    r = se.apply_item(
        TINY,
        {"type": "hours", "before": "Mon–Fri 9–5", "after": "Mon–Sat 8–8"},
    )
    assert r["ok"] is True
    assert "Mon–Sat 8–8" in r["html"]
    assert "Mon–Fri 9–5" not in r["html"]


def test_section_hours_without_before():
    r = se.apply_item(
        TINY,
        {"type": "hours", "target": "Hours", "after": "Daily 7am–7pm"},
    )
    assert r["ok"] is True
    assert "Daily 7am–7pm" in r["html"]


def test_phone_tel_and_display():
    r = se.apply_item(
        TINY,
        {"type": "phone", "after": "3525559999"},
    )
    assert r["ok"] is True
    assert "tel:3525559999" in r["html"]
    assert "(352) 555-9999" in r["html"]


def test_address_before_after():
    r = se.apply_item(
        TINY,
        {
            "type": "address",
            "before": "100 Main St",
            "after": "200 University Ave",
        },
    )
    assert r["ok"] is True
    assert "200 University Ave" in r["html"]


def test_copy_target_replace():
    r = se.apply_item(
        TINY,
        {
            "type": "copy",
            "target": "Welcome to Tiny Cafe",
            "after": "Hello from Tiny Cafe",
        },
    )
    assert r["ok"] is True
    assert "Hello from Tiny Cafe" in r["html"]


def test_unsupported_type_skipped():
    r = se.apply_item(TINY, {"type": "image", "after": "x.png"})
    assert r["ok"] is False
    assert r.get("skipped") is True


def test_apply_items_stops_on_failure():
    r = se.apply_items(
        TINY,
        [
            {"type": "hours", "before": "Mon–Fri 9–5", "after": "open always"},
            {"type": "copy", "before": "NOT IN PAGE", "after": "x"},
        ],
    )
    assert r["ok"] is False
    assert r["applied"] == 1
    # html on failure is original (no partial write at this layer)
    assert r["html"] == TINY


def test_validate_html_ok():
    ok, err = se.validate_html(TINY)
    assert ok is True
    assert err is None


def test_summarize_change():
    s = se.summarize_change(TINY, TINY.replace("9–5", "10–6"))
    assert s["changed"] is True
    assert "summary" in s
