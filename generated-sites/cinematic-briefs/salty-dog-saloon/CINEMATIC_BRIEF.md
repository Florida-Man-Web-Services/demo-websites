# Cinematic wave 1 — demo-websites

You are a Hermes leaf agent. **Do not run memo.**

## Goal
Rewrite ONE existing Gainesville demo landing page into a **cinematic scroll-driven** version while keeping product constraints of Florida Man demo-websites.

## Hard product constraints (demo-websites)
1. **Single self-contained HTML** at `generated-sites/<slug>.html` (inline CSS/JS; optional Google Fonts only)
2. **NAP truth only** — never invent phone, address, hours grids, awards, staff, or testimonials
3. Ratings only from provided Google string — **no fake testimonials**
4. Siteedit hooks: Hours/Open heading if you mention hours (only if true data exists — usually omit invented hours), Address/`<address>`, visible phone + `tel:+1…` **only when NAP phone is non-empty**
5. AI 411 speakable text (who/what/where/call), not image-only facts
6. Hero first, **≥5 sections**, mobile + **`prefers-reduced-motion`**
7. Unique palette/type/motif vs category siblings
8. Output **only** overwrite `generated-sites/<slug>.html` in your worktree
9. Demo footer acknowledging Florida Man Web Services demo (match tone of siblings)
10. Do **not** touch index.html, other slugs, mcp-server, voice-agent

## Cinematic requirements (scroll storytelling)
Implement a premium **scroll-bound cinematic journey** without external video files (no Higgsfield / no binary assets — pure HTML/CSS/JS):

- Document scroll runway **~400–700vh** for the hero journey stage
- **Sticky full-viewport stage** with layered visuals (CSS gradients, mesh, grain, geometric light, parallax layers, filmic grade)
- Scroll progress `p ∈ [0,1]` drives:
  - layer transforms / depth
  - timed copy beats (3–5 short lines fade in progress bands)
  - optional thin progress indicator
- **NOT** autoplaying video; no audio
- Primary CTA: if phone exists → **Hold to Call** (~1s hold with progress ring) + accessible click/`tel:` fallback; if no phone → **Get Directions** using maps URL or address
- After journey: remaining sections (About/what, Address, Rating if any, Contact/CTA, footer) in readable stacked layout
- `prefers-reduced-motion: reduce` → static poster hero + normal scroll, no scrub journey
- Mobile: touch scroll works; keep CSS light (no multi-MB assets)

## Aesthetic direction
Match the business category with a **distinct** cinematic motif (do not clone another agent’s palette):
- Bars: neon bleed, amber glass, night rain reflections
- Coffee/cafes: steam volumetrics, warm ceramic, soft morning god-rays
- Barber: chrome clippers macro-feel, stripe pole motion, velvet shadow
- Bakery: flour dust motes, warm oven glow, pastry macro abstract
- Tattoo: ink bloom, emerald beetle iridescence, blackwork geometry

Feel: Apple product-page / luxury brand teaser, but honest small-business Gainesville — sparse chrome, strong type, no stock-photo dependency (CSS/SVG illustration only).

## Steps
1. `cd` to your exclusive worktree (only allowed cwd)
2. Read current `generated-sites/<slug>.html` + NAP JSON provided
3. Write the full cinematic HTML replacement
4. Sanity check: file exists, has ≥5 sections, reduced-motion CSS, address present, tel only if phone non-empty, no lorem/fake quotes
5. Commit on your branch:
   ```
   git add generated-sites/<slug>.html
   git commit -m "feat(sites): cinematic scroll redesign — <slug>"
   ```
6. Write untracked `/tmp/cinematic-wave1/RESULT-<slug>.md` with: path, bytes, section count, phone linked Y/N, one-line motif

## Forbidden
- Inventing hours, phone, reviews text, staff names
- External images/video URLs that are not fonts
- Editing any file except your slug HTML
- git push (orchestrator merges)
- Running memo
