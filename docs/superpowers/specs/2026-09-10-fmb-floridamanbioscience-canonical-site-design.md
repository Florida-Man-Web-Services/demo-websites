# FMB canonical site on floridamanbioscience.com

**Date:** 2026-09-10  
**Status:** design approved in fmws-cos session; not implemented  
**Owner (FMWS slice):** fmws-cos / demo-websites  
**Handoff:** `fmb-cos` for `flmanbiosci.net` redirect and any new product claims  

## Problem

Florida Man Bioscience’s public marketing lives on `https://flmanbiosci.net` (Next.js, `~/u4u-engine/frontend`). Noah registered `floridamanbioscience.com` (2026-09-11 UTC) as the new canonical public face: a better multi-page set for every already-public product, editable by calling AI411.

This is an **FMWS website + AI411 + vanity-domain** ship. It is not a rewrite of the Next app, not FMB science strategy, and not a Gainesville outreach demo.

## Locked decisions

1. `.com` is the new canonical public site. `flmanbiosci.net` → `.com` redirect is **later**, `fmb-cos` / IAC (that zone is `cora`/`stan`).
2. Real URLs, 1:1 with today’s sitemap. AI411 asks which page, then edits that file.
3. New visual system. Keep the live **mark PNG**. Copy and claims only from the live public site. No new efficacy language. Vector nanodisk stays research / held-out.
4. Architecture: demo-websites static HTML + `/vanity/florida-man-bioscience/` + Worker on the `.com` zone. Not `u4u-engine/frontend`. Not Cloudflare Pages as a separate CMS.
5. Visual: **clinician dossier** (paper `#ece8e1`, ink `#1c1915`, Fraunces + Source Sans 3) and **bound report** layout (one paper column, hairline rules, status as a contents table — not SaaS cards, not a permanent TOC rail).
6. AI411: one inbound CID owns every FMB slug (`owned_slugs`). Number is PVC-only; never on HTML, catalog, or this spec.
7. Gainesville catalog: these files are **not** `index.html` cards.

## Non-goals

- Restyling or replacing the Next app in this ship.
- Publishing a phone number (none is public NAP).
- Invented hours, addresses, testimonials, or clinical outcome claims.
- Routing held-out delivery IP into marketing copy beyond the already-public “research / IP held out” label.
- Dual-binding Discord bots or A2P / Twilio brand work.
- Immediate `.net` DNS cutover.

## Information architecture

Pretty paths on `https://floridamanbioscience.com` (trailing slash on directories):

| Path | Page | AI411 slug (`generated-sites/<slug>.html`) |
|------|------|--------------------------------------------|
| `/` | Company home | `florida-man-bioscience` |
| `/team` | Team | `fmb-team` |
| `/peptodyssey` | PeptOdyssey | `fmb-peptodyssey` |
| `/peptodyssey/privacy` | PeptOdyssey privacy (verbatim policy) | `fmb-peptodyssey-privacy` |
| `/products/u4u` | U4U | `fmb-u4u` |
| `/products/u4u-privacy` | U4U privacy (verbatim) | `fmb-u4u-privacy` |
| `/products/cytogate` | CytoGate | `fmb-cytogate` |
| `/products/discovery-informatics` | Discovery Informatics | `fmb-discovery-informatics` |
| `/products/next-gen-drug-development` | Next-gen drug design (Stage A) | `fmb-next-gen-drug-development` |
| `/products/vector-nanodisk` | Vector nanodisk (research / held-out) | `fmb-vector-nanodisk` |
| `/products/neurocreatine` | NeuroCreatine | `fmb-neurocreatine` |

Shared chrome: real mark, bound-report running header, nav to the public programs, footer `hello@flmanbiosci.net`. No `tel:`.

Canonical tags on the new pages point at `https://floridamanbioscience.com<path>`. Until the `.net` redirect, both hosts may be live; that is accepted for this ship.

## Architecture

### Files

Each AI411 slug is a **top-level** `generated-sites/<slug>.html` so `get_site_outline` / `apply_change_request` keep the existing stem lookup.

Shared assets (mark and any cloned public images) live in `generated-sites/florida-man-bioscience/`. Use **relative** `src`/`href` so hash URLs and vanity both work:

| Page depth | Mark path |
|------------|-----------|
| `/` | `florida-man-bioscience/mark.png` |
| `/team`, `/peptodyssey` | `../florida-man-bioscience/mark.png` |
| `/products/*`, `/peptodyssey/privacy` | `../../florida-man-bioscience/mark.png` |

Do not use site-root `/assets/…` (that 404s on the floridamanweb vanity prefix).

There is **one HTML file per page**, not a duplicate tree. `hosting/Dockerfile` maps stems onto pretty paths:

```
COPY generated-sites/florida-man-bioscience.html /usr/share/nginx/html/vanity/florida-man-bioscience/index.html
COPY generated-sites/fmb-team.html /usr/share/nginx/html/vanity/florida-man-bioscience/team/index.html
COPY generated-sites/fmb-peptodyssey.html /usr/share/nginx/html/vanity/florida-man-bioscience/peptodyssey/index.html
COPY generated-sites/fmb-peptodyssey-privacy.html /usr/share/nginx/html/vanity/florida-man-bioscience/peptodyssey/privacy/index.html
COPY generated-sites/fmb-u4u.html /usr/share/nginx/html/vanity/florida-man-bioscience/products/u4u/index.html
COPY generated-sites/fmb-u4u-privacy.html /usr/share/nginx/html/vanity/florida-man-bioscience/products/u4u-privacy/index.html
COPY generated-sites/fmb-cytogate.html /usr/share/nginx/html/vanity/florida-man-bioscience/products/cytogate/index.html
COPY generated-sites/fmb-discovery-informatics.html /usr/share/nginx/html/vanity/florida-man-bioscience/products/discovery-informatics/index.html
COPY generated-sites/fmb-next-gen-drug-development.html /usr/share/nginx/html/vanity/florida-man-bioscience/products/next-gen-drug-development/index.html
COPY generated-sites/fmb-vector-nanodisk.html /usr/share/nginx/html/vanity/florida-man-bioscience/products/vector-nanodisk/index.html
COPY generated-sites/fmb-neurocreatine.html /usr/share/nginx/html/vanity/florida-man-bioscience/products/neurocreatine/index.html
COPY generated-sites/florida-man-bioscience/ /usr/share/nginx/html/vanity/florida-man-bioscience/florida-man-bioscience/
```

Same class of bake as IMPACTO (`impacto.html` + `impacto-pages/` + sidecar). Do not `kubectl cp` into the live demo-sites pod as the ship.

### Catalog exception

`index.html` card count today must equal `generated-sites/*.html`. FMB files would pollute the Gainesville catalog. **Exclude** stems `florida-man-bioscience` and `fmb-*` from that equality check and from the catalog UI. Document the denylist next to the count.

### Edge

`floridamanbioscience.com` NS is `margot.ns.cloudflare.com` / `martin.ns.cloudflare.com` — **not** the hwcopeland account (`cora`/`stan` on `floridamanweb.online` and `flmanbiosci.net`). Do not add an IAC `Zone` CR or expect `cf-issuer` DNS-01.

Copy `hosting/impacto-community-worker/`. `ORIGIN_BASE=https://floridamanweb.online/vanity/florida-man-bioscience`. Worker `fetch()` + incoming path; strip `Host` and hop-by-hop/`cf-*`. Apex originless A + `www` CNAME; bind **zone Worker routes**, not account custom domains.

Token (Zone DNS Edit, Account Workers Scripts Edit, Zone Workers Routes Edit) is provided at hookup, never committed. Treat a chat paste as spent; prefer rotate after.

Until the token exists, the preview is `https://floridamanweb.online/vanity/florida-man-bioscience/`.

### AI411 page manager

Today: one customer row, one `slug`. This ship extends the registry:

- `owned_slugs: string[]` on the customer (plus existing primary `slug` for back-compat).
- `authorize_owner_write(phone, slug)` succeeds if `slug` is the primary slug **or** in `owned_slugs`.
- `resolve_mode` for that CID remains `owner_updates`.
- Voice: if more than one owned slug, **ask which page** (speakable names from the IA table), then outline/apply on that stem.
- Inbound only. Do not dial the number (TCPA).
- `apply_change_request` writes **pod-local** HTML (`GENERATED_SITES_DIR` / PVC). Public `.com` updates when demo-sites CI rebuilds the image. Say that on the call; do not claim the CDN published.

Grant: upsert `active_owner` on the voice PVC `/data/customers.json` with all FMB stems in `owned_slugs`. Copy every FMB HTML file to `/app/generated-sites/` **and** `/data/generated-sites/`. Do not rollout-restart the voice pod just to pick up a copied `lookup.py`.

Tests: owner CID × each slug `ok`; stranger CID `not_owner`; unlisted slug `slug_mismatch` (or equivalent existing code).

### Copy source

Crawl **live** `https://flmanbiosci.net` (sitemap as of 2026-09-10). Do not treat `~/fmb-website` as source of truth (legacy static). Privacy pages stay verbatim.

Siteedit targets: lead paragraph and the shipping/stage/research status lines. Do not add fake Hours/Address blocks.

## Visual system

| Token | Value |
|-------|--------|
| Paper | `#ece8e1` |
| Ink | `#1c1915` |
| Rule | `rgba(28,25,21,0.22)` |
| Mute | ink at ~65% |
| Display | Fraunces |
| Body | Source Sans 3 |
| Mark | live `mark.png`, not a letter-F stand-in |
| Accent | none required; do not reuse live pine `#1a6b4a` |

Layout: bound report. Home hero is the public thesis sentence without a single italicized/colored word. Status is a three-column contents table (PeptOdyssey shipping / design Stage A / nanodisk research) using **already-public** labels only.

Motion: none beyond `:focus-visible`. Honor `prefers-reduced-motion`.

Do not use: cream+pine stack from the current Next site, SaaS icon cards, tracked-out all-caps eyebrows, fake metrics, numbered 01/02/03 chrome unless the content is truly a sequence (the Detect → Design → Deliver philosophy may stay as prose, not badge numbers).

## Ship order

1. Crawl live pages; write 11 HTML files + mark sidecar; hooks; no `tel:`.
2. Dockerfile vanity map; catalog denylist.
3. CI: GET 200 on vanity home, every pretty path, and the mark PNG.
4. Worker on `.com` when the zone token is available; otherwise stop at floridamanweb vanity.
5. `owned_slugs` + tests; PVC grant; HTML copies into voice dirs.
6. Handoff `.net` redirect to `fmb-cos` (not this PR).

## Verification

- Every IA path 200 on vanity (and on `.com` after Worker).
- Mark PNG 200; no reconstructed SVG logo.
- No phone digits on any FMB HTML.
- Nanodisk page still says research / held-out; no delivery-as-product language.
- Privacy HTML matches live policy text.
- Catalog card count ignores FMB stems.
- AI411 tests as above.
- Visual check ~390px and ~1440px.

## Risks

- Two public sites until `.net` redirect (SEO). Canonical hrefs on `.com` mitigate; redirect is the fix.
- AI411 apply ≠ production publish until CI.
- Voice image may lack new `owned_slugs` until a voice-agent/mcp image ships; PVC grant still records intent.
- Foreign Cloudflare account: wrong token or IAC Zone CR will fail; follow IMPACTO worker recipe only.
