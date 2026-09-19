"""Escape-only public HTML for a published CMS document."""

from __future__ import annotations

import html
from typing import Any

from business_cms_schema import public_projection, validate_document


def _e(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def render_public_html(doc: dict[str, Any], *, origin: str, release_id: str) -> str:
    canonical = validate_document(doc, for_publish=True)
    pub = public_projection(canonical)
    ident = pub["identity"]
    name = ident.get("name") or canonical["slug"]
    seo = pub.get("seo") or {}
    title = seo.get("title") or name
    desc = seo.get("description") or ""
    canonical_path = seo.get("canonical_path") or "/"
    canonical_url = origin.rstrip("/") + "/businesses/" + _e(canonical["slug"]) + "/"
    if canonical_path != "/":
        canonical_url = origin.rstrip("/") + canonical_path
    hours = pub.get("hours") or {}
    faq = pub.get("faq") or []
    groups = (pub.get("services") or {}).get("groups") or []
    forms = pub.get("forms") or {}
    form_action = f"/businesses/{canonical['slug']}/requests"
    robots = "index,follow" if seo.get("indexable", True) else "noindex"

    hour_rows = []
    if hours.get("unknown"):
        hour_rows.append("<p>Hours are not published.</p>")
    else:
        hour_rows.append(f"<p>Timezone: {_e(hours.get('timezone'))}</p>")
        hour_rows.append("<ul>")
        for day, intervals in (hours.get("weekly") or {}).items():
            if not intervals:
                hour_rows.append(f"<li>{_e(day)}: closed</li>")
            else:
                bits = ", ".join(f"{_e(i['open'])}–{_e(i['close'])}" for i in intervals)
                hour_rows.append(f"<li>{_e(day)}: {bits}</li>")
        hour_rows.append("</ul>")

    service_html = ["<ul>"]
    for group in groups:
        service_html.append(f"<li><strong>{_e(group.get('name'))}</strong><ul>")
        for item in group.get("items") or []:
            price = ""
            if item.get("price") is not None:
                price = f" — {_e(item.get('price'))} {_e(item.get('currency') or '')}".rstrip()
            service_html.append(f"<li>{_e(item.get('name'))}{price}</li>")
        service_html.append("</ul></li>")
    service_html.append("</ul>")

    faq_html = ["<dl>"]
    for item in faq:
        faq_html.append(f"<dt>{_e(item.get('question'))}</dt><dd>{_e(item.get('answer'))}</dd>")
    faq_html.append("</dl>")

    nap = []
    if ident.get("address"):
        nap.append(f"<p>{_e(ident['address'])}</p>")
    if ident.get("public_phone"):
        nap.append(f"<p>{_e(ident['public_phone'])}</p>")

    contact_form = ""
    if (forms.get("contact") or {}).get("enabled", True) or (forms.get("message") or {}).get("enabled", True):
        contact_form = f"""
<form method="post" action="{_e(form_action)}">
<input type="hidden" name="kind" value="contact"/>
<label>Message <textarea name="message" required maxlength="2000"></textarea></label>
<button type="submit">Send</button>
</form>"""
    appt_form = ""
    if (forms.get("appointment") or {}).get("enabled", True):
        appt_form = f"""
<form method="post" action="{_e(form_action)}">
<input type="hidden" name="kind" value="appointment"/>
<label>Requested time <input name="requested_time_text" required maxlength="200"/></label>
<label>Notes <textarea name="message" maxlength="2000"></textarea></label>
<button type="submit">Request appointment</button>
</form>
<p>Appointment requests are reviewed by the business. They are not confirmed bookings.</p>"""

    body_blocks = []
    for block in (pub.get("page") or {}).get("blocks") or []:
        if block.get("heading"):
            body_blocks.append(f"<h2>{_e(block['heading'])}</h2>")
        if block.get("body"):
            body_blocks.append(f"<p>{_e(block['body'])}</p>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{_e(title)}</title>
<meta name="description" content="{_e(desc)}"/>
<meta name="robots" content="{_e(robots)}"/>
<link rel="canonical" href="{_e(canonical_url)}"/>
<meta name="fmws-cms-release" content="{_e(release_id)}"/>
</head>
<body>
<header><h1>{_e(name)}</h1>{''.join(nap)}</header>
<main>
{''.join(body_blocks)}
<section id="hours"><h2>Hours</h2>{''.join(hour_rows)}</section>
<section id="services"><h2>Services</h2>{''.join(service_html)}</section>
<section id="faq"><h2>FAQ</h2>{''.join(faq_html)}</section>
<section id="contact"><h2>Contact</h2>{contact_form}{appt_form}</section>
</main>
</body>
</html>
"""


def render_preview_html(doc: dict[str, Any], *, origin: str, draft_revision: str) -> str:
    html_out = render_public_html(doc, origin=origin, release_id=f"preview-{draft_revision}")
    return html_out.replace(
        "<body>",
        "<body><p>Preview — not published.</p>",
        1,
    )
