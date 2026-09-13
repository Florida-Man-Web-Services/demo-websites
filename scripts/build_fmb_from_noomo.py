#!/usr/bin/env python3
"""Clone Noomo Awwwards chrome and transfer public FMB copy + images. No fake clients/awards."""
from __future__ import annotations

import re
import shutil
import urllib.request
from pathlib import Path

SRC = Path("/home/noahtjones/Sourcecode/award-winning-websites/noomo-agency/noomoagency.com")
DEST = Path("/home/noahtjones/demo-websites/generated-sites/florida-man-bioscience-site")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36"

FMB_IMGS = {
    "mark.png": "https://flmanbiosci.net/assets/img/mark.png",
    "noah.jpg": "https://flmanbiosci.net/assets/img/noah.jpg",
    "curtis.jpg": "https://flmanbiosci.net/assets/img/curtis.jpg",
    "garrett.jpg": "https://flmanbiosci.net/assets/img/garrett.jpg",
    "michael.jpg": "https://flmanbiosci.net/assets/img/michael.jpg",
    "jacob.jpg": "https://flmanbiosci.net/assets/img/jacob.jpg",
    "tyler.jpg": "https://flmanbiosci.net/assets/img/tyler.jpg",
    "cytogate-bench.png": "https://flmanbiosci.net/assets/img/cytogate-bench.png",
    "cytogate-matrix.png": "https://flmanbiosci.net/assets/img/cytogate-matrix.png",
    "nanodisk.jpg": "https://flmanbiosci.net/assets/img/nanodisk.jpg",
    "neurocreatine.jpg": "https://flmanbiosci.net/assets/img/neurocreatine.jpg",
}

REPLACEMENTS = [
    ("Noomo Agency", "Florida Man Bioscience"),
    ("Noomo", "Florida Man Bioscience"),
    ("noomoagency.com", "floridamanbioscience.com"),
    ("hello@floridamanbioscience.com", "hello@flmanbiosci.net"),  # after domain swap
    ("hello@noomoagency.com", "hello@flmanbiosci.net"),
    ("Digital Storytelling &amp; 3D Website Design Agency | Noomo", "Florida Man Bioscience — Peptide medicine, matched to the genome"),
    ("Digital Storytelling & 3D Website Design Agency | Noomo", "Florida Man Bioscience — Peptide medicine, matched to the genome"),
    ("At Florida Man Bioscience, we create 3D storytelling websites and immersive digital experiences where craft and narrative become one.",
     "PeptOdyssey is what we ship: the PeptidIQ engine and a dossier a licensed clinician can read, plus biomarker follow-up. Design visualization is Stage A. Nanodisk delivery stays research."),
    ("From immersive 3D websites to cinematic brand videos, we let the story dictate the medium—whether that's real-time rendering, editorial design, or interactive experiences.",
     "Decision-support software with a licensed clinician in the loop. Not a medical device. Not a prescription. Not a guarantee of clinical outcomes."),
    ("We partner with brands like Salesforce, AMD, Red Bull and Vogue Business who believe craft makes the difference.",
     "Programs: PeptOdyssey (shipping), next-gen drug design (Stage A), CytoGate (lab software), Discovery Informatics, vector nanodisk (research / IP held out)."),
    ("We craft interactive experiences", "Programs we ship — and what is still research"),
    ("Creating an impact requires a team with sharp minds and strategic", "The people behind Florida Man Bioscience."),
    ("We are a woman owned design agency.", "Founders plus the 2026 Nucleate Activator cohort."),
    ("Florida Man Bioscience Agency was founded by a Ukrainian family based in Los Angeles.",
     "Public marketing roles only. Formal titles and equity are internal governance — not restated here."),
    ("Los Angeles / San Francisco", "Gainesville, Florida"),
    ("Los Angeles, CA", "Gainesville, FL"),
    ("netrix logo", "Florida Man Bioscience mark"),
    (" Our Story ", " Team "),
    (">Our Story<", ">Team<"),
    (" Work ", " Programs "),
    (">Work<", ">Programs<"),
    (" Connect ", " Contact "),
    (">Connect<", ">Contact<"),
    ("Let's work together", "Get in touch"),
    ("Let&#39;s work together", "Get in touch"),
]

# Case titles on work.html → FMB products (order of appearance)
CASE_TITLES = [
    ("Warriors and Coinbase Fan Collectible", "PeptOdyssey"),
    ("Interactive website for the Salesforce Platform", "CytoGate"),
    ("Gen Z Broke the Marketing Funnel, a report for Vogue Business / Archrival", "Next-gen drug design"),
    ("The Power of Digital Storytelling", "Discovery Informatics"),
    ("Immersive 3D Experience for AMD at Cisco Live", "Vector nanodisk"),
    ("Salesforce OEHU Website Redesign", "Neurocreatine"),
    ("Storytelling Website for Dandy Vision", "U4U Privacy"),
    ("Florida Man Bioscience Valentime - Immersive 3D Storytelling About Love", "PeptOdyssey dossier"),
    ("Battalion Website", "PeptidIQ engine"),
    ("Florida Man Bioscience Showcase", "Clinician-readable report"),
    ("Vibrant Wellness Interactive Event Experience", "Biomarker tracker"),
    ("Florida Man Bioscience Labs", "Research software"),
    ("The Future of XR", "Stage A visualization"),
    ("Jasmina Denner Storytelling Website", "Team"),
    ("Florida Man Bioscience Beat - AI-powered Brand Activation Microsite", "Privacy toolkit"),
    ("Intel | ai.io AI-powered Booth Experience for the Olympics 2024, AWS re:Invent and MWC", "Held-out IP"),
    ("OneLine Health Storytelling Website", "Not a medical device"),
    ("Percipio Health AI-driven Interactive Website", "Not a prescription"),
    ("Interactive 3D Configurator", "Software, not a wet lab"),
    ("Middle Finance - Fintech Platform", "Research / optionality"),
    ("Coinbase &amp; LA Clippers Digital Activation", "Nucleate Activator 2026"),
    ("Cadence - OrCAD Website Redesign", "hello@flmanbiosci.net"),
]

HIDE_CSS = """
<style id="fmb-transfer">
.home-awards-list,.home-news,a[href*="labs."],a[href*="insights"],a[href*="Labs"],a[target="_blank"].font-12-dark{display:none!important}
.home-contact-form input[type="radio"]{display:none}
.preloader,#transition{display:none!important;opacity:0!important;pointer-events:none!important;z-index:-1!important;clip-path:none!important}
html,body,.index-page{overflow:auto!important;height:auto!important}
</style>
"""


def fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        dest.write_bytes(r.read())


def copy_chrome() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    keep_dirs = ["_nuxt", "logos", "backgrounds", "icons", "mobileRev"]
    keep_files = [
        "index.html", "work.html", "what-we-do.html", "our-story.html",
        "connect.html", "privacy-policy.html", "careers-connect.html",
        "careers.html", "brand-identity.html", "product-design.html",
        "websites.html", "favicon.png",
    ]
    for d in keep_dirs:
        shutil.copytree(SRC / d, DEST / d)
    for f in keep_files:
        src = SRC / f
        if src.exists():
            shutil.copy2(src, DEST / f)


def download_fmb() -> list[str]:
    img_dir = DEST / "fmb"
    img_dir.mkdir(exist_ok=True)
    local = []
    for name, url in FMB_IMGS.items():
        dest = img_dir / name
        fetch(url, dest)
        local.append(f"fmb/{name}")
        print("img", name, dest.stat().st_size)
    # logos
    shutil.copy2(img_dir / "mark.png", DEST / "logos" / "noomoLogo1.png")
    shutil.copy2(img_dir / "mark.png", DEST / "logos" / "noomoLogo2.png")
    shutil.copy2(img_dir / "mark.png", DEST / "favicon.png")
    # team into mobileRev slots
    team = ["noah.jpg", "curtis.jpg", "garrett.jpg", "michael.jpg"]
    for i, name in enumerate(team, 1):
        slot = DEST / "mobileRev" / f"mrev{i}.png"
        shutil.copy2(img_dir / name, slot)
    return local


def rewrite_html(path: Path, fmb_locals: list[str]) -> None:
    t = path.read_text(encoding="utf-8", errors="replace")
    for a, b in REPLACEMENTS:
        t = t.replace(a, b)
    for a, b in CASE_TITLES:
        t = t.replace(a, b)
    # remote CMS images → local FMB photos
    remote_pat = re.compile(
        r"https://(?:images\.prismic\.io|noomo-website\.cdn\.prismic\.io)/[^\"'\s>]+"
    )
    i = 0

    def sub_img(m):
        nonlocal i
        url = fmb_locals[i % len(fmb_locals)]
        i += 1
        return url

    t = remote_pat.sub(sub_img, t)
    t = re.sub(
        r'<iframe[^>]+vimeo\.com[^>]*>\s*</iframe>',
        f'<img alt="" src="{fmb_locals[0]}" style="width:100%;height:auto"/>',
        t,
        flags=re.I,
    )
    # leftover first-party noomo
    t = t.replace("../labs.noomoagency.com/index.html", "work.html")
    t = t.replace("https://floridamanbioscience.com/_nuxt/", "_nuxt/")
    if "<head>" in t and "fmb-transfer" not in t:
        t = t.replace("<head>", "<head>" + HIDE_CSS, 1)
        t = t.replace("<head ", "<head " + HIDE_CSS, 1) if "fmb-transfer" not in t else t
    path.write_text(t, encoding="utf-8")


def main() -> None:
    print("copy chrome")
    copy_chrome()
    print("download fmb assets")
    locals_ = download_fmb()
    for html in DEST.rglob("*.html"):
        rewrite_html(html, locals_)
        print("rewrote", html.relative_to(DEST))
    leftover = []
    for html in DEST.rglob("*.html"):
        t = html.read_text(encoding="utf-8", errors="replace")
        if "Salesforce" in t or "Red Bull" in t or "noomoagency" in t.lower():
            leftover.append(str(html.relative_to(DEST)))
    print("leftover_brand_files", leftover[:20], "count", len(leftover))
    fetch_missing_nuxt_css()


def fetch_missing_nuxt_css() -> None:
    """Wget did not capture Nuxt CSS chunks that entry.js modulepreloads."""
    nuxt = DEST / "_nuxt"
    on_disk = {p.name for p in nuxt.iterdir()}
    pat = re.compile(r"(index\.[a-f0-9]{8}\.css)")
    need = set()
    for p in nuxt.glob("*.js"):
        t = p.read_text(encoding="utf-8", errors="replace")
        need.update(pat.findall(t))
    for name in sorted(need):
        if name in on_disk:
            continue
        url = f"https://noomoagency.com/_nuxt/{name}"
        dest = nuxt / name
        try:
            fetch(url, dest)
            print("nuxt-css", name, dest.stat().st_size)
        except Exception as e:
            print("nuxt-css-fail", name, type(e).__name__)


if __name__ == "__main__":
    main()
