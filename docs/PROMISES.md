# AI 411 product promises ledger (G01)

Every promise the public surfaces make today, mapped to the mechanism that
delivers it. Status: **delivered** (works end to end) · **partial** (works with
caveats) · **manual** (a human step is required) · **gated** (waiting on a
business/legal enablement decision). Evidence = code/test/URL. Audited
2026-09-21 against `hosting/ai411/index.html` + voice prompts.

## Business funnel

| # | Public promise | Where made | Delivered by | Status | Evidence |
|---|----------------|-----------|--------------|--------|----------|
| 1 | "Call AI 411 and ask for the local directory" — business lookup | Landing | Voice `ai411` mode: `lookup_business`, `search_business_knowledge` | partial — lookup over 261 generated sites + knowledge cache; no browsable web directory | voice-agent/ai411.py; /health |
| 2 | "You can also ask about events" | Landing | `search_events`, `summarize_event_categories`, `get_event`, `list_event_sources` | partial — search works; source freshness/expiry policy not operator-visible (G22) | voice-agent/ai411.py |
| 3 | "Free demo website callback. We'll call from Florida Man Web Services" | Landing form | `POST /api/onboarding/register` → `callback_queued` → `callback_dial.place_onboarding_callback` | partial — dialing works; dedupe (24 h) shipped 2026-09-21; durable job queue + retry policy still pending (G13) | voice-agent/callback_dial.py |
| 4 | "The first call is a short AI conversation about your business and demo" | Landing form | `onboarding` mode interview: `save_onboarding_answer`, `finalize_requirements`, `queue_website_build` | delivered | voice-agent/onboarding.py |
| 5 | "Missed the call? Call AI 411 back and say you're returning" | Landing form | `customers.resolve_mode`: `callback_queued` routes to onboarding on inbound | delivered | mcp-server/customers.py |
| 6 | Website build from requirements | Landing ("what happens next") | Builder brief on PVC → **built by Hermes agent waves** | **manual** — no autonomous builder service (G16) | docs/PRODUCT_LOOP.md |
| 7 | Demo delivered by link | implied | `send_demo_link_sms` / `send_demo_link_email` (sales mode) | partial — delivery outcomes logged, not provider-verified (G17) | voice-agent/agent.py SALES_TOOLS |
| 8 | "Going live costs $999 a month" | Sales prompt | Sales conversation states price | delivered (spoken) — payment itself is **manual**: Stripe payment link in prompt + `POST /api/billing/mark-paid` by operator (G11/G18) | voice-agent/agent.py:398 |

## Personal pages

| # | Public promise | Where made | Delivered by | Status | Evidence |
|---|----------------|-----------|--------------|--------|----------|
| 9 | "A simple public page from interests you've shared — not your phone number" | Landing | opt-in (voice tool or `POST /api/personal-pages/opt-in`) → `/me/{slug}` render | partial — published fields are the approved subset; full removal-propagation audit pending (G05/G25) | voice-agent/server.py:1015 |
| 10 | "Rebuilds about once a day. Take it down anytime: call AI 411 and say 'take it down'" | Landing | rebuild via `scripts/regen_personal_pages.py` (**manual — not scheduled**, G25) + opt-out (`opt_out_personal_page`, `POST /api/personal-pages/opt-out`) | partial — take-down is delivered; the daily cadence is not automated | mcp-server/personal_pages.py |
| 11 | "Requires memory consent (enabled when you opt in here)" | Landing | memory consent gate before profile build | delivered | customer_memory.py |
| 12 | "Will not show your phone number" | Landing | phone excluded from rendered page | delivered | server.py `/me/{slug}` |

## Community features (voice, ai411 mode)

| # | Capability | Status | Notes |
|---|-----------|--------|-------|
| 13 | Community broadcasts (events/notices) by callers | partial | submit + report + delete-own exist; moderation queue is primitives-only (G10) |
| 14 | Question of the day | partial | suite works; editorial/rollover policy undefined (G24) |
| 15 | Event interest + matching | partial | match loop works; contact-sharing deliberately not promised — never share one caller's info with another (G23) |
| 16 | Activities search | partial | freshness/availability policy pending (G22) |

## Standing constraints (truth-in-advertising)

- **Free vs paid:** demo sites are free; going live is $999/mo. Never promise
  hosting, SEO results, or timelines beyond "the first call is short".
- **AI disclosure:** calls identify as AI 411. No claim of human agents.
- **Voice biometrics:** not advertised, not enabled (`voice_auth_vendor: none`).
- **A2P/10DLC:** pending FMWS entity formation gate — no bulk SMS promises.
- **Human transfer:** never promised; unsupported requests are declined
  honestly.
- **Privacy:** personal pages never show phone numbers; memory requires
  consent; caller memory is deletable (`forget_caller`).

## Registry notes

- New promise ⇒ new row here + acceptance test (G30) before it ships.
- Correcting a promise is a product decision (Noah) — do not silently redefine.
