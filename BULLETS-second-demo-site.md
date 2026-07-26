# BULLETS — second demo site / next catalog work

Ops backlog (not a full rebuild). Pick one slice at a time.

- **tel: coverage:** ~51 of 246 `generated-sites/*.html` lack a `tel:` link; backfill from `gainesville-no-website/gainesville_no_website.json` where phone exists (siteedit-friendly + AI 411).
- **Improve-wave craft:** finish/ship thin WIP pages (see dirty worktree simplify diffs); prioritize bars/cafes/food/trades for AI 411; score with `docs/site-generation/quality-rubric.md`.
- **Catalog hygiene:** regenerate or script-sync `index.html` when slugs added; add `og:url` / canonical on catalog if public SEO matters; retire or document root `Dockerfile` arcade-bar sample vs `hosting/`.
- **Second “hero” vertical demo:** ship one polished category package (e.g. full Restaurants or Gyms set beyond single stubs) with unique design systems + complete NAP hooks — use as sales leave-behind, not a monorepo rewrite.
- **Owner-update path drill:** end-to-end ChangeRequest → `apply_change_request` → `open_site_update_pr` on one real slug with `SITE_PR_ENABLED` staged; document happy path in voice-agent README if gaps remain.
- **Do not touch:** arete-holdings-llc (out of scope for this monorepo).
