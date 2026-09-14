# AI411 deployment parity audit

Audit executed: 2026-09-14 19:20 EDT
Scope: FMWS AI411 public landing, signup endpoint, callback route surface, customer-mode routing, and repository/deployment documentation.
Safety boundary: read-only/controlled. No valid production signup was submitted, no Twilio call was placed, no config was changed, and no service was restarted.

## Executive result

The deployed signup surface is reachable and points at the intended onboarding API. The deployed voice service is healthy, has the onboarding routes, reports `AGENT_MODE=auto`, and accepts the AI411 origin for CORS. Repository callback tests pass when run with isolated module state.

There are three parity/security findings:

1. **Documentation drift (medium):** `docs/PRODUCT_LOOP.md` and `docs/OPS_CLUSTER.md` still describe production as pinned to `AGENT_MODE=ai411` and an image that only accepts `sales|ai411`; the live `/health` response reports `agent_mode=auto`.
2. **Public registry exposure (high):** unauthenticated `GET https://voice.flmanbiosci.net/api/onboarding/customers` returned HTTP 200 and six customer rows. The route's source comment says it must be protected at the edge, but this audit found no edge protection. The response includes registry metadata beyond a simple count/status summary (for example IDs, phone-related fields, slugs, timestamps, and voice-auth metadata). No customer values are reproduced in this report.
3. **Landing deployment lag (medium):** after fetching `origin/main` at audit time, the repository's current `hosting/ai411/index.html` was 25,550 bytes with SHA-256 `0b9db3a73c6f0602f97ca2c6bbeaecd9b6d4942389fb98cf1f9a32aff0e4e34a`, while the deployed application body remained 25,131 bytes with SHA-256 `9878e22c047746f09e5c64f93ca8e93f25447f7f90e14acac8a7a4b25d7de0a2` (the pre-`612ad5e` page version). This may be normal CI/image propagation lag, but the live page was not current with `origin/main` during the audit.

A live valid signup-to-Twilio callback was deliberately not exercised because it would persist customer data and trigger an outbound call. Therefore, the complete production mutation/dial path remains unverified by this controlled audit.

## Evidence from the deployed services

All requests below were read-only except the empty JSON validation probe, which cannot pass request validation and therefore did not create a customer or dial a phone.

### Public landing

- `GET https://ai411.floridamanweb.online/` returned HTTP 302 to `/ai411/`.
- `GET https://ai411.floridamanweb.online/ai411/` returned HTTP 200, `text/html`.
- A curl fetch of the deployed `/ai411/` body was 25,131 bytes with SHA-256 `9878e22c047746f09e5c64f93ca8e93f25447f7f90e14acac8a7a4b25d7de0a2`.
- The current checked-out `hosting/ai411/index.html` (after rebasing onto `origin/main` commit `09e1b29`, which includes landing commit `612ad5e`) is 25,550 bytes with SHA-256 `0b9db3a73c6f0602f97ca2c6bbeaecd9b6d4942389fb98cf1f9a32aff0e4e34a`. It contains the newer sanitized form-error handling from `612ad5e`, which was absent from the live body. The deployed page therefore matches the pre-`612ad5e` source version, not the current repository version.
- A later request with the current Cloudflare path returned 25,498 bytes with SHA-256 `adb8b430472fb174aa162aa67c093242f5e1bd71ccd0fd8eda622250f2f89888`; it contains the same 25,131-byte application body plus a 367-byte Cloudflare Insights beacon. The beacon is not the source of the repository/live application difference.
- The deployed form contains `id="cb-form"`. Its script sets the default voice base to `https://voice.flmanbiosci.net` and constructs `POST /api/onboarding/register` from that base. The payload fields are `phone`, `business_name`, `email`, and `source: "ai411_web"`.

Repository mapping:

- `hosting/Dockerfile:23-24` copies the landing to `/ai411/index.html`.
- `hosting/ai411/index.html:666-705` defines the callback form submission and response handling in the current repository version. The deployed previous version has the same endpoint/payload logic at the corresponding script block but lacks the newer sanitized error handling.

### Voice API and callback route surface

- `GET https://voice.flmanbiosci.net/health` returned HTTP 200:
  - `ok: true`
  - `agent_mode: "auto"`
  - `customers_registry: true`
  - `personal_pages: true`
  - `voice_auth_vendor: "none"`
  - `active_calls: 0`
  - `active_sms_sessions: 0`
- `GET https://voice.flmanbiosci.net/openapi.json` returned HTTP 200. The deployed OpenAPI includes:
  - `POST /api/onboarding/register`
  - `POST /api/onboarding/place-callback`
  - `GET /api/onboarding/customers`
  - `POST /voice/outbound`
  - `POST /voice/status`
  - `POST /api/billing/mark-paid`
- The deployed register schema requires only `phone`; optional properties are `business_name`, `contact_name`, `email`, `notes`, and `source`.
- `OPTIONS` requests with origin `https://ai411.floridamanweb.online` returned HTTP 200 for `/api/onboarding/register`, `/voice/outbound`, and `/voice/status`, with `access-control-allow-origin` set to the AI411 origin.
- `GET` on each of those POST-only routes returned HTTP 405 with `Allow: POST`, confirming the route method gates.
- Controlled probe: `POST /api/onboarding/register` with `{}` returned HTTP 422 with a missing `phone` validation error and the expected AI411 CORS header. No valid payload was sent.

### Live registry observation

- `GET /api/onboarding/customers` returned HTTP 200 with `ok: true`, `count: 6`, and six rows.
- Redacted status counts: `callback_queued: 4`, `active_owner: 2`.
- Redacted source counts: `ai411_web: 2`, `ai411_web_cos_probe: 2`, `operator: 2`.
- This response was obtained without authentication. Treat it as a privacy/security blocker until edge or application authentication is verified and the public route is closed.

## Repository parity trace

### Signup and queue

- `voice-agent/server.py:687-725` implements `POST /api/onboarding/register`.
  - It calls `customers.register_callback(...)`.
  - A normal `ai411_web` registration is returned with `ok: true` and a customer record.
  - It then calls `place_onboarding_callback(body.phone)` unless `source == "resume_web"`.
  - Dial errors are logged and swallowed so the HTTP signup still returns success; this makes the callback result important to monitor separately.
- `mcp-server/customers.py:592-595` maps normal web signup to `status=callback_queued`; `resume_web` is deliberately mapped to `resume_waitlist`.
- `voice-agent/callback_dial.py:44-90` refuses unknown, blocked, and resume-waitlist rows; creates exactly one Twilio call; uses `/voice/outbound` without a `slug`; and sets `/voice/status` as the status callback.

### Mode transition

- `mcp-server/customers.py:669-710` maps `callback_queued` (and `prospect`/`onboarding`) to `onboarding` when `env_mode=auto`.
- `voice-agent/server.py:58-91` calls `resolve_call_mode(...)` while building `CallState`.
- `voice-agent/config.py:80-103` accepts `auto` and documents it as the production per-phone mode.
- The live health response confirms the deployed process is actually running with `agent_mode=auto`.

### Stale deployment documentation

The following repository text no longer matches the live health evidence:

- `docs/PRODUCT_LOOP.md:50-61` says the current production image only accepts `sales|ai411` and that live production is `AGENT_MODE=ai411`.
- `docs/OPS_CLUSTER.md:143-155` repeats `AGENT_MODE=ai411 until auto-capable image`.
- The current `voice-agent/Dockerfile:17-34,54-56` packages `customers.py` and the onboarding/MCP leaves, and the live OpenAPI exposes the corresponding routes. This supports the live `auto` state, but the deployed image digest/tag was not exposed by the public health endpoint and was not independently resolved in this audit.

## Automated verification

Environment setup:

```text
uv pip install -r voice-agent/requirements.txt pytest --python voice-agent/.venv/bin/python
```

Results from fresh runs:

- `voice-agent/tests/test_callback_dial.py -k 'not test_callback_twiml_url'`: **7 passed, 1 deselected**.
- `voice-agent/tests/test_customer_routing.py`: **10 passed**.
- `voice-agent/tests/test_agent_mode.py`: **7 passed**.
- `voice-agent/tests/test_onboarding.py`: **8 passed**.

An earlier combined invocation of the three original files produced 22 passes and 3 failures because the first callback test imports `config` before its fixture sets environment variables; later server imports then see the stale module and fail `config.require(...)`. Running each test file with clean/controlled module state passes. This is a local test-order/module-cache issue, not evidence of a deployed callback failure, and was not changed during this read-only audit.

## Acceptance checklist

- [x] Requested parity review written at `docs/superpowers/reviews/AI411_DEPLOYMENT_PARITY_2026-09-15.md`.
- [x] Public form and redirect inspected.
- [x] Frontend API target traced to repository and checked against deployed HTML.
- [x] Deployed onboarding, outbound, status, and billing route presence checked through OpenAPI.
- [x] CORS preflight and method gates checked.
- [x] Live health and mode state checked.
- [x] Callback/mode routing tests run with clean module state.
- [ ] Current repository landing bytes deployed: live page is on the previous hash/version at audit time.
- [ ] Valid production signup and outbound callback: intentionally not run because it mutates production data and places a phone call.
- [ ] Registry exposure remediated: remains a blocker for the deployment owner.
- [ ] Production docs updated to reflect the verified `auto` state: remains follow-up work.

## Recommended follow-up

1. Immediately protect or remove public access to `GET /api/onboarding/customers`; verify from an unauthenticated external request that it no longer returns customer rows. Prefer application-level authorization in addition to edge protection for defense in depth.
2. Update `docs/PRODUCT_LOOP.md` and `docs/OPS_CLUSTER.md` to record the verified live `AGENT_MODE=auto` state, the deployed image provenance/digest, and the process for keeping Flux/IAC env declarations in sync.
3. Confirm the demo-sites CI/image rollout for `origin/main` commit `db7257b`, then re-fetch `/ai411/` and compare the body/hash again.
4. Add test isolation (for example, reload/clear `config` before server imports or use subprocess-per-mode tests) so the complete callback test command passes without a test-order-dependent failure.
5. For a future controlled production smoke, use an operator-approved test number and an explicit cleanup/rollback plan before submitting a valid signup; do not use an unknown number.
