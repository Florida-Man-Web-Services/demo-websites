# Site generation quality rubric

Use after generating or improving a demo page. Score pass/fail; anything in **Blockers** fails the ship.

## Blockers (must pass)

| # | Check |
|---|--------|
| B1 | Single self-contained HTML under `generated-sites/<slug>.html` |
| B2 | Hero is first major content section |
| B3 | At least 5 distinct content sections (+ footer) |
| B4 | Name matches brief; address/phone only if provided (no fabrications) |
| B5 | Rating/testimonials: only real rating string or none — no fake quotes/counts |
| B6 | If phone present: visible + `tel:` link with correct digits |
| B7 | Editable hooks: Hours and/or Address/Location/Find us/Visit us labeling present |
| B8 | Responsive + `prefers-reduced-motion` respected for non-trivial animation |
| B9 | Meta description + sensible `<title>` including Gainesville context |
| B10 | Critical facts exist as real text (not only images/CSS) for AI 411 |

## Craft bar (aim 8/10+)

| # | Check |
|---|--------|
| C1 | Unique palette/type/motif vs same-category siblings |
| C2 | Not generic SaaS / purple gradient / glassmorphism default |
| C3 | One clear idea per section; strong headlines |
| C4 | Local Gainesville flavor without UF trademark abuse or tourist cliché spam |
| C5 | CTA hierarchy clear (call / directions) |
| C6 | Motion restrained; focus states visible |
| C7 | Footer has NAP + copyright |
| C8 | File roughly 12–45KB of purposeful code |

## Improve-mode extras

| # | Check |
|---|--------|
| I1 | Owner-corrected NAP preserved |
| I2 | Invented social proof removed if it was present |
| I3 | Siteedit labels improved if they were missing |

## Coke Zero craft transfer (qualitative)

High-scoring pages in the public Coke Zero model bake-off shared:

1. **Hero authority** — one line you remember  
2. **Section pacing** — numbered or clearly sequenced chapters  
3. **System thinking** — tokens, rhythm, not one-off hacks  
4. **Restraint** — empty space used on purpose  
5. **Brand-specific motif** — not a neutral template  

Apply the same bar; swap soda ritual for the business’s craft ritual.

## Quick visual pass

Open the file in a browser at 390px and 1280px:

- [ ] Headline readable without horizontal scroll  
- [ ] Nav doesn’t collide with CTA  
- [ ] Phone/address easy to find in <5 seconds  
- [ ] No lorem, no broken links, no console errors from bad JS  
