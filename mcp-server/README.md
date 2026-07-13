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
| `search_business_knowledge(query, limit?)` | Keyword/TF-IDF search over local `generated-sites` HTML chunks |
| `get_business_snapshot(slug)` | Compact text snapshot of one demo page (slug = filename stem) |
| `get_caller_profile(phone)` | Caller memory by phone; redacts when `consent.memory_ok` is false |
| `update_caller_profile(phone, patch)` | Create/merge name, prefs, consent, topics |
| `forget_caller(phone)` | Hard-delete caller profile ("forget me") |
| `add_caller_note(phone, note)` | Append freeform note (created if needed) |
| `create_change_request(business_slug, summary, items?, …)` | Persist a pending owner site ChangeRequest (JSONL) |
| `list_open_change_requests(slug?)` | List open ChangeRequests (optional slug filter) |
| `cancel_change_request(request_id)` | Mark a ChangeRequest cancelled |
| `get_site_outline(slug)` | Title + headings from `generated-sites/<slug>.html` |
| `search_events(query?, when?, tags?, free_only?, limit?)` | Local Gainesville events (seed JSON); when=tonight/tomorrow/this_weekend |
| `get_event(event_id)` | Full event record by id |
| `list_event_sources()` | Event source names with counts (seed, community, …) |
| `submit_event_broadcast(title, when_start, venue, phone, …)` | Community event post (JSONL; auto-approve + rate limit) |
| `submit_notice_broadcast(text, category, phone, expires_at?)` | Short notice/gossip (≤280 chars; categories) |
| `list_recent_broadcasts(category?, limit?)` | Approved non-expired broadcasts, newest first |
| `report_broadcast(id, reason, reporter_phone?)` | Flag a post for review (pulls from public list) |
| `delete_own_broadcast(id, phone)` | Soft-delete own post by author phone |

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
