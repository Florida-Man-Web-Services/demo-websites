# demo-mcp — MCP server for Florida Man Web Services agents

Gives AI sales/support agents (currently the xAI voice agent) business
lookups, the sales pitch, call history, and outcome logging for the
Gainesville demo-websites campaign.

## Endpoint

- URL: `https://mcp.flmanbiosci.net/mcp` (Streamable HTTP)
- Auth: `Authorization: Bearer <MCP_AUTH_TOKEN>` — token lives in the
  Vaultwarden item `demo-mcp-auth`
- Health: `GET /health` (no auth)

## Tools

| Tool | Purpose |
| --- | --- |
| `lookup_business(query)` | Profile + demo URL by name/slug/phone; suggestions on miss |
| `get_pitch_info()` | Offer ($999/month), objections, compliance rules |
| `get_call_history(business)` | Prior call-log rows for the business (this server's log only) |
| `log_call_outcome(business, outcome, notes, email?, callback_time?)` | Append to call-log.csv |
| `get_caller_profile(phone)` | Caller memory by phone; redacts when `consent.memory_ok` is false |
| `update_caller_profile(phone, patch)` | Create/merge name, prefs, consent, topics |
| `forget_caller(phone)` | Hard-delete caller profile ("forget me") |
| `add_caller_note(phone, note)` | Append freeform note (created if needed) |

## Local development

    python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python -m pytest tests/ -v
    MCP_AUTH_TOKEN=devtoken .venv/bin/python server.py   # http://localhost:8036/mcp

## Deployment

Push to `main` → GitHub Actions builds
`zot.hwcopeland.net/florida-man-bioscience/demo-mcp:main` → Flux image
automation rolls out the `demo-mcp` Deployment in `theswamp`
(manifests: iac repo, `rke2/tooling/flux/theswamp/*-mcp.yaml`).
The call log persists on the `demo-mcp-data` PVC at `/data/call-log.csv`.
