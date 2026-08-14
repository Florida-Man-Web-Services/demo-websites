# FMWS product architecture

Florida Man Web Services (FMWS) — Gainesville demo sites, voice/SMS agent, AI 411
public line, onboarding interview, site desk, and owner updates.

**Repo:** `/home/noahtjones/demo-websites` (GitHub `Florida-Man-Web-Services/demo-websites`)  
**IAC:** `/home/noahtjones/iac` — Flux `rke2/tooling/flux/theswamp/`  
**Cluster NS:** `theswamp` (FMB can Deploy/Secret/PVC; HTTPRoute/DNSRecord need Flux)

---

## 1. System context

```text
                    Internet
                       │
         ┌─────────────┼──────────────────────────────┐
         ▼             ▼                              ▼
  ai411.floridamanweb  floridamanweb.online      voice.flmanbiosci.net
  .online              /<hash>/                  (Twilio + APIs)
         │             │                              │
         │             │                              ├─ /voice/*  (phone)
         │             │                              ├─ /sms/inbound
         │             │                              ├─ /api/onboarding/*
         ▼             ▼                              └─ /api/billing/*
   demo-sites nginx                         voice-agent Deployment
   (static HTML)                            PVC voice-agent-data
         │                                  CUSTOMERS_PATH=/data/customers.json
         │                                            │
         │         sites.floridamanweb.online         │ CUSTOMERS_API (in-cluster)
         │                   │                        │
         │                   ▼                        │
         │            Authentik full-proxy            │
         │                   │                        │
         │                   ▼                        ▼
         │            site-tracker                 call-log / calldb
         │            (desk CRM)                   builder-briefs/
         │                                         customer-memory/
         └─────────────────────────────────────────────┘
                              │
                    mcp-server (optional remote MCP)
                    generated-sites/ (git + hosting image)
```

### Related products (not this monorepo)

| Product | Host / repo |
|---------|-------------|
| PeptOdyssey platform | `peptodyssey.flmanbiosci.net` / `~/u4u-engine` |
| Company marketing | `flmanbiosci.net` |
| Arete ops | `~/arete-holdings-llc` (FMWS as cash-engine venture) |

---

## 2. Domain map

| Host | Auth | Backend | Purpose |
|------|------|---------|---------|
| `ai411.floridamanweb.online` | **Public** | `demo-sites` → `/ai411/` | Phone callback form |
| `floridamanweb.online` | **Public** | `demo-sites` | Hashed demo pages |
| `www.floridamanweb.online` | Public | redirect → apex | Canonical |
| `sites.floridamanweb.online` | **Authentik** | site-tracker | Internal desk (canonical) |
| `sites.flmanbiosci.net` | redirect | → sites.floridamanweb.online | Legacy |
| `voice.flmanbiosci.net` | Twilio signature | voice-agent | Voice/SMS/API |
| `mcp.flmanbiosci.net` | Bearer | demo-mcp | Remote MCP tools |

TLS: `*.floridamanweb.online` via cert-manager secret `cf-floridamanweb-wildcard-cert-secret`.

---

## 3. Components

### 3.1 Demo sites (`generated-sites/`, `hosting/`)

- One self-contained HTML file per business slug.
- Production URL: `https://floridamanweb.online/<sha256(file)[:12]>/`
- Hash lockstep: `hosting/Dockerfile` ↔ `voice-agent/businesses.py` `demo_site_hash()`.
- AI 411 landing baked at `/ai411/index.html` in the same image.

### 3.2 Voice agent (`voice-agent/`)

| Piece | Role |
|-------|------|
| `server.py` | FastAPI: Twilio webhooks, SMS, public APIs, CORS |
| `agent.py` | LLM brain, tools, mode switch, call routing |
| `ai411.py` | Directory / events / broadcasts persona |
| `onboarding.py` | Requirements interview persona |
| `owner_updates.py` | ChangeRequest intake for paid owners |
| `unified.py` | AI411 + owner tools by caller ID (legacy pin) |
| `mcp_bridge.py` | inproc/HTTP dispatch to mcp-server stores |
| `calldb.py` | SQLite calls + transcripts (+ CSV dual-write) |
| `customers` (mcp-server) | Lifecycle registry + mode resolve |
| `customer_memory.py` | Honcho or local per-phone memory |
| `mailer.py` | Resend/SMTP demo-link email |
| `realtime.py` | Grok realtime media bridge |

**Live deploy env (theswamp, 2026-08-11):**

| Env | Value |
|-----|--------|
| `PUBLIC_BASE_URL` | `https://voice.flmanbiosci.net` |
| `VOICE_BACKEND` | `grok-realtime` |
| `AGENT_MODE` | `ai411` *(until image supports `auto`)* |
| `CUSTOMERS_PATH` | `/data/customers.json` |
| `BUILDER_BRIEFS_DIR` | `/data/builder-briefs` |
| `MEMORY_DIR` | `/data/customer-memory` |
| `CALL_DB` | `/data/call-log.db` |
| `CALL_LOG` | `/data/call-log.csv` |
| Secrets | `voice-agent-keys` (BW) + `voice-agent-extra` (Honcho) |

### 3.3 MCP server (`mcp-server/`)

Pure stores + FastMCP tools: lookup, knowledge, events, callers, broadcasts,
changerequests, siteedit/sitepr, **customers**.

### 3.4 Site tracker (`site-tracker/`)

Authentik-guarded CRM. Lists demos + **customers funnel** via:

- `CUSTOMERS_API=http://voice-agent.theswamp.svc.cluster.local:8035` (preferred)
- fallback file `CUSTOMERS_PATH` if API down

### 3.5 Customer registry (`mcp-server/customers.py`)

JSON map `phone_e164 → Customer` on voice PVC (source of truth).

**Statuses:** `prospect` → `callback_queued` → `onboarding` → `requirements_ready` →
`building` → `demo_ready` / `sales_ready` → `paid` / `active_owner` → optional
`churned` / `do_not_call`.

---

## 4. Agent modes

| Mode | When | Tools (summary) |
|------|------|-----------------|
| **ai411** | Default public / unknown phone | Directory, events, broadcasts, SMS links |
| **onboarding** | Signup / interview queue | Profile, save answers, finalize requirements, builder brief |
| **sales** | Demo ready or outbound slug dial | Demo SMS/email, outcome log, Stripe link in prompt |
| **owner_updates** | Paid / active_owner | ChangeRequests, outline, apply local HTML; **auth_level** gates writes |
| **unified** | Pinned env | AI411 + owner if caller ID matches business phone |
| **auto** | Desired prod | Per-call `customers.resolve_mode` (needs new image) |

**Owner identity (phased):** F1 Twilio CID ∈ `trusted_phones`; F2 passive speaker
verify after enrollment; step-up OTP for high-risk. Spec:
[superpowers/specs/2026-08-14-owner-voice-auth-design.md](./superpowers/specs/2026-08-14-owner-voice-auth-design.md).
Server-side gates in `mcp_bridge` / stores — not LLM-only.

Resolution (when `AGENT_MODE=auto`):

```text
outbound + slug          → sales
status paid|active_owner → owner_updates
status onboarding queue  → onboarding
status demo/sales ready  → sales
else                     → ai411
```

Code: `customers.resolve_mode` → `agent.resolve_call_mode` → `server._make_state`.

---

## 5. Product funnel (end-to-end)

See also [PRODUCT_LOOP.md](./PRODUCT_LOOP.md) (ops-oriented).

```text
1. Visitor → ai411.floridamanweb.online
2. POST /api/onboarding/register {phone, business_name?, email?}
3. customers.status = callback_queued
4. Call/SMS → onboarding interview (open-ended)
5. finalize_requirements + queue_website_build → builder brief on PVC
6. Coding agent builds generated-sites/<slug>.html (+ GitHub / sitepr)
7. mark_demo_ready(phone, demo_url, stripe_payment_link?)
8. Sales call: demo link + Stripe Payment Link
9. Stripe paid → POST /api/billing/mark-paid → active_owner
10. Future calls → owner_updates
```

---

## 6. Data on voice PVC (`voice-agent-data`)

| Path | Contents |
|------|----------|
| `/data/customers.json` | Customer registry (source of truth) |
| `/data/builder-briefs/` | Markdown briefs for coding agents |
| `/data/customer-memory/` | Local memory fallback (if no Honcho) |
| `/data/call-log.csv` | Outcome CSV dual-write |
| `/data/call-log.db` | SQLite calls + transcripts |
| `/data/audio_cache/` | TTS cache (pipeline mode) |

**RWO Longhorn:** only voice-agent mounts this volume. Tracker/builders use **HTTP API**, not a second mount.

---

## 7. Memory (Honcho)

Product note “hombre” → **Honcho**.

| Config | Behavior |
|--------|----------|
| `HONCHO_API_KEY` set | HTTP append/recall per phone session |
| Missing / error | Local JSON under `MEMORY_DIR` |

Live: Secret `voice-agent-extra` in `theswamp` (not git). Prefer Bitwarden field later.

---

## 8. IAC inventory (FMWS slice)

| Manifest | Purpose |
|----------|---------|
| `kube-system/floridamanweb-dnsrecord.yaml` | apex, www, **ai411**, **sites** A records |
| `kube-system/floridamanweb-cert.yaml` | `*.floridamanweb.online` |
| `theswamp/httproute-ai411.yaml` | Public AI411 host |
| `theswamp/httproute-tracker.yaml` | Desk @ sites.floridamanweb.online |
| `theswamp/httproute-tracker-legacy-redirect.yaml` | flmanbiosci → floridamanweb |
| `theswamp/httproute-voice.yaml` | voice.flmanbiosci.net |
| `theswamp/deployment-voice.yaml` | Voice env + PVC |
| `theswamp/deployment-tracker.yaml` | Desk + CUSTOMERS_API |
| `theswamp/external-secret-voice.yaml` | Twilio/API keys from Bitwarden |
| `theswamp/external-secret-voice-extra.yaml` | Staged Honcho/Stripe/email (optional) |
| `authentik/blueprints/providers-sitetracker.yaml` | external_host desk URL |
| `authentik/blueprints-configmap.yaml` | **Must mirror** blueprint |

PR track: `hwcopeland/iac` **#96** branch `feat/theswamp-authentik-flmanbiosci`.

---

## 9. Security / compliance

- Twilio webhooks: `X-Twilio-Signature` (`VALIDATE_TWILIO_WEBHOOKS=1`).
- Voice APIs for register are **public** (CORS-limited origins in prod).
- Desk is Authentik FMB | Infrastructure only.
- Demo pages are public by unguessable hash (not auth).
- **Owner updates auth (phased):** F1 caller ID ∈ `trusted_phones`; F2 passive
  speaker verification post-consent enrollment; step-up OTP for high-risk; gates in
  code not prompt — [2026-08-14-owner-voice-auth-design.md](./superpowers/specs/2026-08-14-owner-voice-auth-design.md).
- TCPA: outbound sales remains human-confirmed dialer; AI disclosure in prompts.
- A2P/10DLC still required for bulk SMS under FMWS entity (formation gate).
- Never commit API keys; Honcho/Stripe live only in Secrets/Bitwarden.
- Biometric retention: templates preferred over raw audio; delete path on offboarding;
  counsel before multi-state scale of voiceprints.

---

## 10. Doc index

| Doc | Audience |
|-----|----------|
| [PRODUCT_LOOP.md](./PRODUCT_LOOP.md) | Funnel steps, APIs, builder |
| [OPS_CLUSTER.md](./OPS_CLUSTER.md) | DNS, Authentik, Flux, secrets, runbooks |
| [API.md](./API.md) | HTTP API reference |
| [superpowers/specs/2026-08-14-owner-voice-auth-design.md](./superpowers/specs/2026-08-14-owner-voice-auth-design.md) | Owner phone + voice 2FA |
| [../voice-agent/README.md](../voice-agent/README.md) | Local voice dev |
| [../mcp-server/README.md](../mcp-server/README.md) | MCP tools |
| [../README.md](../README.md) | Monorepo map |

---

*Last verified against cluster + repo: 2026-08-11.*
