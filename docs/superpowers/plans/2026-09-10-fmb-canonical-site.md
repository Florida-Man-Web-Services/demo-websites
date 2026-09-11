# FMB canonical site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Operator ordered inline execution (“do it”).

**Goal:** Ship a multi-page Florida Man Bioscience marketing set (dossier / bound-report) on floridamanweb vanity, AI411-editable per page, ready for a later `.com` Worker.

**Architecture:** Top-level `generated-sites/<slug>.html` per IA row; sidecar mark; Dockerfile vanity map; `customers.owned_slugs`; owner_updates asks which page. No Next rewrite. No Gainesville catalog cards. `.com` Worker waits on zone token.

**Tech Stack:** static HTML, FastMCP `customers.py`, pytest, nginx vanity COPY.

## Global Constraints

- Copy/claims only from live public FMB pages (`u4u-engine/frontend` products + team + privacy). No new efficacy language.
- No `tel:` / no manager CID on HTML.
- Nanodisk = research / held-out.
- Paper `#ece8e1`, ink `#1c1915`, Fraunces + Source Sans 3.
- Catalog denylist: `florida-man-bioscience` and `fmb-*`.
- Inbound AI411 only.

---

### Task 1: owned_slugs auth

**Files:** `mcp-server/customers.py`, `mcp-server/tests/test_owner_auth.py`, `voice-agent/owner_updates.py`

- [ ] Failing tests: owner CID can write every owned slug; stranger `not_owner`; unlisted slug `slug_mismatch`.
- [ ] `slugs_owned(customer)`; `owners_of_slug` membership; authorize uses it.
- [ ] Prompt: if multiple owned slugs, ask which page before outline.

### Task 2: pages + vanity

**Files:** `generated-sites/florida-man-bioscience.html`, `fmb-*.html`, sidecar `mark.png`, `hosting/Dockerfile`, `README.md`, `generated-sites/CATALOG_EXCLUDE`

- [ ] Download live mark.png.
- [ ] Render 11 dossier pages from public copy.
- [ ] Dockerfile COPY map from spec.
- [ ] Exclude stems from catalog count docs.

### Task 3: grant + verify

- [ ] pytest owner auth.
- [ ] No phone digits on FMB HTML.
- [ ] PVC grant if cluster reachable (CID not in git).
- [ ] `.com` Worker blocked without Cloudflare token.
