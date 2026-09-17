# Review: AI411 account lifecycle (2026-09-16 plan)

Scope: `demo-websites` implementation on `origin/main` through `b348407`, plus live `theswamp` read-back on 2026-09-17. Not a legal opinion.

## Privacy / bypass scan

Searched lifecycle + sitepr + customers + voice wiring for raw OTP/phone logging, model-visible secrets, anonymous HTTP mutations, `cid_legacy` as lifecycle proof, model `confirmed=true`, and reuse of `personal_pages.py` / `generated-sites` for account pages.

| Check | Result |
|-------|--------|
| Model-facing voice schemas (`lifecycle_tools.py`) | Request data only (`action`, title/body, opaque `phone_ref` / `page_id`, `operation_id`, `idempotency_key`). No OTP, E.164, account id, caller phone, confirmation boolean, or capability. |
| MCP wrappers (`mcp-server/server.py`) | Reconstruct `AuthContext` via `context_from_capability`; empty/forged capability → `denied()`. Residual: `service_capability` is a FastMCP tool argument (service transport, not voice). |
| `cid_legacy` | Forbidden for lifecycle auth (`customers._FORBIDDEN_LIFECYCLE_AUTH_LEVELS`, `account_lifecycle` ctx check). Still used for **non-lifecycle** owner ChangeRequests — expected. |
| HTTP mutations | `GET /clients/{slug}` is read-only. Live smoke: unknown slug → 404, `Cache-Control: no-store`, empty body. |
| Account pages vs outreach | Renderer/store in `account_pages.py` / SQLite. `generated-sites` untouched by page create/remove. |
| Logging | No `logging.*` hits that interpolate OTP/phone in lifecycle modules. CLI `voice-agent/call.py` still prints NAP phones for the sales helper — out of lifecycle path. |
| Tests | Fixtures use fake numbers; authorization tests assert `cid_legacy` denial. |

No scan blocker found that should revert the merge. Residuals listed below.

## What landed

- Store + renderer + prepare/commit/tombstone: `account_lifecycle.py`, `account_pages.py`
- Auth/phones/recovery: `customers.py` + phone/recovery tests
- Voice step-up / DTMF confirmation: `account_verification.py`, `lifecycle_voice.py`
- Wiring: `lifecycle_tools.py` in AI411 and owner_updates; fail-closed when flag off
- Signed service bridge: `lifecycle_service.py`
- Image packaging: voice Dockerfile copies the four mcp-server leaves; `c1a77bf` / `b348407` unblocked live start (`mcp<2`)
- Autonomous site PR merge: `SITE_PR_AUTOMERGE`, receipt `merged` / `merge_failed`

## Unresolved production gates

These remain even after the 2026-09-17 live enablement:

1. **GitHub token** — live demo-mcp had `SITE_PR_*` true but `GH_TOKEN`/`GITHUB_TOKEN` length 0. Automerge cannot open or merge a PR until a secret is mounted. Do not put a token in chat.
2. **Default repo org** — code previously defaulted to `Florida-Man-Bioscience/demo-websites`. Fixed in the handoff commit to `Florida-Man-Web-Services/demo-websites`. Set `SITE_PR_GITHUB_REPO` on the live Deployment if the image has not rolled.
3. **Flux source of truth** — `voice-agent` and `demo-mcp` reconcile=disabled. IAC must not be edited as `hwcopeland/iac` from this identity; branch `jonesnoaht/iac` and wait for Noah review. Re-enable Flux only with `GROK_VOICE` preserved.
4. **SQLite durability** — `/data/account-lifecycle.sqlite3` backup, lock, and crash recovery in the live PVC not proven here.
5. **Live SMS / spend** — tests use a fake adapter. Real OTP sender, throttles, and recording/DTMF redaction in production Twilio are not proven by this review.
6. **Registry migration** — live `customers.json` collision audit and `account_id` / `auth_revision` backfill not run as a controlled report in this pass.
7. **Rollback** — disable flags without losing tombstones/revocations; image pin vs Flux.
8. **Public `/clients` URL** — route is on the voice process, not demo-sites CDN. Cache/base-URL for a public hostname still needs an explicit operator choice.
9. **Controlled real-phone test** — not done. Do not treat flag-on as a successful owner create/remove drill.

## Verification this handoff ran

- Cluster context `theswamp`; voice-agent and demo-mcp 1/1 Ready; health `ok`.
- Flags true on both workloads; service key length 64 (value not recorded).
- `GROK_VOICE` set (length 12, value not recorded).
- `GET /clients/does-not-exist` → 404 `no-store`.
- Khashayar outreach hash still HTTP 200 on floridamanweb (separate system).
