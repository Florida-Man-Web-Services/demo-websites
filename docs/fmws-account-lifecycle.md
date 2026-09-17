# FMWS account lifecycle (AI411)

Owner-authenticated create/remove for **client account pages** and **verified secondary phones**. Separate from outreach HTML in `generated-sites/` and from free personal pages in `personal_pages.py`.

## Two page systems

| Surface | Path | Mutates how |
|---------|------|-------------|
| Outreach / owner site edits | `generated-sites/<slug>.html` → floridamanweb hash or `/vanity/<slug>/` | ChangeRequest → apply → `open_site_update_pr` |
| Client account pages | `GET /clients/{slug}` on the voice service | Lifecycle store (SQLite); no HTTP mutation route |

Do not mix them. Account-page bodies are bounded escaped plain text. Outreach clones keep NAP/brand rules.

## Eligibility and proof

- Registry: `customers.py` is authoritative for status, primary phone, trusted phones, `account_id`, `auth_revision`.
- Eligible statuses: `paid` and `active_owner` only.
- Caller ID is a lookup hint, not proof. Lifecycle rejects `cid_legacy` / `soft` / `cid_only`.
- Model-facing tools never take OTP, raw destination phone, account id, caller phone, confirmation booleans, or auth context.
- MCP service wrappers reconstruct context only from a short-lived signed capability (`lifecycle_service.py`, audience `fmws-account-lifecycle`).
- Mutations need fresh action-bound step-up plus server-controlled keypad confirmation after exact read-back.
- Adding a secondary phone also requires destination-phone verification. Destination proof alone cannot authorize.
- Primary phone, last trusted phone, and current verification phone cannot be removed. Primary replacement / recovery is Noah-only.

## Flags (code defaults **off**)

| Flag | Meaning |
|------|---------|
| `ACCOUNT_LIFECYCLE_ENABLED` | Lifecycle mutations. False → fail closed. |
| `SITE_PR_ENABLED` | Allow push/open of a generated-sites PR after a shipped ChangeRequest. |
| `SITE_PR_AUTO` | `apply_change_request` may call `open_site_update_pr`. |
| `SITE_PR_AUTOMERGE` | After a just-created site PR, request merge. Independent of lifecycle. Failed merge is stored as `merge_failed` and is never reported as published. |

Repository tests use a fake OTP adapter and never send real SMS.

## Public client route

`GET /clients/{slug}` (voice-agent):

- Invalid slug → 404, empty body, `Cache-Control: no-store`
- Unknown / unpublished → 404, empty body, `no-store`
- Active → 200 HTML, `no-store`
- Tombstone → 410, empty body, `no-store` (slug reserved)

## Autonomous website publishing

Removes the **human merge/review gate only**. It does not remove owner authorization, explicit read-back, generated-sites-only path, audit/receipt persistence, or CI/deployment checks. Target is derived from `request_id`, never from a model-supplied path or PR URL.

Default GitHub repo is `Florida-Man-Web-Services/demo-websites` (`SITE_PR_GITHUB_REPO` / `GITHUB_REPOSITORY` override).

## Live note (operator, 2026-09-17)

`theswamp` voice-agent and demo-mcp were enabled with all four flags true, Flux reconcile disabled on those two Deployments, and a 64-byte `ACCOUNT_LIFECYCLE_SERVICE_KEY` mounted. That does **not** mean GitHub publishing works: demo-mcp had no `GH_TOKEN` / `GITHUB_TOKEN` at enablement. Mount a token secret before expecting a live site PR.

Do not re-enable Flux on voice-agent without preserving `GROK_VOICE`. IAC diffs go to `jonesnoaht/iac` for Noah review first.
