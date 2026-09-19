# Gainesville AI 411 — product feature list (catch-up, 2026-08-14)

Live: `voice.flmanbiosci.net` health OK · `AGENT_MODE=auto` · landing
`https://ai411.floridamanweb.online/ai411/` · image pin on theswamp.

## What AI 411 is

A **Gainesville voice AI** on a public phone line (default mode when unknown
caller). Not a sales pitch first. Sister funnel: web form → onboarding interview
→ free demo website → sales/Stripe → owner updates (Florida Man Web Services).

## Features (current product)

### Answer & identity
- Fixed first line: **“A411 here.”** (realtime-forced + pipeline short-circuit)
- Identifies as AI when asked; emergency redirect to 911
- `AGENT_MODE=auto` routes by customer registry status

### Local intelligence
- **Business lookup** + site-text knowledge search (demo pages)
- **Events**: seed + community broadcasts
- **Event discovery flow**: interest first (or memory) → if long list,
  **category counts** (`summarize_event_categories`) → drill-down by category
  (`search_events category=`) → ≤2–3 titles + SMS links
- Community **event/notice posting** (moderated tools)
- **SMS link delivery** of event/business URLs

### Memory (phone-keyed)
- Caller profiles: name, interests, topics, notes
- Consent gate with **bool/string coercion** + auto-enable when saving prefs
- **MEMORY SNAPSHOT** injected into system prompt at call start
- Honcho optional + local `/data/customer-memory`
- “Forget me” hard-delete

### People layer (QOTD)
- Question of the day (people-oriented)
- Answer recording → long-horizon **people profile**
- Community **suggest QOTD**
- **match_events_for_profile** — events for like-minded hangouts

### Product loop (FMWS cash engine)
- Landing form → `POST /api/onboarding/register`
- Onboarding interview mode → requirements + builder briefs on PVC
- Sales mode (demo + Stripe payment link config)
- Owner updates mode after paid
- Site tracker desk (`sites.floridamanweb.online`, Authentik)
- Notify-updated SMS button path

### Owner auth (in progress / phased)
- Design: phone F1 trusted + passive F2
- Phase 0: trusted phones + CR ownership gates
- Phase 1: `CallState.auth_level` + tool gates (`voice_auth`)

### Reliability fixes shipped this week
- Voice image **bakes mcp-server stores** (fixed instant hangup /
  `ModuleNotFoundError: customers`)
- Register API 200; health reports `customers_registry`
- Fail-soft mode routing if packaging drifts

## Marketing angles (grounded)

1. Group-chat replacement for “what’s going on tonight?”
2. Yellow Pages / 411 nostalgia + modern voice AI
3. Interest → categories → details (no dump)
4. Consent memory that actually remembers
5. QOTD people-matching → real hangouts
6. Local shops: free demo website callback (inbound/web form preferred)
7. Florida Man Web Services brand (local, irreverent, useful)

## Compliance (non-negotiable in ads)

- Prefer **inbound** call + **web form** callback
- No claims of TCPA-safe cold outbound AI voice without counsel program
- No fabricated efficacy stats; no fake testimonials
- Twilio ≠ National DNC list maintenance
