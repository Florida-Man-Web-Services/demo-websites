# Khashayar Mahmoudi Website Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, register, publish, and verify a name-led personal website for Khashayar Mahmoudi with the supplied phone number as both public contact and private owner key.

**Architecture:** Add one self-contained HTML page to the existing `generated-sites/` static-site source. The page will be served through the existing content-hash demo-sites image and will not be added to the outreach catalog. Separately, upsert the phone-keyed customer record in the live voice-agent registry with `active_owner` status and the page slug, then verify owner authorization against that slug.

**Tech Stack:** Semantic HTML5, inline CSS/JavaScript, Google Fonts with system fallbacks, SHA-256 content hashing, existing FMWS `hosting/Dockerfile`, live voice-agent customer registry API or in-cluster PVC, curl, Python standard library, git.

## Global Constraints

- Slug: `khashayar-mahmoudi`
- Public phone display: `(352) 888-3741`
- Public phone link: `tel:+13528883741`
- Private owner association: customer registry keyed by `+13528883741`
- No address, hours, occupation, credentials, client list, testimonials, awards, or other claims unless supplied later
- Output one self-contained HTML file at `generated-sites/khashayar-mahmoudi.html`.
- Inline CSS and JavaScript only; no framework, Tailwind, Bootstrap, or required local assets.
- Include responsive layouts for narrow mobile screens and wide desktop screens.
- Use semantic headings, `<main>`, `<section>`, `<address>` or labeled contact block, and a `tel:+13528883741` link.
- Include an honest contact-hours note such as “Call to connect” rather than inventing an hours schedule.
- Do not copy the phone into unrelated NAP fields or claim a business category.
- Register the number as a private `active_owner` page manager for the `khashayar-mahmoudi` slug, with `trusted_phones` set to the same E.164 number.
- Leave unrelated dirty repository changes untouched.

---

### Task 1: Create the personal website HTML

**Files:**
- Create: `generated-sites/khashayar-mahmoudi.html`

**Interfaces:**
- Consumes: the approved design spec at `docs/superpowers/specs/2026-09-15-khashayar-mahmoudi-website-design.md`.
- Produces: a complete static page whose bytes are the input to the repository hash URL and whose file stem is the owner registry slug.

- [ ] **Step 1: Build the semantic page shell**

Create a complete document with:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Khashayar Mahmoudi — Personal website</title>
  <meta name="description" content="Personal website and contact page for Khashayar Mahmoudi.">
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
  <header>...</header>
  <main id="main">
    <section id="intro">...</section>
    <section id="profile">...</section>
    <section id="focus">...</section>
    <section id="work">...</section>
    <section id="contact">...</section>
    <section id="connect">...</section>
  </main>
  <footer>...</footer>
</body>
</html>
```

Write neutral copy only. The page may say it is a personal contact page and invite future profile/work details, but it must not name an occupation, services, location, credentials, clients, awards, or reviews.

- [ ] **Step 2: Add the approved visual system**

Use inline CSS with:

```css
:root {
  --paper: #f4f1e9;
  --ink: #17202b;
  --muted: #5d6975;
  --cobalt: #2457d6;
  --line: #cbd1d8;
}
```

Use a name-led editorial hero, cobalt rule, offset paper-panel motif, left-aligned reading column, expressive display typography with strong fallback, and restrained body typography. Include responsive rules for approximately 390px and wide desktop widths. Add visible `:focus-visible` states and a single restrained load reveal disabled by:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

- [ ] **Step 3: Add the public contact action exactly once in the primary hero and again in contact/closing CTA**

Use the exact public link and display text:

```html
<a href="tel:+13528883741">Call (352) 888-3741</a>
```

Use supporting text such as “Call to connect” rather than asserting hours. Keep the phone out of any fake address or business NAP block.

### Task 2: Verify the local page against the spec

**Files:**
- Read: `generated-sites/khashayar-mahmoudi.html`
- Read: `voice-agent/businesses.py`

**Interfaces:**
- Consumes: the HTML from Task 1.
- Produces: a verified 12-character SHA-256 content hash and a pass/fail checklist before any external write.

- [ ] **Step 1: Run a complete structural verification**

Run from `/home/noahtjones/demo-websites`:

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib, re
p = Path('generated-sites/khashayar-mahmoudi.html')
s = p.read_text(encoding='utf-8')
checks = {
    'file_exists': p.is_file(),
    'title': bool(re.search(r'<title>[^<]*Khashayar Mahmoudi', s, re.I)),
    'description': 'Personal website and contact page for Khashayar Mahmoudi.' in s,
    'sections_at_least_5': len(re.findall(r'<section\b', s, re.I)) >= 5,
    'semantic_main': bool(re.search(r'<main\b', s, re.I)),
    'phone_display': '(352) 888-3741' in s,
    'exact_tel': 'href="tel:+13528883741"' in s,
    'reduced_motion': 'prefers-reduced-motion' in s,
    'focus_styles': ':focus-visible' in s,
    'no_fake_social_proof': not bool(re.search(r'\b(?:reviews?|testimonials?|clients?|awards?|5\.0|100\+|500\+)\b', s, re.I)),
    'no_fake_nap': not bool(re.search(r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b', s, re.I)),
}
for k, v in checks.items(): print(f'{k}={"PASS" if v else "FAIL"}')
assert all(checks.values()), checks
print('sha256_12=' + hashlib.sha256(p.read_bytes()).hexdigest()[:12])
PY
```

Expected: every check prints `PASS`, followed by the actual 12-character hash. Do not invent or manually type the hash into later reporting.

- [ ] **Step 2: Inspect the rendered page locally**

Run:

```bash
python3 -m http.server 8765 --directory /home/noahtjones/demo-websites
```

Open `http://127.0.0.1:8765/generated-sites/khashayar-mahmoudi.html` in the browser and inspect mobile-width and desktop-width layouts. Confirm no horizontal overflow, the name is visible above the fold, and the call links are readable and keyboard-focusable. Stop the temporary server after inspection.

### Task 3: Upsert and verify the private owner association

**Files:**
- External state: live voice-agent customer registry (`/data/customers.json` through the supported service path)
- Read: `mcp-server/customers.py` for field semantics and authorization behavior

**Interfaces:**
- Consumes: verified slug `khashayar-mahmoudi` and exact E.164 phone `+13528883741`.
- Produces: a phone-keyed customer row with `status=active_owner`, `business_name=Khashayar Mahmoudi`, `contact_name=Khashayar Mahmoudi`, `slug=khashayar-mahmoudi`, and `trusted_phones=["+13528883741"]`.

- [ ] **Step 1: Check the live registry before writing**

Run:

```bash
curl -fsS https://voice.flmanbiosci.net/api/onboarding/customers -o /tmp/fmws-customers-before.json
python3 - <<'PY'
import json
rows = json.load(open('/tmp/fmws-customers-before.json')).get('customers', [])
needle = '+13528883741'
for row in rows:
    if row.get('phone') == needle:
        print(json.dumps(row, indent=2))
PY
```

If an existing row for the exact number has a conflicting owner, slug, or status, stop and report the conflict rather than overwriting it.

- [ ] **Step 2: Upsert through an existing authorized path**

Use the repository’s supported customer-write mechanism or the voice-agent PVC only if the API does not expose a write endpoint. The logical operation must be equivalent to:

```python
customers.upsert(
    '+13528883741',
    status='active_owner',
    business_name='Khashayar Mahmoudi',
    contact_name='Khashayar Mahmoudi',
    slug='khashayar-mahmoudi',
    source='operator',
    demo_url='https://floridamanweb.online/<verified-hash>/',
    notes='Page manager. Inbound caller ID authorized for owner updates.',
    patch={'trusted_phones': ['+13528883741']},
)
```

Use the actual hash produced in Task 2. Do not expose the number in any unrelated NAP record.

- [ ] **Step 3: Read back the exact row and verify authorization**

Read the registry again and assert these values:

```python
row['phone'] == '+13528883741'
row['status'] == 'active_owner'
row['slug'] == 'khashayar-mahmoudi'
'+13528883741' in row['trusted_phones']
```

Then run a fresh Python verification in the voice-agent environment equivalent to:

```python
assert customers.resolve_mode('+13528883741', env_mode='auto') == 'owner_updates'
assert customers.authorize_owner_write('+13528883741', 'khashayar-mahmoudi')['ok'] is True
```

Record the exact customer id returned by the read-back. If live code cannot verify one of these checks, report the blocker instead of claiming association succeeded.

### Task 4: Publish through the existing demo-sites path

**Files:**
- Modify only if required: `generated-sites/khashayar-mahmoudi.html`
- Do not modify: `hosting/Dockerfile` (hashed pages are already copied by its wildcard)

**Interfaces:**
- Consumes: verified HTML and hash from Task 2; verified owner record from Task 3.
- Produces: a reachable public URL at `https://floridamanweb.online/<hash>/`.

- [ ] **Step 1: Commit the page before publishing**

Stage only the new website file:

```bash
git add -- generated-sites/khashayar-mahmoudi.html
git commit -m "feat: add Khashayar Mahmoudi personal site"
```

- [ ] **Step 2: Push the page to the repository’s main branch**

Before pushing, fetch the remote and check for remote movement. Preserve unrelated dirty paths:

```bash
git fetch origin main
git status --short
git log --oneline --decorate -3
```

If `origin/main` advanced beyond the local commit, rebase only the new commit onto `origin/main`, then push. Otherwise push the local main branch:

```bash
git push origin main
```

- [ ] **Step 3: Verify the deployment and public page**

Recompute the hash from the committed file and request the resulting URL:

```bash
HASH=$(sha256sum generated-sites/khashayar-mahmoudi.html | cut -c1-12)
curl -fsS -o /tmp/khashayar-live.html -w 'http=%{http_code} url=https://floridamanweb.online/%{url_effective}\n' "https://floridamanweb.online/$HASH/"
python3 - <<'PY'
from pathlib import Path
s = Path('/tmp/khashayar-live.html').read_text(encoding='utf-8')
assert 'Khashayar Mahmoudi' in s
assert 'tel:+13528883741' in s
print('live_content=PASS')
PY
```

If CI or Flux has not rolled the new image, report the observed deployment state and do not claim the public URL is live.

### Task 5: Final integrity and scope check

**Files:**
- Read: git status and the committed page

- [ ] **Step 1: Confirm only intended new tracked files were committed**

Run:

```bash
git show --stat --oneline HEAD
git status --short
```

Confirm the commit contains only `generated-sites/khashayar-mahmoudi.html` and that pre-existing dirty files remain uncommitted.

- [ ] **Step 2: Report evidence with identifiers**

Report the verified page slug, content hash URL, customer id, status, and owner authorization result. State any deployment or registry blocker explicitly if a fresh read-back did not pass.
