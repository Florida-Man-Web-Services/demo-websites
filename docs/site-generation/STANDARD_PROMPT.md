# Gainesville Demo Site — Standard Generation Prompt

**Purpose.** One standardized prompt that produces a unique, high-craft, self-contained demo website for a Gainesville business. Fill the `{{…}}` fields (or run `fill_prompt.py`) before sending to the model.

**Quality bar.** Inspired by the high-reasoning Coca-Cola Zero landing-page tests (hero first, ≥5 sections, distinctive design system, editorial hierarchy) — adapted for *local service businesses*, *NAP accuracy*, *owner-update surgery*, and *AI 411 voice knowledge*.

**Modes.**

| Mode | When | Extra instruction |
|------|------|-------------------|
| `create` | No site yet, or full rewrite | Output one complete HTML file |
| `improve` | Site exists but is thin/generic | Rewrite in place to the same path; keep true NAP; raise craft |

Default: `create`.

---

## Prompt (copy from the line below)

```
You are a world-class brand + web designer building a single production-ready landing page for a real local business in Gainesville, Florida.

No external design kits, component libraries, or frameworks are required. You may use Google Fonts and pure CSS/JS only. Prefer zero network dependencies beyond optional fonts. The page must open as a single self-contained HTML file.

════════════════════════════════════════════════════════
BUSINESS BRIEF (facts only — do not invent conflicting data)
════════════════════════════════════════════════════════
Mode:              {{MODE}}
Business name:     {{NAME}}
Slug (filename):   {{SLUG}}.html
Search category:   {{SEARCH_CATEGORY}}
Google category:   {{CATEGORY_LABEL}}
Address:           {{ADDRESS}}
Phone:             {{PHONE}}
Rating string:     {{RATING}}
Google Maps URL:   {{GOOGLE_MAPS_URL}}
City context:      Gainesville, FL (University of Florida / Hogtown; neighborhood, not tourist-trap cliché)
Network:           Florida Man Web Services demo network — referenced by Gainesville AI 411 (voice directory for local businesses, events, deals, recommendations)

Optional extras:
Hours (known):     {{HOURS}}
Services (known):  {{SERVICES}}
Notes / voice:     {{NOTES}}
Design seed:       {{DESIGN_SEED}}
Prior HTML path:   {{PRIOR_PATH}}

════════════════════════════════════════════════════════
MISSION
════════════════════════════════════════════════════════
Create a beautiful, memorable, conversion-oriented landing page that:
1. Makes this business look trusted and specific to Gainesville — not a generic SaaS template.
2. Is unique among hundreds of sibling demos (different palette, type, layout rhythm, and motifs than neighbors in the same category).
3. Gives AI 411 clean, speakable facts (who they are, what they do, where, how to contact).
4. Is surgically editable later (hours / phone / address / copy) via structured section labels.

════════════════════════════════════════════════════════
HARD TECHNICAL REQUIREMENTS
════════════════════════════════════════════════════════
- Single file: generated-sites/{{SLUG}}.html
- Valid HTML5, lang="en", charset UTF-8, responsive viewport meta
- All CSS in <style>; all JS in <script> (minimal JS; progressive enhancement only)
- No build step, no React/Vue/Svelte, no Tailwind CDN, no Bootstrap
- Optional: one Google Fonts <link> (max 2 families, few weights)
- Prefer system/stack fonts when they fit the craft better than generic Inter
- Mobile-first, works at 360px and 1200px+
- prefers-reduced-motion: reduce animations when set
- Smooth scroll for in-page anchors
- Semantic structure: header/nav, main, sections with ids, footer
- Accessible: real focus styles, 44px+ hit targets, contrast for body text, alt text on meaningful SVGs if any
- Open Graph + meta description using real NAP / category
- Do NOT add JSON-LD Schema unless you can keep every claim true to the brief (default: skip)
- No external images required. Use CSS, SVG, geometric motifs, or typography as the visual system. Do not hotlink random stock photos.
- tel: links when phone is known (href="tel:+1XXXXXXXXXX" with digits only after +1)
- Maps: if Google Maps URL provided, use it; else a maps search link built from the address
- File target size: roughly 12–45 KB of thoughtful HTML/CSS (not empty; not a framework dump)

════════════════════════════════════════════════════════
SECTION ARCHITECTURE (minimum 5 content sections + hero on top)
════════════════════════════════════════════════════════
Required order and intent (names may be restyled; keep the *labels below* discoverable in headings or aria):

1. HERO (top of page)
   - Business name as the brand
   - One unforgettable headline (not "Welcome to our website")
   - One short supporting line grounded in category + Gainesville
   - Primary CTA: Call (if phone) and/or Get directions / Visit
   - Secondary nav anchors to sections below
   - Optional micro-proof: rating stars ONLY when the rating string includes a real numeric score (ignore placeholder "none on file")

2. STORY / ABOUT
   - 2–4 short paragraphs or a tight editorial block
   - Specific, local, human. No corporate fluff ("synergy", "solutions", "elevate your lifestyle")
   - If facts are thin: write honest category-true copy framed as a demo introduction, not fake history (do not invent founding year, licenses, awards, staff names, or "family-owned since 19xx")

3. OFFERING (Services / Menu / What we do)
   - 3–6 offerings appropriate to the search category / Google category above
   - Concrete nouns, not vague features
   - If Services (known) lists real offerings, use those; if it says not provided, use honest category defaults framed as general offerings (not invented specialties or prices)

4. SOCIAL PROOF / TRUST
   - Use the rating string only as provided (parse stars + review count if present)
   - If no rating: skip fake testimonials. Use a quiet trust band (local focus, clear contact, straightforward process) instead of invented 5-star quotes
   - NEVER invent customer names, quotes, or review counts

5. VISIT / CONTACT (machine-editable zone)
   - Include a clearly labeled block with heading text that contains one of: "Hours", "Address", "Location", "Find us", "Visit us"
   - Put the street address in a simple text block or <address> so it can be updated later
   - Phone displayed in standard US form and as tel: link when known
   - Hours: if Hours (known) is real data, use it; if not provided, a short line like "Call or message for current hours" — do NOT invent a full weekly schedule
   - Map link when address or Maps URL exists

6. FINAL CTA
   - Strong close: call / visit / request service
   - Repeat phone and address if known

Optional 7th section when it earns its place (pick at most one unless the category demands more):
- Process / How it works (trades, services)
- Menu highlights (food/drink)
- Styles / gallery placeholders as abstract CSS (tattoo, salon) — no fake photos of real people
- Neighborhood note (campus, downtown, NW/SW Gainesville) only if address implies it

Footer: business name, address, phone, © year. Optional quiet line: "Demo site by Florida Man Web Services" (small, not a sales billboard).

════════════════════════════════════════════════════════
DESIGN SYSTEM (invent a unique one per business)
════════════════════════════════════════════════════════
Before coding, decide and apply consistently:

- Palette: 1 background family, 1 ink/text, 1 muted, 1 accent (optional secondary). Prefer cohesive oklch/hex tokens in :root
- Typography: distinctive pairing (editorial serif + clean sans, condensed industrial, soft beauty, mono accents for trades, etc.). Avoid default "Inter everywhere" unless the brand is intentionally product-software
- Layout rhythm: not every section is the same card grid. Vary scale, density, and alignment
- Motif: one signature motif tied to the craft (barber pole rhythm, neon edge, grid paper, steam, stitch, bolt, foam, ink, leaf, etc.) — subtle, not clip-art spam
- Motion: restrained; clarify hierarchy; never delay usability
- Category direction seed: {{CATEGORY_DIRECTION}}

Anti-slop (do not ship):
- Purple-on-white generic SaaS gradients by default
- Glassmorphism + floating blobs as a personality substitute
- Emoji-as-icons unless the brand voice truly warrants them
- Fake dashboards, fake metrics, fake "500+ happy clients"
- Lorem ipsum
- Identical section templates stacked five times
- Stock-photo hero collages
- Rainbow palettes and decorative left-border callout cards everywhere
- Copy stuffed with "elevate", "seamless", "next-level", "your one-stop shop for all your needs"

Coca-Cola-Zero craft lessons to apply (adapted to local business):
- Hero first, then a deliberate multi-section narrative
- One idea per section; strong headline + short body
- Numbered kickers (01 / 02 / …) are welcome when they improve scanability
- Make ordinary local service feel intentional and designed, not template-generated
- Let the brand’s craft dictate the visual system (ritual of a haircut, cold glass at a bar, clean line of a plumber’s work)

════════════════════════════════════════════════════════
TRUTH & COMPLIANCE
════════════════════════════════════════════════════════
- Never contradict {{ADDRESS}}, {{PHONE}}, {{RATING}}, or {{GOOGLE_MAPS_URL}}
- Empty fields stay empty — do not fabricate phone/address
- Do not claim partnerships with UF, city contracts, licenses, BBB, or insurance unless provided in NOTES
- Do not impersonate the business as already having a long web presence; this is a professional demo they can adopt
- Accessible language; no medical/legal guarantees for vets, accountants, etc.

════════════════════════════════════════════════════════
AI 411 / KNOWLEDGE INDEX HOOKS
════════════════════════════════════════════════════════
Write real sentences a voice agent can read aloud:
- Who they are and what category
- What they offer (list-like clarity)
- Where they are and how to call
- Any hours language present

Avoid pure image text or CSS-only content for critical facts. Critical facts must exist as selectable HTML text.

════════════════════════════════════════════════════════
OWNER-UPDATE SURGERY HOOKS (siteedit)
════════════════════════════════════════════════════════
Structure so these updates are easy without redesign:
- Hours under a heading containing "Hours" (or "Open" / "Business Hours")
- Address under "Address", "Location", "Find us", or "Visit us", or inside <address>
- Phone appears as visible text AND tel: link when known
- Key marketing sentences as plain text nodes (not only background images)

════════════════════════════════════════════════════════
IMPROVE MODE (only if Mode = improve)
════════════════════════════════════════════════════════
- Read Prior HTML if provided; keep true NAP and any owner-corrected facts
- Raise visual craft, section clarity, and uniqueness
- Remove invented testimonials/metrics if present
- Preserve tel: and maps behaviors; improve labels for siteedit
- Full rewrite is allowed if the old page is a weak template — do not preserve bad structure for its own sake

════════════════════════════════════════════════════════
OUTPUT
════════════════════════════════════════════════════════
1. Mentally lock the design system (palette, type, motif, section plan).
2. Output ONLY the complete HTML document for generated-sites/{{SLUG}}.html — no markdown fence, no commentary before/after.
3. Title pattern: "{Business name} – {short category phrase}, Gainesville FL" (em dash or en dash fine).
4. Ensure every required section is present and the hero is first.

If any brief field is missing, proceed with best honest defaults rather than asking questions.
```

---

## Field reference

| Field | Source | Notes |
|-------|--------|--------|
| `MODE` | operator | `create` or `improve` |
| `NAME` | Maps / JSON `name` | Exact listing name |
| `SLUG` | `slugify(name)` | lowercase, non-alnum → `-` |
| `SEARCH_CATEGORY` | JSON `search_category` | drives design pack |
| `CATEGORY_LABEL` | JSON `category_label` | Google’s label |
| `ADDRESS` / `PHONE` / `RATING` / `GOOGLE_MAPS_URL` | JSON | may be empty |
| `HOURS` / `SERVICES` / `NOTES` | operator / owner | optional |
| `DESIGN_SEED` | operator or `fill_prompt.py` | 1–3 sentences |
| `CATEGORY_DIRECTION` | `category-directions.md` | pasted block |
| `PRIOR_PATH` | improve mode | path to existing HTML |

---

## Minimal “Coke Zero style” ablation (optional A/B)

When testing models for craft only (not for production network sites), this ultra-short form mirrors the original Coke Zero experiment while keeping local NAP:

```
No skills are allowed. Create a beautiful landing page for {{NAME}} ({{CATEGORY_LABEL}} in Gainesville, FL) using only plain AI. Address: {{ADDRESS}}. Phone: {{PHONE}}. Rating: {{RATING}}. It can use custom CSS only (no component frameworks). It must have at least five sections, with the hero section on top. Single self-contained HTML file. Do not invent phone, address, reviews, or testimonials.
```

Use the full standard prompt for anything that ships into `generated-sites/` and AI 411.
