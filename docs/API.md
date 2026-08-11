# FMWS voice-agent HTTP API

Base URL (production): `https://voice.flmanbiosci.net`  
Auth: Twilio signature on `/voice/*` and `/sms/*` (unless `VALIDATE_TWILIO_WEBHOOKS=0`).  
Public JSON APIs below are unauthenticated (rate-limit at edge if abused).

CORS: controlled by `CORS_ALLOW_ORIGINS` (comma-separated).

---

## Health

### `GET /health`

```json
{ "ok": true, "active_calls": 0, "active_sms_sessions": 0 }
```

(`active_sms_sessions` present when SMS session map is enabled.)

---

## Onboarding / customers

### `POST /api/onboarding/register`

Queue a phone for onboarding callback (AI 411 web form).

**Request**

```json
{
  "phone": "+13555550100",
  "business_name": "Cool Cafe",
  "contact_name": "Alex",
  "email": "alex@coolcafe.example",
  "source": "ai411_web"
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `phone` | yes | Normalized to E.164 (`+1…`) |
| `business_name` | no | |
| `contact_name` | no | |
| `email` | no | |
| `source` | no | default `ai411_web` |

**Response 200**

```json
{
  "ok": true,
  "message": "You are on the list — we will call shortly to design your free demo site.",
  "customer": { "id": "cust-…", "phone": "+1…", "status": "callback_queued", "…" : "…" },
  "voice_number": "+1…"
}
```

**Errors:** `400` invalid phone / bad status.

**Side effects:** writes `CUSTOMERS_PATH`; status `callback_queued`.

---

### `GET /api/onboarding/customers`

List registry rows (desk / builder). Optional query:

| Query | Notes |
|-------|--------|
| `status` | Filter exact status string |
| `limit` | Default 100, max 1000 |

**Response**

```json
{
  "ok": true,
  "count": 2,
  "customers": [ { "phone": "+1…", "status": "…", "…" : "…" } ]
}
```

Protect at the edge in hostile environments (currently open on voice host).

---

### `POST /api/billing/mark-paid`

Mark customer paid → future routing `owner_updates` (when `AGENT_MODE=auto`).

**Request**

```json
{
  "phone": "+13555550100",
  "stripe_customer_id": "cus_…"
}
```

**Response**

```json
{ "ok": true, "customer": { "status": "active_owner", "…" : "…" } }
```

Wire Stripe webhooks to this endpoint (or an adapter that calls it).

---

## Twilio voice

All require valid `X-Twilio-Signature` when validation is on.

| Method | Path | Role |
|--------|------|------|
| POST | `/voice/inbound` | Inbound call TwiML |
| POST | `/voice/outbound?slug=` | Outbound TwiML for dialer |
| POST | `/voice/turn` | Pipeline STT turn |
| POST | `/voice/status` | Call completed → flush transcript |
| WS | `/voice/stream` | Grok-realtime media |

TwiML / Media Streams details: `voice-agent/README.md`.

---

## Twilio SMS

| Method | Path | Role |
|--------|------|------|
| POST | `/sms/inbound` | Inbound SMS → agent reply (Messaging TwiML) |

Sessions keyed by From number; TTL `SMS_SESSION_TTL_S` (default 3600).

---

## Site-tracker (desk) APIs

Base: `https://sites.floridamanweb.online` (Authentik session).

| Method | Path | Role |
|--------|------|------|
| GET | `/api/meta` | Statuses, base_url, user |
| GET | `/api/sites` | Demo catalog + CRM state (includes `phone` from customer registry) |
| GET | `/api/sites/{hash}` | Detail + notes + `phone` |
| POST | `/api/sites/{hash}/status` | `{ "status": "Contacted" }` |
| POST | `/api/sites/{hash}/note` | `{ "body": "…" }` |
| POST | `/api/sites/{hash}/notify` | Send "site updated" SMS to customer (auto-resolves phone from registry, proxies to voice `/api/sms/notify-updated`) |
| GET | `/api/customers` | Funnel via voice `CUSTOMERS_API` |
| GET | `/api/customers/{phone}` | One customer + builder_brief path |
| GET | `/healthz` | Liveness |

### `POST /api/sites/{hash}/notify`

Looks up the customer's phone from the registry by matching the site's demo URL
or slug. Returns `400` if no phone on file. Proxies to voice
`POST /api/sms/notify-updated` with `{phone, demo_url}`.

### `POST /api/sms/notify-updated` (voice agent)

Sends a Twilio SMS to the customer. Request:

```json
{ "phone": "+1…", "demo_url": "https://…", "message": "" }
```

If `message` is empty, uses: `"Hi from Florida Man Web Services! Your free demo
website has been updated and is ready to view: {url}. Reply or call {callback} to
take it live."`

---

## Customer statuses (registry)

`prospect` · `callback_queued` · `onboarding` · `requirements_ready` ·
`building` · `demo_ready` · `sales_ready` · `paid` · `active_owner` ·
`churned` · `do_not_call`

---

## Internal Python API (customers module)

Import path: monorepo `mcp-server/customers.py` (on `sys.path` in voice).

| Function | Purpose |
|----------|---------|
| `register_callback(phone, …)` | Web signup |
| `save_requirements(phone, requirements, summary, …)` | End interview |
| `write_builder_brief(phone)` | Markdown for coding agent |
| `mark_demo_ready(phone, demo_url, …)` | Ready for sales call |
| `mark_paid(phone, …)` | Owner mode |
| `resolve_mode(phone, direction=…, env_mode=…)` | Agent mode string |
| `list_customers(status=, limit=)` | Desk listing |
| `get(phone)` | Single row |

---

## Builder sync script

```bash
# monorepo
./scripts/sync-customers-for-builder.sh
# env overrides:
#   CUSTOMERS_API=https://voice.flmanbiosci.net
#   CUSTOMERS_LOCAL_PATH=./data/customers.json
```

Fetches `GET /api/onboarding/customers` and writes a phone-keyed JSON file for
offline coding agents.

---

*Keep this file in sync when adding routes in `voice-agent/server.py` or `site-tracker/app.py`.*
