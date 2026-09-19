#!/usr/bin/env python3
"""Build AI 411 v3 one-job ads: copy pack, PNGs, INDEX, review.html."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path("/home/noahtjones/demo-websites/marketing/ai411-ads-v2/outputs/2026-08-15")
TPL = Path(
    "/home/noahtjones/.hermes/profiles/arete-cos/skills/marketing/ad-creative/assets/creative-review-template.html"
)
DATA = json.loads((ROOT / "ads.json").read_text())
SLUGS = {a["id"]: a["slug"] for a in DATA["ads"]}


def nchars(s: str) -> int:
    return len(s)


def first_line_block(primary: str) -> str:
    # first 125 visible before See more — use first paragraph
    first = primary.split("\n\n")[0]
    return first.replace("\n", " ")


def write_copy() -> None:
    lines = [
        "# AI 411 — twelve Meta ads (one job each)",
        "",
        f"Destination: {DATA['destination']}",
        "Specs: primary first ~125 visible · headline ≤40 (aim 27) · description ≤30",
        "No invented stats. Shop offer never appears on consumer ads.",
        "",
    ]
    for a in DATA["ads"]:
        hook = first_line_block(a["primary"])
        lines += [
            f"## {a['id']} · {a['angle']}",
            f"Template: {a['template']} · Wave {a['wave']} · Offer: {a['offer']}",
            f"Awareness: {a['awareness']}",
            "",
            f"**Primary** ({nchars(a['primary'])} chars · hook {nchars(hook)})",
            "```",
            a["primary"],
            "```",
            "",
            f"**Headline:** {a['headline']} ({nchars(a['headline'])})",
            f"**Description:** {a['description']} ({nchars(a['description'])})",
            f"**On-image:** {a['overlay'].replace(chr(10), ' / ')}",
            f"**CTA:** {a['cta']}",
            f"**Grounded in:** {a['grounding']}",
            "",
        ]
        flags = []
        if nchars(a["headline"]) > 40:
            flags.append("HEADLINE OVER 40")
        if nchars(a["description"]) > 30:
            flags.append("DESC OVER 30")
        if nchars(hook) > 125:
            flags.append(f"HOOK OVER 125 ({nchars(hook)})")
        if flags:
            lines.append("**SPEC FAIL:** " + "; ".join(flags))
            lines.append("")
    (ROOT / "COPY.md").write_text("\n".join(lines) + "\n")
    print("wrote COPY.md")


def write_index() -> None:
    lines = [
        "# INDEX — 2026-08-15 AI 411 twelve angles",
        "",
        "| # | File | Angle | Template | Wave |",
        "|---|------|-------|----------|------|",
    ]
    for a in DATA["ads"]:
        fn = f"{a['id']}-{a['slug']}.png"
        lines.append(
            f"| {a['id']} | `images/{fn}` | {a['angle']} | {a['template']} | {a['wave']} |"
        )
    lines += [
        "",
        "Scan in 2 minutes. Pick Wave 1 (01, 02, 04, 07) first.",
        "Shop (11) is a separate campaign. Trust (12) is retarget only.",
        "",
    ]
    (ROOT / "INDEX.md").write_text("\n".join(lines) + "\n")
    concepts = ROOT / "concepts"
    concepts.mkdir(exist_ok=True)
    for a in DATA["ads"]:
        md = f"""## Concept {a['id']}: {a['template']}

**Headline**: {a['headline']}
**Body**: {a['primary'].splitlines()[0]}
**Visual**: See `images/{a['id']}-{a['slug']}.png` (HTML artboard, exact type).
**Image prompt**: n/a — type-built static (Imagine skill: exact text via code).
**Grounded in**: {a['grounding']}
"""
        (concepts / f"{a['id']}-{a['slug']}.md").write_text(md)
    print("wrote INDEX.md + concepts/")


def screenshot() -> None:
    img_dir = ROOT / "images"
    img_dir.mkdir(exist_ok=True)
    html = (ROOT / "artboards.html").resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/home/noahtjones/.local/bin/chromium",
            args=["--no-sandbox", "--disable-gpu"],
        )
        page = browser.new_page(
            viewport={"width": 1080, "height": 1350},
            device_scale_factor=1,
        )
        page.goto(html, wait_until="load")
        page.wait_for_timeout(200)
        for a in DATA["ads"]:
            dest = img_dir / f"{a['id']}-{a['slug']}.png"
            el = page.locator(f"#ad-{a['id']}")
            el.scroll_into_view_if_needed()
            el.screenshot(path=str(dest), type="png")
            print(f"shot {dest.name} {dest.stat().st_size}")
        browser.close()


def write_review() -> None:
    raw = TPL.read_text()
    start = raw.index('<script type="application/json" id="review-data">')
    end = raw.index("</script>", start)
    concepts = []
    for a in DATA["ads"]:
        img = f"images/{a['id']}-{a['slug']}.png"
        concepts.append(
            {
                "name": a["angle"],
                "tagline": a["overlay"].replace("\n", " "),
                "handles": [
                    {
                        "name": "ai411gnv",
                        "partner": "Florida Man Web Services",
                        "initials": "411",
                    }
                ],
                "frames": [
                    {
                        "label": "Hook",
                        "prompt": a["overlay"].replace("\n", " "),
                        "image": img,
                        "headline": a["headline"],
                        "headlineTheme": "dark",
                    }
                ],
                "headlines": [a["headline"]],
                "primaryText": a["primary"],
                "destination": {
                    "url": "ai411.floridamanweb.online",
                    "cta": a["cta"],
                    "offer": "Call or request callback",
                },
                "grounding": a["grounding"],
            }
        )
    payload = {
        "project": {
            "brand": "AI 411",
            "agency": "Florida Man Web Services",
            "date": "2026-08-15",
            "note": "Twelve one-job angles. Do not stack features.",
        },
        "platforms": ["instagram", "facebook"],
        "concepts": concepts,
    }
    blob = json.dumps(payload, ensure_ascii=True, indent=2)
    blob = blob.replace("<", "\\u003c")
    new = raw[:start] + '<script type="application/json" id="review-data">\n' + blob + "\n" + raw[end:]
    (ROOT / "review.html").write_text(new)
    print("wrote review.html")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    write_copy()
    write_index()
    screenshot()
    write_review()
    print("done", ROOT)


if __name__ == "__main__":
    main()
