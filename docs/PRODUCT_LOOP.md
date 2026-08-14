# FMWS product loop — operator guide

Lifecycle: **AI 411 (default) → web signup → onboarding interview → website build →
sales call (demo + Stripe) → paid owner updates**, with **Honcho** (or local)
per-customer memory.

Companion docs: [ARCHITECTURE.md](./ARCHITECTURE.md) · [OPS_CLUSTER.md](./OPS_CLUSTER.md) · [API.md](./API.md)

---

## 1. Goals

1. Unknown callers to the public number get **Gainesville AI 411**, not a cold sales pitch.
2. Anyone can request a **callback** from the AI 411 website by entering a phone number.
3. Callback runs an **onboarding interview** that captures website requirements.
4. Requirements become a **builder brief** for a coding agent (GitHub-capable).
5. When the demo is ready, a **sales** call delivers the link + **Stripe** payment link.
6. After payment, the same number reaches **owner_updates** for change requests.
7. Memory of each user persists across calls (Honcho preferred).

---

## 2. Domain map

| Host | Role |
|------|------|
| `ai411.floridamanweb.online` | Public landing + phone form (`hosting/ai411/`) |
| `floridamanweb.online/<hash>/` | Demo site pages |
| `sites.floridamanweb.online` | Internal desk (Authentik) — customers + demos |
| `sites.flmanbiosci.net` | Legacy → redirect to sites.floridamanweb.online |
| `voice.flmanbiosci.net` | Twilio voice/SMS + onboarding/billing APIs |

---

## 3. Mode routing

### 3.1 Desired production (`AGENT_MODE=auto`)

| Customer `status` | Inbound / SMS mode |
|-------------------|--------------------|
| *(no row)* | **ai411** |
| `prospect`, `callback_queued`, `onboarding` | **onboarding** |
| `requirements_ready`, `building`, `demo_ready`, `sales_ready` | **sales** |
| `paid`, `active_owner` | **owner_updates** |
| Outbound dialer with business `slug` | **sales** (always) |

Implementation: `mcp-server/customers.py` → `resolve_mode()`  
Wired at call start: `voice-agent/agent.py` `resolve_call_mode` + `server._make_state`.

### 3.2 Current production image (2026-08-11)

Cluster image only accepts `sales` | `ai411`.  
**Live:** `AGENT_MODE=ai411` so the public line is directory-first.  
After shipping a voice image that includes `auto` + onboarding modules, set:

```yaml
- name: AGENT_MODE
  value: auto
```

in `deployment-voice.yaml` and re-roll.

### 3.3 Pinning a single mode (dev)

```bash
AGENT_MODE=sales          # cold outreach only
AGENT_MODE=onboarding     # force interview
AGENT_MODE=owner_updates  # force owner desk
AGENT_MODE=unified        # AI411 + owner-by-caller-ID
```

---

## 4. Funnel step-by-step

### Step A — Web signup

1. User opens `https://ai411.floridamanweb.online/` (or `/ai411/` on demo-sites).
2. Form (`hosting/ai411/index.html`) POSTs JSON to  
   `https://voice.flmanbiosci.net/api/onboarding/register`.
3. Body:

```json
{
  "phone": "+13555550100",
  "business_name": "Cool Cafe",
  "contact_name": "",
  "email": "owner@example.com",
  "source": "ai411_web"
}
```

4. Registry: `status=callback_queued`, phone normalized to E.164.
5. Response includes `voice_number` for display.

**CORS:** `CORS_ALLOW_ORIGINS` includes `https://ai411.floridamanweb.online`.

### Step B — Onboarding call

1. Human or dialer places/receives call; with `auto`, mode = **onboarding**.
2. Agent tools (`voice-agent/onboarding.py`):

| Tool | Effect |
|------|--------|
| `get_customer_profile` | Load registry + memory |
| `save_onboarding_answer` | Patch requirements object mid-call |
| `finalize_requirements` | Save full brief, `status=requirements_ready` |
| `queue_website_build` | Write markdown brief under `BUILDER_BRIEFS_DIR` |
| `send_sms_links` / `log_call_outcome` / `end_call` | Local |

3. Interview is **open-ended**: business, audience, goals, pages, branding, content, must-haves, email.
4. Memory notes appended (Honcho or local).

### Step C — Website build

1. Brief path example: `/data/builder-briefs/cool-cafe-cust-abc123.md` (in-cluster).
2. Coding agent (Hermes / kanban / human):

```bash
# On build host
./scripts/sync-customers-for-builder.sh
# or pull brief:
kubectl -n theswamp exec deploy/voice-agent -- \
  cat /data/builder-briefs/<file>.md > /tmp/brief.md
```

3. Build `generated-sites/<slug>.html` per demo-websites landing rules (NAP truth).
4. Ship via git + hosting image / `sitepr` as appropriate.
5. Mark ready (Python or future API):

```python
import customers
customers.mark_demo_ready(
    "+13555550100",
    demo_url="https://floridamanweb.online/<hash>/",
    slug="cool-cafe",
    stripe_payment_link="https://buy.stripe.com/...",  # optional per-customer
)
```

### Step D — Sales call

1. Mode **sales**; prompt injects demo URL + payment link.
2. Tools: `send_demo_link_sms`, `send_demo_link_email`, `log_call_outcome`, `end_call`.
3. Prefer SMS for Stripe URL (hard to speak).
4. Log outcome (`interested`, `sent_sms`, `wants_email`, …).

### Step E — Payment → owner

1. Stripe Checkout / Payment Link completes.
2. Webhook or ops calls:

```bash
curl -X POST https://voice.flmanbiosci.net/api/billing/mark-paid \
  -H 'content-type: application/json' \
  -d '{"phone":"+13555550100","stripe_customer_id":"cus_..."}'
```

3. `status=active_owner`.
4. Later calls → **owner_updates** (ChangeRequests on their demo).
5. **Owner auth (phased):** Factor 1 = Twilio caller ID ∈ `trusted_phones`; Factor 2 =
   passive speaker verification after consented enrollment. Writes gated by
   `CallState.auth_level` in code (not prompt-only). High-risk actions (phone/billing/publish)
   use SMS OTP / liveness step-up. Full design:
   [superpowers/specs/2026-08-14-owner-voice-auth-design.md](./superpowers/specs/2026-08-14-owner-voice-auth-design.md).

---

## 5. Customer registry schema

File: `CUSTOMERS_PATH` (JSON object).

```json
{
  "+13555550100": {
    "id": "cust-xxxxxxxxxxxx",
    "phone": "+13555550100",
    "status": "callback_queued",
    "business_name": "Cool Cafe",
    "contact_name": "",
    "email": "owner@example.com",
    "category": "",
    "source": "ai411_web",
    "requirements": {},
    "requirements_summary": "",
    "demo_url": "",
    "slug": "",
    "stripe_payment_link": "",
    "stripe_customer_id": "",
    "builder_brief_path": "",
    "honcho_session_id": "",
    "trusted_phones": ["+135****0100"],
    "voice_auth": {
      "consent_version": "",
      "consented_at": null,
      "enrolled_at": null,
      "vendor": "none",
      "template_id": "",
      "quality": null
    },
    "delegates": [],
    "notes": "",
    "created_at": "2026-08-11T00:00:00+00:00",
    "updated_at": "2026-08-11T00:00:00+00:00"
  }
}
```

`trusted_phones` / `voice_auth` / `delegates` are **additive** (owner voice-auth design).
Empty `trusted_phones` ⇒ treat primary `phone` as sole trusted line.

API helpers: `register_callback`, `save_requirements`, `write_builder_brief`,
`mark_demo_ready`, `mark_paid`, `list_customers`, `get`, `resolve_mode`.

---

## 6. Shared data (no RWX)

Voice PVC is **ReadWriteOnce**. Other consumers **must not** mount it.

| Consumer | How it reads customers |
|----------|------------------------|
| voice-agent | Local file `CUSTOMERS_PATH` |
| site-tracker | `CUSTOMERS_API` → voice Service |
| Host builder / Hermes | `scripts/sync-customers-for-builder.sh` or `kubectl exec … cat` |

---

## 7. Memory (Honcho)

| Env | Purpose |
|-----|---------|
| `HONCHO_API_KEY` | Required for Honcho backend |
| `HONCHO_APP_ID` | Default `fmws-voice` |
| `HONCHO_BASE_URL` | Optional API base |
| `HONCHO_MESSAGE_URL` / `HONCHO_RECALL_URL` | Override paths if API shape differs |
| `MEMORY_DIR` | Local JSON fallback directory |

Onboarding tools call `customer_memory.append_note` / `recall` automatically.

**Live secret:** `kubectl -n theswamp get secret voice-agent-extra`  
**Do not commit keys.** Add Bitwarden custom field when ready for ExternalSecrets.

---

## 8. Environment reference

### Voice (production)

| Variable | Example / notes |
|----------|-----------------|
| `AGENT_MODE` | `ai411` now; `auto` after image upgrade |
| `CUSTOMERS_PATH` | `/data/customers.json` |
| `BUILDER_BRIEFS_DIR` | `/data/builder-briefs` |
| `MEMORY_DIR` | `/data/customer-memory` |
| `CALL_DB` | `/data/call-log.db` |
| `CALL_LOG` | `/data/call-log.csv` |
| `CALL_LOG_DUAL_WRITE_CSV` | `1` |
| `PUBLIC_BASE_URL` | `https://voice.flmanbiosci.net` |
| `AI411_PUBLIC_URL` | `https://ai411.floridamanweb.online` |
| `SITES_DESK_URL` | `https://sites.floridamanweb.online` |
| `CORS_ALLOW_ORIGINS` | ai411 + floridamanweb origins |
| `STRIPE_PAYMENT_LINK_DEFAULT` | Dashboard Payment Link |
| `EMAIL_FROM` + `RESEND_API_KEY` | Demo email |
| `TWILIO_*` | From `voice-agent-keys` |
| `HONCHO_*` | From `voice-agent-extra` |

### Tracker

| Variable | Value |
|----------|--------|
| `CUSTOMERS_API` | `http://voice-agent.theswamp.svc.cluster.local:8035` |
| `CUSTOMERS_PATH` | `/data/customers.json` (fallback only) |
| `DESK_PUBLIC_HOST` | `sites.floridamanweb.online` |
| `SITES_BASE_URL` | `https://floridamanweb.online` |

---

## 9. Twilio console checklist

| Webhook | URL |
|---------|-----|
| Voice | `{PUBLIC_BASE_URL}/voice/inbound` POST |
| Status | `{PUBLIC_BASE_URL}/voice/status` |
| Messaging | `{PUBLIC_BASE_URL}/sms/inbound` POST |

Signature validation on by default.

---

## 10. Desk / builder workflows

### List funnel

```bash
curl -sS https://voice.flmanbiosci.net/api/onboarding/customers | jq .
# or on desk (after Authentik): GET /api/customers
```

### Pull brief for coding agent

```bash
kubectl -n theswamp exec deploy/voice-agent -- ls -la /data/builder-briefs
kubectl -n theswamp exec deploy/voice-agent -- cat /data/builder-briefs/<file>.md
```

### Host sync

```bash
cd /home/noahtjones/demo-websites
./scripts/sync-customers-for-builder.sh
export CUSTOMERS_PATH=$PWD/data/customers.json
```

### Skills

- Landing HTML: Hermes skill **demo-websites** (NAP rules, improve waves).
- Voice modes pattern: `demo-websites` → `references/voice-agent-modes.md`.
- Cluster auth: **hwcopeland-cluster-auth**.
- FMB hosts/email: **flmanbiosci-ops**.

---

## 10b. AI 411 events feed (Visit Gainesville)

AI 411 `search_events` reads `EVENTS_PATH` (default `/data/events.json`). **No fake seed events** are auto-written — an empty file is valid. Real rows come from:

1. **community** — approved event broadcasts (`community-<id>`)
2. **visitgainesville** — public tribe REST ingest  
   `https://www.visitgainesville.com/wp-json/tribe/events/v1/events`  
   via `scripts/ingest_visitgainesville_events.py` (ids `vg-<tribe_id>`, local FL filter, replaces only that source, purges legacy `source=seed`).

Cron monitor: exit 0 + one stable `DIGEST …` stdout line. Optional manifest: [cronjob-visitgainesville-events.yaml](./cronjob-visitgainesville-events.yaml). After merge, one-shot PVC: purge seed rows then run ingest against the voice volume.

---

## 11. Tests

```bash
cd voice-agent
.venv/bin/python -m pytest \
  tests/test_customer_routing.py \
  tests/test_sms_email.py \
  tests/test_owner_updates.py \
  tests/test_agent_mode.py \
  tests/test_ai411_tools.py -q
```

---

## 12. Cluster cutover status (2026-08-11)

| Item | Status |
|------|--------|
| Secret `voice-agent-extra` (Honcho) | **Live** |
| Voice PVC paths (customers, briefs, memory) | **Live** |
| Tracker `CUSTOMERS_API` | **Live** |
| `AGENT_MODE=ai411` | **Live** (safe for current image) |
| `AGENT_MODE=auto` in image | **Pending** voice code + image build |
| DNS ai411 + sites | **IAC PR #96** (Flux) |
| HTTPRoutes + Authentik host | **IAC PR #96** |
| Stripe webhook → mark-paid | **Pending** wiring |
| Bitwarden fields for Honcho | **Pending** (manual Secret OK) |
| Owner voice auth (F1 harden + F2) | **Phases 0–2 shipped (F1 + auth_level + enroll/stub F2); 3–4 open |

---

## 13. Failure modes

| Symptom | Check |
|---------|--------|
| Voice CrashLoop `Unknown AGENT_MODE 'auto'` | Image too old → set `ai411`/`sales` |
| Call connects then hangs up in ~1s | `customers` missing from image — rebuild voice with `mcp-server/*.py` baked in; check logs for `ModuleNotFoundError: customers` |
| Form CORS error | `CORS_ALLOW_ORIGINS` + voice up |
| Register API 500/503 | Same customers packaging; `GET /health` → `customers_registry: true` |
| Tracker empty customers | Voice up; `CUSTOMERS_API` DNS inside cluster |
| Multi-Attach PVC error | Only one voice replica; wait for detach |
| Flux wiped deploy env | PR not merged; re-apply deploy YAML or merge Flux |
| Honcho missing in pod | `kubectl get secret voice-agent-extra`; envFrom optional secret |

---

*Last verified: 2026-08-11 against theswamp + monorepo.*
