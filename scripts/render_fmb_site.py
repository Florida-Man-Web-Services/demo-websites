#!/usr/bin/env python3
"""Render FMB canonical dossier pages into generated-sites/. Public copy only."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "generated-sites"
CANON = "https://floridamanbioscience.com"
EMAIL = "hello@flmanbiosci.net"

PAGES = [
    ("florida-man-bioscience", "/", 0, "Home"),
    ("fmb-team", "/team", 1, "Team"),
    ("fmb-peptodyssey", "/peptodyssey", 1, "PeptOdyssey"),
    ("fmb-peptodyssey-privacy", "/peptodyssey/privacy", 2, "Privacy"),
    ("fmb-u4u", "/products/u4u", 2, "PeptOdyssey"),
    ("fmb-u4u-privacy", "/products/u4u-privacy", 2, "U4U Privacy"),
    ("fmb-cytogate", "/products/cytogate", 2, "CytoGate"),
    ("fmb-discovery-informatics", "/products/discovery-informatics", 2, "Discovery Informatics"),
    ("fmb-next-gen-drug-development", "/products/next-gen-drug-development", 2, "Drug design"),
    ("fmb-vector-nanodisk", "/products/vector-nanodisk", 2, "Nanodisk"),
    ("fmb-neurocreatine", "/products/neurocreatine", 2, "Neurocreatine"),
]

CSS = """
:root{--paper:#ece8e1;--ink:#1c1915;--mute:rgba(28,25,21,.62);--rule:rgba(28,25,21,.22);--cta:#1c1915}
*{box-sizing:border-box}
html,body{margin:0;background:var(--paper);color:var(--ink);font-family:'Source Sans 3',system-ui,sans-serif}
body{min-height:100vh;line-height:1.5}
a{color:inherit}
.skip{position:absolute;left:-9999px}
.skip:focus{left:1rem;top:1rem;z-index:9;background:var(--ink);color:var(--paper);padding:.5rem .8rem}
.wrap{max-width:44rem;margin:0 auto;padding:0 1.25rem}
.nav{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:1.1rem 0 1.25rem;border-bottom:1px solid var(--rule)}
.brand{display:flex;align-items:center;gap:.6rem;text-decoration:none;font-family:Fraunces,serif;font-size:1.05rem}
.brand img{width:28px;height:28px;object-fit:contain}
.nav-links{display:flex;flex-wrap:wrap;gap:.75rem 1.1rem;font-size:.92rem}
.nav-links a{text-decoration:none;opacity:.75}
.nav-links a[aria-current="page"],.nav-links a:hover{opacity:1}
main{padding:2.4rem 0 3.5rem}
.kicker{font-size:.92rem;opacity:.7;margin:0 0 .6rem}
h1,h2,h3{font-family:Fraunces,serif;font-weight:500;line-height:1.15;text-wrap:pretty}
h1{font-size:clamp(1.8rem,4vw,2.6rem);margin:0 0 .8rem}
h2{font-size:1.45rem;margin:2.2rem 0 .6rem}
h3{font-size:1.12rem;margin:0 0 .35rem}
.lead{font-size:1.08rem;max-width:38em}
.mute,.lead{color:var(--ink)}
.lead{opacity:.84}
hr.rule{border:0;border-top:1px solid var(--rule);margin:1.4rem 0}
.status{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;font-size:.95rem}
.status b{display:block;font-family:Fraunces,serif;font-weight:500}
.status span{display:block;opacity:.65;font-size:.88rem;margin-top:.2rem}
@media (max-width:640px){.status{grid-template-columns:1fr}}
.cta{display:inline-block;margin-top:1.1rem;padding:.55rem 1rem;background:var(--cta);color:var(--paper);text-decoration:none}
.cta.ghost{background:transparent;color:var(--ink);border:1px solid var(--rule);margin-left:.4rem}
.stack{display:grid;gap:1.15rem}
.item{padding-top:1rem;border-top:1px solid var(--rule)}
.item:first-child{border-top:0;padding-top:0}
ul.plain{margin:.4rem 0 0;padding-left:1.1rem}
footer{border-top:1px solid var(--rule);padding:1.4rem 0 2.2rem;font-size:.9rem;opacity:.75}
footer a{text-decoration:underline}
:focus-visible{outline:2px solid var(--ink);outline-offset:3px}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""


def root_prefix(depth: int) -> str:
    return "../" * depth


def href(depth: int, path: str) -> str:
    if path == "/":
        return root_prefix(depth) or "./"
    return f"{root_prefix(depth)}{path.lstrip('/')}/"


def chrome(depth: int, active_path: str, title: str, description: str, body: str) -> str:
    r = root_prefix(depth)
    mark = f"{r}florida-man-bioscience/mark.png"
    canon = f"{CANON}{active_path if active_path != '/' else '/'}"
    nav = [
        ("/", "Home"),
        ("/peptodyssey", "PeptOdyssey"),
        ("/products/cytogate", "Programs"),
        ("/team", "Team"),
    ]
    links = []
    for path, label in nav:
        cur = ' aria-current="page"' if path == active_path or (
            label == "Programs" and active_path.startswith("/products")
        ) else ""
        links.append(f'<a href="{href(depth, path)}"{cur}>{label}</a>')
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<meta name="description" content="{description}"/>
<link rel="canonical" href="{canon}"/>
<meta property="og:title" content="{title}"/>
<meta property="og:description" content="{description}"/>
<meta property="og:url" content="{canon}"/>
<meta property="og:image" content="{CANON}/florida-man-bioscience/mark.png"/>
<link rel="icon" type="image/png" href="{mark}"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet"/>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="wrap">
<header class="nav">
  <a class="brand" href="{href(depth, '/')}">
    <img src="{mark}" alt=""/>
    Florida Man Bioscience
  </a>
  <nav class="nav-links">{''.join(links)}</nav>
</header>
<main id="main">
{body}
</main>
<footer>
  <p>Florida Man Bioscience · <a href="mailto:{EMAIL}">{EMAIL}</a></p>
  <p>Decision-support and research software. Not a medical device. Not a prescription. Not a guarantee of clinical outcomes.</p>
</footer>
</div>
</body>
</html>
"""


def page_home() -> str:
    return """
<p class="kicker">Shipping product</p>
<h1>Peptide medicine, matched to the genome.</h1>
<p class="lead" id="copy-lead">PeptOdyssey is what we ship: the PeptidIQ engine and a dossier a licensed clinician can read, plus biomarker follow-up. Design visualization is Stage A. Nanodisk delivery stays research.</p>
<p class="lead">Decision-support software with a licensed clinician in the loop. Not a medical device. Not a prescription. Not a guarantee of clinical outcomes.</p>
<hr class="rule"/>
<div class="status" id="status">
  <div><b>PeptOdyssey</b><span>Shipping</span></div>
  <div><b>Drug design</b><span>Stage A</span></div>
  <div><b>Vector nanodisk</b><span>Research · IP held out</span></div>
</div>
<a class="cta" href="peptodyssey/">Open PeptOdyssey</a>
<h2>Detect, design, deliver</h2>
<p>Company vision from the working notes. Internally the software loop is still Read → Predict → Report → Track. Delivery is a separate research program, not part of that loop.</p>
<div class="stack">
  <div class="item"><h3>Detect</h3><p>PeptidIQ annotates genome files and measured signals. PeptOdyssey turns that into a clinician-readable dossier. Decision support, not a protocol.</p></div>
  <div class="item"><h3>Design</h3><p>Next-gen drug design and protein visualization. See the molecule before the bench. A software surface today; not a wet-lab or therapeutic claim.</p></div>
  <div class="item"><h3>Deliver</h3><p>MSP / vector nanodisk work on getting payloads where they are needed. Research only. Institutional IP stays held out until cleared.</p></div>
</div>
<h2>Programs</h2>
<div class="stack">
  <div class="item"><h3><a href="peptodyssey/">PeptOdyssey</a></h3><p>Genome-aware peptide decision support for longevity, functional, and concierge clinics. A licensed clinician reads the dossier with the patient.</p></div>
  <div class="item"><h3><a href="products/cytogate/">CytoGate</a></h3><p>Compensation and QC desktop (CytoCrunch) for FCS sessions. Lab software, not the company thesis.</p></div>
  <div class="item"><h3><a href="products/next-gen-drug-development/">Next-gen drug design</a></h3><p>Stage A design and visualization for peptide and protein work — software, not a wet lab.</p></div>
  <div class="item"><h3><a href="products/discovery-informatics/">Discovery Informatics</a></h3><p>A jailed science-agent OS. Research software. Not a medical device.</p></div>
  <div class="item"><h3><a href="products/vector-nanodisk/">Vector nanodisk</a></h3><p>Research only. Institutional IP stays held out until cleared.</p></div>
  <div class="item"><h3><a href="products/neurocreatine/">Neurocreatine</a></h3><p>Parked early discovery — not a lead program.</p></div>
</div>
"""


def product(tagline, lead, pillars, status, disclaimer, extra="") -> str:
    items = "".join(f'<div class="item"><h3>{t}</h3><p>{b}</p></div>' for t, b in pillars)
    return f"""
<p class="kicker">{status}</p>
<h1>{tagline}</h1>
<p class="lead" id="copy-lead">{lead}</p>
<hr class="rule"/>
<div class="stack">{items}</div>
{extra}
<p class="lead">{disclaimer}</p>
<a class="cta" href="mailto:{EMAIL}">Contact the team</a>
"""


def page_team() -> str:
    founders = [
        ("Noah T. Jones", "Founder & CEO", "Builds the engine and app foundations. Bioinformatics, pipeline architecture, and company operations."),
        ("Curtis Dearing", "Co-founder · PeptOdyssey", "Builds the PeptOdyssey engine and the clinician-facing product surface."),
        ("Garrett Knotts", "Co-founder · Omics", "Calcium-sensing and transmembrane proteins; metabolism and mitochondria-focused science."),
        ("Michael MacNair", "Co-founder · Structural biology", "Structural biochemistry and the VR structural-biochemistry platform."),
        ("Jacob Davis", "Founder · Bioinformatics", "Bioinformatics and immunology."),
        ("Tyler Kopf", "Founder · Clinical & operations", "Clinical and operations; skunkworks and program execution."),
    ]
    contrib = [
        ("Sasank Desaraju", "Clinical anchor · PeptOdyssey contributor", "MD/PhD student, University of Florida. Clinical link and PeptOdyssey engine contributor."),
        ("Kayla Schwartz", "Safety & contraindications", "MD-PhD student, University of Miami. Safety / contraindication layer for genotype-aware peptide protocols."),
        ("Rocky Truong", "Oncology genetics · VR structural biochemistry", "Post-doc, Moffitt Cancer Center. Oncology genetics advisor; VR structural biochemistry project lead."),
        ("Min Young Park", "Metabolism · MitoFocus", "Metabolism, adipose biology, and nutrition. Nucleate Activator contributor."),
        ("Delaney Ding", "Clinical & translational strategy", "Clinical and public-health researcher guiding clinical and translational strategy."),
        ("Christopher Marais", "Activator contributor", "Joined the 2026 Nucleate Activator cohort mid-program."),
        ("Hampton Copeland", "Engineering lead", "Graduate student, MTSU. Engineering lead for platform and infrastructure."),
        ("Jeran Fox", "Marketing & growth", "GTM and growth; channel and go-to-market execution."),
        ("Ty Dearing", "Contributor", "Non-founder contributor supporting company operations and growth."),
    ]
    advisors = [
        ("Giuseppina Sannino", "Commercialization advisor", "Founder & CEO, Auralis Biotech. Commercialization and strategic partnership advisor."),
    ]

    def block(people):
        return "".join(f'<div class="item"><h3>{n}</h3><p>{r}</p><p class="lead">{b}</p></div>' for n, r, b in people)

    return f"""
<p class="kicker">Company</p>
<h1>The people behind Florida Man Bioscience.</h1>
<p class="lead" id="copy-lead">Founders, operators, and the 2026 Nucleate Activator cohort — scientists and builders who refuse to choose between rigor and speed. Public marketing roles only. Formal titles and equity are internal governance — not restated here.</p>
<h2>Founding team</h2>
<p>Public roles describe the work. Skills, not equity — no invented C-suite titles and no unit counts on this page.</p>
<div class="stack">{block(founders)}</div>
<h2>Nucleate Activator contributors</h2>
<div class="stack">{block(contrib)}</div>
<h2>Advisors</h2>
<div class="stack">{block(advisors)}</div>
"""


def page_privacy() -> str:
    return """
<p class="kicker">PeptOdyssey · iOS research app</p>
<h1>Privacy Policy</h1>
<p class="lead">Last updated: 16 July 2026 · Version 1</p>
<p class="lead" id="copy-lead"><strong>Counsel review open.</strong> This policy is an operational draft for TestFlight and App Store disclosure. It is not legal advice. Outside counsel review remains an open obligation before broad external distribution or regulated study use. IRB / informed-consent documents, where required for a formal study, are separate.</p>
<h2>Who we are</h2>
<p>PeptOdyssey (“the app”) is operated by <strong>Florida Man Bioscience</strong> (“we”, “us”). Contact for privacy and data requests: <a href="mailto:hello@flmanbiosci.net">hello@flmanbiosci.net</a>.</p>
<h2>What this app does</h2>
<p>PeptOdyssey collects health and activity data from Apple Health (HealthKit), with your permission, and uploads it to our research backend so we can study longitudinal biomarkers and run a research / product measurement loop for peptide-related programs. The app is a data-collection tool for consented participants. It is not a medical device and does not diagnose, treat, cure, or prevent any disease.</p>
<h2>What we collect</h2>
<p>With your explicit Health permission, the app may read a broad range of HealthKit data types (only those you grant in the system permission sheet), including:</p>
<ul class="plain">
<li><strong>Activity &amp; fitness</strong> — steps, distance, energy, workouts, exercise/stand/move time, VO<sub>2</sub> max, mobility and cycling metrics</li>
<li><strong>Heart &amp; vitals</strong> — heart rate, HRV, blood pressure, respiratory rate, oxygen saturation, body temperature, blood glucose, related events</li>
<li><strong>Sleep &amp; mindfulness</strong> — sleep analysis, mindful sessions, sleep-related vitals where available</li>
<li><strong>Nutrition</strong> — energy, macronutrients, vitamins, minerals, water, caffeine</li>
<li><strong>Body measurements &amp; characteristics</strong> — height, weight, body composition; biological sex, date of birth, blood type, and similar characteristics where recorded</li>
<li><strong>Reproductive health &amp; symptoms</strong> — category data you have logged in Health</li>
<li><strong>Other logged Health samples</strong> — e.g. audio exposure, UV, inhaler usage, hygiene events, vision prescription (per-object authorization)</li>
</ul>
<p>Clinical medical records and very high-volume series such as full ECG waveforms are not in the v1 collection set.</p>
<p>We also process limited technical metadata on each sample (source app, device name, timestamps) and an account / subject identifier tied to enrollment (device token / subject id used to associate uploads).</p>
<h2>How it is collected</h2>
<p>Collection is continuous and may run in the background using Apple’s HealthKit background delivery so data stays current. New and updated samples are queued on-device and uploaded over HTTPS/TLS to our backend, authenticated with a per-device enrollment token. Nothing is read from HealthKit until you accept the in-app consent screen and grant Health access.</p>
<h2>Legal basis &amp; consent</h2>
<p>We collect and process this data only after you provide informed consent in the app. You may withdraw consent at any time (see Your choices).</p>
<h2>How we use it</h2>
<p>We use your health data solely for the research and product-measurement purpose described above and to operate the app (enrollment, upload, sync status). <strong>We do not sell your data.</strong> We do not use it for advertising or for tracking you across other companies’ apps or websites.</p>
<h2>Storage, security, and retention</h2>
<ul class="plain">
<li>Data is stored in a PostgreSQL database on our research backend (reachable via https://peptodyssey.flmanbiosci.net/api/v1).</li>
<li><strong>In transit:</strong> encrypted via HTTPS/TLS.</li>
<li><strong>At rest:</strong> hosted on infrastructure with disk / volume encryption as provided by the managed host; access restricted to authorized study and engineering personnel.</li>
<li><strong>Retention:</strong> for the duration of active research participation and up to three (3) years afterward for analysis continuity, unless you request earlier deletion or a study protocol requires a different period. We may retain de-identified aggregates longer.</li>
</ul>
<h2>Sharing</h2>
<p>We share data only with service providers needed to run the study and backend (e.g. cloud hosting), under appropriate access controls. We do not sell or rent your data. We may disclose information if required by law.</p>
<h2>Your choices</h2>
<ul class="plain">
<li><strong>Withdraw consent</strong> in the app (Privacy &amp; Consent → Withdraw Consent). This stops future collection.</li>
<li><strong>Revoke Health access</strong> any time in iOS Settings → Health → Data Access &amp; Devices → PeptOdyssey.</li>
<li><strong>Request deletion</strong> of previously uploaded data by emailing <a href="mailto:hello@flmanbiosci.net">hello@flmanbiosci.net</a> with identifiers that let us locate your records.</li>
</ul>
<h2>Children</h2>
<p>PeptOdyssey is not intended for anyone under 18. We do not knowingly collect data from children under 18.</p>
<h2>Changes</h2>
<p>We will update this policy and the in-app consent materials when practices change. Material changes will be reflected by a new “Last updated” date on this page and, where appropriate, an updated consent gate in the app.</p>
<h2>Contact</h2>
<p>Questions or data requests: <a href="mailto:hello@flmanbiosci.net">hello@flmanbiosci.net</a><br/>Florida Man Bioscience · flmanbiosci.net</p>
"""


BODIES = {
    "florida-man-bioscience": page_home,
    "fmb-team": page_team,
    "fmb-peptodyssey": lambda: product(
        "Peptide options, matched to the genome.",
        "PeptOdyssey is Florida Man Bioscience’s shipping platform — engine, clinician-readable dossier, iOS research capture, and tracker. It turns a genetic file into a structured options set. A licensed clinician stays in the loop. The dossier is not a prescription.",
        [
            ("Genome-aware options", "Turn a genetic file into peptide-relevant context — structured so a licensed clinician can actually read it."),
            ("Dossier, not a prescription", "Priorities, cautions, and open questions. Software does not prescribe. A clinician stays in the loop."),
            ("A loop that learns", "Pair the first read with follow-up signals so the picture can refine — still under clinical judgment."),
        ],
        "Shipping platform",
        "Research and decision-support software. Not a medical device. Not a prescription. Not intended to diagnose, treat, cure, or prevent disease. Does not replace clinical judgment or genetic counseling.",
        extra='<p><a href="privacy/">Privacy policy</a></p>',
    ),
    "fmb-peptodyssey-privacy": page_privacy,
    "fmb-u4u": lambda: product(
        "Peptide options, matched to the genome.",
        "Shipping platform: genome → structured dossier → follow-up, with a licensed clinician in the loop. A dossier, not a prescription.",
        [
            ("Genome-aware options", "Turn a genetic file into peptide-relevant context — structured so a licensed clinician can actually read it."),
            ("Dossier, not a prescription", "Priorities, cautions, and open questions. Software does not prescribe."),
            ("A loop that learns", "Pair the first read with follow-up signals so the picture can refine — still under clinical judgment."),
        ],
        "Flagship platform · Decision support",
        "Research and decision-support software. Not a medical device. Not a prescription.",
    ),
    "fmb-u4u-privacy": lambda: product(
        "Work the file on hardware you control.",
        "u4u-privacy is a local-first consumer genomics toolkit. Variant work and related utilities run on Windows, macOS, or Linux you own. The starting assumption is not “upload everything.”",
        [
            ("Your machine first", "Pipelines are designed to run locally. Network is an opt-in (for example a public reference), not the default hop."),
            ("Files you already have", "Consumer genotype exports and common genomic formats are in scope — without making a vendor the first stop."),
            ("A different trust model", "PeptOdyssey is the clinic-facing dossier. This toolkit is for when the file should stay put."),
        ],
        "Genomics toolkit · Local-first",
        "Consumer and research utilities. Not a medical device. Not a diagnostic service. Does not replace clinical genetic counseling.",
    ),
    "fmb-cytogate": lambda: product(
        "See the matrix. Gate with intent.",
        "CytoGate is Florida Man Bioscience’s flow-cytometry product. CytoCrunch is the desktop bench — compensation and QC you can inspect, plus an optional Assistant that proposes gates and experiment-design checklists. You Apply. Auto-gate still works if the Assistant is off.",
        [
            ("Compensation you can inspect", "Matrix, spillover, and CompQC live on the inspector — nudge a coefficient and see the plot, instead of trusting a hidden unmix."),
            ("QC before the argument", "Time QC, clean-events, auto-gate, and FMO are first-class. Argue about biology after the file has been through QC — not before."),
            ("Propose, then Apply", "Optional Assistant suggests gates and a panel/FMO checklist from summaries. No raw FCS to a vendor. Nothing applies until you do."),
        ],
        "Lab software · Flow cytometry",
        "Research and laboratory software. Not a medical device. Not intended to diagnose, treat, cure, or prevent disease.",
    ),
    "fmb-discovery-informatics": lambda: product(
        "A jailed science-agent OS.",
        "PI ask → routed evidence → versioned artifacts + METHODS + cannots. Wave 0 productization. Method and operating system only — not a shipping SaaS login, not a wet lab, not a therapeutic, and not a grant-submission product.",
        [
            ("Ask to journal", "A PI ask is routed, identifiers are normalized, the smallest skill set runs, and the desk gets versioned artifacts with METHODS and cannots."),
            ("Generic machinery. No target list.", "The public page describes the operating system a lab would run on its own desk and corpus. It does not publish results, sequences, or held-out datasets."),
            ("What the OS will not say", "Method claims only. Occupancy is not a requirement. Missing is not a negative result. The PI commits the bench."),
        ],
        "Stage A · Research software",
        "Research software. Not a medical device. Not grant submission. Not intended to diagnose, treat, cure, or prevent disease.",
    ),
    "fmb-next-gen-drug-development": lambda: product(
        "See the structure. Design in software — not a wet lab.",
        "Next-gen drug design is Florida Man Bioscience’s Stage A surface: local structure visualization (desktop and VR) and a simulated design–build–test–learn loop for peptide and protein work. Software you can look at. Not a wet lab. Not a therapeutic.",
        [
            ("See the structure", "Desktop and headset views of local structures so design talk stays on the molecule, not a slide deck."),
            ("A simulated loop", "Design, evaluate, and record a cycle with simulated build/test economics. Lab robots are a later stage — not this one."),
            ("Honest non-goals", "No wet-lab claim. No therapeutic claim in the UI. No Stage B hardware."),
        ],
        "Design platform · Stage A",
        "Research and design software. Not a medical device. Not a substitute for regulated laboratory processes. No therapeutic claims.",
    ),
    "fmb-vector-nanodisk": lambda: product(
        "A research option — not a marketed therapeutic.",
        "Vector nanodisk (MSP) is a research-stage delivery concept for peptide and nucleic-acid payloads — the long-horizon Deliver leg of Detect → Design → Deliver. It is optionality on the books, not a product you can buy and not a drug we sell. Institutional IP stays held out until cleared.",
        [
            ("Not a product you buy", "This page describes a research program. Not a drug, not a device, not a clinic offering."),
            ("Same platform, later leg", "Detect is software. Design is Stage A visualization. Deliver sits further out on the clock."),
            ("No assignment on this page", "We do not preview owners, licensors, or university vehicles here. Scientific collaboration talk only."),
        ],
        "Delivery research · IP held out",
        "Research program only. Not an approved drug, biologic, or clinical product. No outcome guarantees. Not intended to diagnose, treat, cure, or prevent disease.",
    ),
    "fmb-neurocreatine": lambda: product(
        "An early CNS note — not a lead program.",
        "Neurocreatine is a parked, early discovery track for CNS-oriented peptide ideas. It is on the roster so the portfolio is complete. It is not a consumer product, not a supplement, and not what Florida Man Bioscience leads with.",
        [
            ("Parked, not launched", "Early notes, not a campaign. We keep the page so the roster is honest."),
            ("No lead, no claims", "Nothing here is a protocol, a pill, or a clinical offer."),
            ("Earns the next experiment", "If it ever moves, it will earn the next measurement. It has not."),
        ],
        "Parked · Early discovery",
        "Research and discovery only. Not a marketed supplement, drug, or medical device. Not intended to diagnose, treat, cure, or prevent disease.",
    ),
}

TITLES = {
    "florida-man-bioscience": ("Florida Man Bioscience — Peptide medicine, matched to the genome",
                               "PeptOdyssey is the shipping product. Design visualization is Stage A. Nanodisk delivery stays research."),
    "fmb-team": ("Team — Florida Man Bioscience", "Founders, Nucleate Activator contributors, and advisors behind Florida Man Bioscience and PeptOdyssey."),
    "fmb-peptodyssey": ("PeptOdyssey — Florida Man Bioscience", "Genome-aware peptide platform. Engine, clinician-readable dossier, iOS capture, and tracker. Not a prescription."),
    "fmb-peptodyssey-privacy": ("PeptOdyssey Privacy Policy — Florida Man Bioscience", "Privacy policy for the PeptOdyssey iOS research app."),
    "fmb-u4u": ("PeptOdyssey — Florida Man Bioscience", "Genome-aware peptide platform. A dossier, not a prescription."),
    "fmb-u4u-privacy": ("U4U Privacy — Florida Man Bioscience", "Local-first consumer genomics toolkit. Not a diagnostic service."),
    "fmb-cytogate": ("CytoGate — Florida Man Bioscience", "Flow cytometry desktop (CytoCrunch). Compensation, QC, auto-gate, propose-only Assistant."),
    "fmb-discovery-informatics": ("Discovery Informatics — Florida Man Bioscience", "A jailed science-agent OS. Research software. Not a medical device."),
    "fmb-next-gen-drug-development": ("Next-gen drug design — Florida Man Bioscience", "Stage A structure visualization. Software, not a wet lab."),
    "fmb-vector-nanodisk": ("Vector nanodisk — Florida Man Bioscience", "Research-stage delivery optionality. Not a marketed therapeutic."),
    "fmb-neurocreatine": ("Neurocreatine — Florida Man Bioscience", "Parked early CNS peptide discovery. Not a lead program."),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    exclude = []
    for slug, path, depth, _label in PAGES:
        title, desc = TITLES[slug]
        body_fn = BODIES[slug]
        body = body_fn()
        html = chrome(depth, path, title, desc, body)
        dest = OUT / f"{slug}.html"
        dest.write_text(html, encoding="utf-8")
        exclude.append(slug)
        print("wrote", dest)
    (OUT / "CATALOG_EXCLUDE").write_text("\n".join(exclude) + "\n", encoding="utf-8")
    print("exclude", len(exclude))


if __name__ == "__main__":
    main()
