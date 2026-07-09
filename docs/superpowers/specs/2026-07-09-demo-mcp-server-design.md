# Demo MCP Server — Design

**Date:** 2026-07-09
**Status:** Approved

## Purpose

An MCP server that gives AI sales and customer-support agents — initially the
Florida Man Web Services voice agent being set up on console.x.ai — everything
they need to help customers: business lookups, demo-site URLs, the sales pitch
(pricing, objection handling, compliance rules), past call history, and a way
to log call outcomes.

## Scope

- **Clients:** the xAI (console.x.ai) voice agent only. Remote access over
  Streamable HTTP; no stdio transport.
- **Capabilities:** read lookups plus append-only call-outcome logging.
  **No SMS sending** — the riskiest action stays out of cloud hands.
- **Out of scope:** editing business data, sending messages of any kind,
  multi-tenant auth, admin UI.

## Architecture

```
xAI voice agent (console.x.ai)
        │  Streamable HTTP + Bearer token
        ▼
Cloudflare (proxied) → hwcopeland-gateway (*.flmanbiosci.net wildcard listener)
        ▼
mcp.flmanbiosci.net → Service/Deployment in `theswamp` namespace
        │  demo-mcp container (FastMCP, port 8036)
        │  imports voice-agent/businesses.py + config.py
        ▼
outreach-data.csv · call-order.csv · gainesville_no_website.json  (baked into image)
call-log.csv                                                       (Longhorn PVC)
```

- **Code lives in this repo** under `mcp-server/` (`server.py`,
  `requirements.txt`, `Dockerfile`, README). The repo root already has a
  `Dockerfile` (arcade-bar-south demo), so the image builds with
  `docker build -f mcp-server/Dockerfile .` — context at the repo root since
  the image needs `voice-agent/`, `correspondences/`, and
  `gainesville-no-website/`.
- **Framework:** official `mcp` Python SDK (FastMCP), Streamable HTTP
  transport, port 8036.
- **Data reuse:** the server adds `voice-agent/` to `sys.path` and imports
  `businesses.py` (loading, slugify, phone normalization, close-slug
  suggestions) and `config.py` (file paths, env overrides). One source of
  truth shared with the Twilio agent.

## Tools

1. **`lookup_business(query)`** — accepts a name, slug, or phone number.
   Returns the full profile: name, category, address, phone, rating,
   `demo_url`, Google Maps link, and a shared-demo note where applicable.
   On a miss: `{found: false, suggestions: [...]}` using the existing
   close-slug logic so the agent can ask "did you mean…?".
2. **`get_pitch_info()`** — no arguments. Returns the static sales knowledge:
   - Business identity: **Florida Man Web Services**, owner Noah, callback
     number (from `OWNER_CALLBACK_NUMBER`).
   - The offer: free demo already built and live; **$999 flat one-time fee**
     to go live (own domain + Google listing pointing at it); no monthly fees.
   - Objection-handling lines distilled from `correspondences/phone-script.md`.
   - Compliance rules: identify as an AI in the first sentence; honor
     do-not-call permanently.
   - SMS caveat: this server cannot text links — collect an email or offer
     Noah's callback number instead.
3. **`get_call_history(business)`** — past `call-log.csv` rows for that
   business (matched by slug or phone), so the agent knows prior contact
   before pitching.
4. **`log_call_outcome(business, outcome, email?, callback_time?, notes)`** —
   appends to `call-log.csv` with the same columns and outcome enum as
   `voice-agent/agent.py` (`interested`, `callback_requested`, `sent_sms`,
   `not_interested`, `do_not_call`, …). The `call_sid` column gets an `XAI-`
   prefix to distinguish channels. `sent_sms` remains in the enum for log
   compatibility even though this server cannot send SMS.

## Auth

- Static bearer token in `MCP_AUTH_TOKEN`.
- ASGI middleware rejects requests without `Authorization: Bearer <token>`
  with 401 before MCP routing.
- The HTTPRoute must **not** use the `authentik.home.arpa/enabled`
  forward-auth annotation — the xAI client cannot do OIDC.

## Data handling

- Business/outreach reads load fresh from the baked-in CSVs per request.
  Data updates ship via image rebuild (the data changes rarely).
- `call-log.csv` lives on a small Longhorn PVC mounted at the `CALL_LOG` path
  (config already supports the env override). Writes are append-only with the
  same file-locking discipline as `agent.py`.
- The server never mutates business data or correspondence files.

## Error handling

Tools never raise into the transport; the agent always gets structured,
speakable results:

- Unknown business → `{found: false, suggestions: [...]}`.
- Invalid outcome value → error message listing valid outcomes.
- Missing/unreadable data file → "data unavailable, offer Noah's callback
  number" so the voice agent degrades gracefully mid-call.

## Deployment (hwcopeland's RKE2 cluster)

- **Namespace:** `theswamp` (existing; `jonesnoaht` has namespace-admin per
  its `ACCESS.md`).
- **Hostname:** `mcp.flmanbiosci.net` on the gateway's `*.flmanbiosci.net`
  wildcard listener (same pattern as `app.flmanbiosci.net`), wildcard cert,
  Cloudflare-proxied.
- **Image:** `zot.hwcopeland.net/florida-man-bioscience/demo-mcp`, built by a
  GitHub Actions workflow **in this repo** (ZOT creds as repo secrets), same
  pipeline as u4u-engine.
- **Flux:** manifests added to `~/iac` under `rke2/tooling/flux/theswamp/`
  and its `kustomization.yaml`; Flux reconciles. Flux image automation tracks
  the image so pushes to `main` auto-deploy.
- **New manifests:** `deployment-mcp.yaml`, `service-mcp.yaml`,
  `httproute-mcp.yaml`, `external-secret-mcp.yaml` (MCP_AUTH_TOKEN from a
  Bitwarden item via the `bitwarden-fields` ClusterSecretStore),
  `pvc-mcp.yaml` (Longhorn, for the call log).

## Testing

- **Pytest:** lookup by exact name / slug / phone / fuzzy miss with
  suggestions; pitch info contains the $999 price and compliance lines;
  outcome logging appends a well-formed row (temp CSV via `CALL_LOG` env
  override); invalid outcome rejected with the valid list; auth middleware
  rejects missing/bad tokens.
- **Manual smoke test:** run locally, exercise with
  `npx @modelcontextprotocol/inspector`, then verify through
  `mcp.flmanbiosci.net` and finally from the console.x.ai agent config.

## Decisions log

- Clients: xAI agent only (no stdio) — user choice.
- Capabilities: read + log only, no SMS — user choice.
- Hosting: originally standalone + ngrok; **revised** to hwcopeland's RKE2
  cluster, `theswamp` namespace — user directed after pointing at `~/iac`.
- Pricing: agent quotes a real price; **$999** flat one-time fee (raised from
  the initially selected $250).
- Business name: **Florida Man Web Services**.
