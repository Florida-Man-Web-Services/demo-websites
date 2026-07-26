# Demo website generation prompts

Standardized, **per-business** prompts for Gainesville demo sites in `generated-sites/`.  
These pages feed **Florida Man Web Services** outreach and **Gainesville AI 411** (voice directory).

## Why this exists

Early sites were strong when the model had room to invent a real design system, and weak when the brief was thin or unconstrained (fake reviews, generic SaaS look, missing hours/address hooks).

The [Coca-Cola Zero multi-model landing page bake-off](https://highsierraloft.github.io/coca-cola-zero-landing-pages/) shows what high-reasoning models do with a tiny brief: **hero first, ≥5 sections, distinctive craft**. We keep that craft bar and add constraints the Coke demo never needed:

| Need | Constraint in our prompt |
|------|---------------------------|
| Real NAP | Never invent phone/address/rating |
| Owner updates (`siteedit`) | Labeled Hours / Address / `tel:` |
| AI 411 knowledge index | Speakable HTML text for facts |
| 246 siblings | Unique design system per brand |
| Hosting | Single self-contained HTML file |

## Files

| File | Role |
|------|------|
| [`STANDARD_PROMPT.md`](./STANDARD_PROMPT.md) | Master prompt template + field reference + short ablation form |
| [`category-directions.md`](./category-directions.md) | Per-category mood / motif / avoid lists |
| [`quality-rubric.md`](./quality-rubric.md) | Ship checklist (blockers + craft) |
| [`fill_prompt.py`](./fill_prompt.py) | Fill template from `gainesville_no_website.json` |

## Quick start

```bash
# One business (full standard prompt to stdout)
python3 docs/site-generation/fill_prompt.py --name "Bob's Barber Shop"

# Improve an existing generated page
python3 docs/site-generation/fill_prompt.py --slug bob-s-barber-shop --mode improve

# Batch a category into files
python3 docs/site-generation/fill_prompt.py --category barbershops --limit 8 \
  --out-dir /tmp/gnv-prompts

# Short Coke-Zero-style craft test (not for production network ships)
python3 docs/site-generation/fill_prompt.py --name "Ole Barn" --ablation

# Optional operator overrides
python3 docs/site-generation/fill_prompt.py --name "Lor Kebab" \
  --design-seed "Persian spice, charcoal grill, late-night warm light" \
  --services "kebabs, grilled meats, rice plates" \
  --hours "Call for current hours"
```

Then send the filled prompt to your model (high reasoning recommended). Save the HTML to:

```text
generated-sites/<slug>.html
```

Slug rule (matches `voice-agent/businesses.py`):

```text
lowercase, non-alphanumeric runs → single hyphen, trim hyphens
```

## Recommended workflow

1. **Fill** prompt with `fill_prompt.py` (add hours/services/notes if the owner shared them).  
2. **Generate** with highest practical reasoning; no multi-site batch in one call (uniqueness dies).  
3. **Score** with `quality-rubric.md`.  
4. **Drop** HTML into `generated-sites/`.  
5. **Index** naturally via MCP knowledge over HTML; voice agent resolves demo URL by content hash when hosted.  
6. **Owner updates** later through ChangeRequests (`hours` / `phone` / `address` / `copy`) — structure the page so those labels exist.

## Improve wave (existing 246)

For thin or generic pages (often &lt;12KB or missing NAP hooks):

```bash
python3 docs/site-generation/fill_prompt.py --slug <slug> --mode improve \
  --notes "Raise craft; remove any fake testimonials; keep NAP"
```

Prioritize categories that AI 411 gets asked about most (bars, cafes, food, trades) and pages missing `tel:` when a phone exists in JSON.

## Related repo pieces

- Business source: `gainesville-no-website/gainesville_no_website.json`  
- Sites: `generated-sites/*.html`  
- Surgical edits: `mcp-server/siteedit.py`  
- Knowledge search: `mcp-server/knowledge.py`  
- Demo URL hash: `voice-agent/businesses.py` + `hosting/Dockerfile`  
- Directory index: `index.html`  

## Design references (human study, not clone)

- Gallery: https://highsierraloft.github.io/coca-cola-zero-landing-pages/  
- Strong internal exemplars: `generated-sites/baby-j-s-bar.html`, `bob-s-barber-shop.html`, `shockwave-electricians.html`  
- Weak exemplars (candidates for improve): very small files under ~11KB with little sectioning  

Do not copy proprietary layouts from brand bake-offs; transfer **principles** only.
