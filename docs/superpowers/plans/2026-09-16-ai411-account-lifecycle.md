# AI411 Client Account Lifecycle Implementation Plan

> **Status (2026-09-17):** Implementation is on `origin/main` (`3123ed8` lifecycle + publishing, `c1a77bf` voice image packaging, `b348407` MCP `mcp<2` pin). Code defaults remain off. Live `theswamp` was enabled by operator order the same day. This file is the original task list with checkboxes closed against that evidence.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a default-off, owner-authenticated AI411 lifecycle for creating/removing client pages and adding/removing verified secondary account phones, with durable operations, audit records, and fail-closed tests.

**Architecture:** Keep `mcp-server/customers.py` authoritative for account status, primary phone, trusted phones, and authorization revision. Add an isolated SQLite lifecycle store for account pages, operations, challenges, tombstones, and audit events; serve page content dynamically at `/clients/{slug}` rather than using immutable generated-sites hosting. AI411 and owner_updates expose the same model-facing preparation/status tools, while server-owned voice context and private confirmation events perform authentication and commit; no model argument can authorize a mutation.

**Tech Stack:** Python 3.12, SQLite, FastAPI, pytest, existing Twilio/httpx dependencies, injected fake SMS adapter, existing voice-agent MCP bridge, semantic HTML rendering with escaping, Docker image smoke tests.

## Global Constraints

- Scope is FMWS `/home/noahtjones/demo-websites` only.
- Feature flag defaults off; repository tests use a fake OTP adapter and never send real SMS.
- Eligible accounts are existing `paid` or `active_owner` rows only.
- Caller ID is a lookup hint, not proof; reject `cid_legacy`/soft authorization for lifecycle operations.
- Model arguments never supply authority for caller phone, account id, destination, confirmation, or session context.
- Every mutation requires fresh action-bound step-up proof and server-controlled keypad confirmation after deterministic read-back.
- Secondary phone addition requires both owner authentication through an existing trusted phone and destination-phone verification.
- Primary-phone, last-phone, and active verification-phone removal is rejected; recovery and primary replacement are Noah-only.
- Normalized phone numbers are globally unique across primary and trusted memberships; ambiguous/colliding data fails closed.
- Client pages use a separate account-page registry and `/clients/{slug}` route; do not modify `personal_pages.py`, outreach NAP/catalog data, `generated-sites`, or `hosting/Dockerfile`.
- Public pages accept bounded structured text only, with escaping and phone/link/script rejection.
- Page removal tombstones the page, permanently reserves the slug, returns HTTP 410, and uses `Cache-Control: no-store`.
- Raw phones, OTPs, full page bodies, and secret input must not appear in model results, normal logs, audit records, or public HTML.
- Provider configuration, real SMS, live registry migration, infrastructure changes, and lifecycle production enablement are outside this implementation pass.
- Website change requests use autonomous agent publishing when explicitly enabled: no human review/merge is required, but owner authorization, explicit read-back confirmation, generated-sites-only file scope, audit metadata, and CI/deployment checks remain mandatory. Autonomous publishing is a separate flag from `ACCOUNT_LIFECYCLE_ENABLED`.

---

### Task 1: Add the isolated lifecycle store and page renderer

**Files:**
- Create: `mcp-server/account_pages.py`
- Create: `mcp-server/account_lifecycle.py`
- Create: `mcp-server/account_verification.py`
- Test: `mcp-server/tests/test_account_lifecycle.py`
- Test: `mcp-server/tests/test_account_pages.py`

**Interfaces:**
- Consumes: server-owned account id, auth revision, verified lifecycle context, and bounded page payload.
- Produces: safe `Result` dictionaries with states `denied`, `verification_required`, `awaiting_confirmation`, `pending`, `verified_success`, `verified_noop`, or `failed`; never raw OTPs or unmasked phones.

- [x] **Step 1: Define SQLite schema and safe result helpers**

Implement `_connect(path)`, `_init_schema(path)`, and `_safe_result(state, **fields)` in `account_lifecycle.py`. Use tables `client_pages`, `operations`, `challenges`, and `audit_events`. Add unique constraints for page slug and `(account_id, idempotency_key)`, indexes for account and operation lookup, WAL mode, and explicit transactions. Store only digests and opaque references in audit rows.

- [x] **Step 2: Add deterministic canonicalization and bounded page validation**

Implement in `account_pages.py`:

```python
def canonical_page_payload(title: str, body: str) -> tuple[str, str]: ...
def validate_public_page(title: str, body: str) -> dict: ...
def render_client_page(page: dict) -> str: ...
def page_digest(title: str, body: str) -> str: ...
```

Require a nonempty title and bounded plain-text body. Reject HTML/script tags, URLs, email addresses, phone-like strings, and known account identifiers. Render with `html.escape`; never accept arbitrary markup or remote fetches. Generate an opaque random slug server-side. Test script injection, link/phone leakage, changed payload digests, and deterministic rendering.

- [x] **Step 3: Implement lifecycle operation preparation**

Implement:

```python
def prepare_client_page_create(*, ctx, title, body, idempotency_key) -> dict: ...
def prepare_client_page_remove(*, ctx, page_id, idempotency_key) -> dict: ...
def prepare_trusted_phone_add(*, ctx, idempotency_key) -> dict: ...
def prepare_trusted_phone_remove(*, ctx, phone_ref, idempotency_key) -> dict: ...
def get_lifecycle_status(*, ctx, operation_id=None) -> dict: ...
def cancel_account_operation(*, ctx, operation_id) -> dict: ...
```

Require a valid server-created context, eligible account, lifecycle feature flag, fresh action-bound proof, and account ownership. Persist immutable payload digest, expected account/page/auth revisions, expiry, state, and an audit preparation event. Same idempotency key plus same digest returns the original operation; different digest is rejected.

- [x] **Step 4: Implement page commit and tombstone semantics**

Implement `commit_account_operation(*, ctx, operation_id, confirmation_token)` for create/remove page actions. Recheck context, account status, auth revision, page ownership/version, expiry, and confirmation digest inside one SQLite transaction. Create pages as published only at commit. Remove pages by setting `state='tombstoned'`, `deleted_at`, and a permanent slug reservation; append audit event in the same transaction. Commit retries return the stored receipt after read-back and never recreate a tombstone.

- [x] **Step 5: Implement exact page reads and route helpers**

Implement:

```python
def get_public_page(slug: str) -> tuple[int, dict, str]: ...
def verify_client_page_publication(page_id: str, expected_state: str) -> dict: ...
```

Return 200 plus rendered content for active pages and 410 with no body content for tombstones. Both paths set `Cache-Control: no-store`. Unknown or unpublished pages return 404. Add tests for account isolation, 410 deletion, reserved slug, and cache headers.

### Task 2: Add fail-closed account authorization and phone operations

**Files:**
- Modify: `mcp-server/customers.py`
- Modify: `mcp-server/account_lifecycle.py`
- Modify: `mcp-server/account_verification.py`
- Test: `mcp-server/tests/test_account_lifecycle_phones.py`
- Test: `mcp-server/tests/test_account_lifecycle_authorization.py`
- Test: `mcp-server/tests/test_account_lifecycle_recovery.py`

**Interfaces:**
- Consumes: existing customer JSON registry and server-owned lifecycle context.
- Produces: account id/auth revision, masked phone refs, collision-safe phone mutations, and durable operation receipts.

- [x] **Step 1: Add stable account identity and auth revision defaults**

Add a migration-on-read/write helper in `customers.py` that assigns a stable opaque `account_id` and integer `auth_revision` to eligible rows without changing the primary phone map key. Preserve existing fields and do not auto-merge duplicate accounts. Add `resolve_lifecycle_account(caller_phone)` that returns exactly one eligible account or a generic denial for none/ambiguous.

- [x] **Step 2: Add lifecycle-specific authorization**

Implement:

```python
def authorize_account_lifecycle(*, account_id: str, action: str, ctx, expected_auth_revision: int | None = None) -> dict: ...
def lifecycle_phone_refs(account_id: str, *, ctx) -> dict: ...
```

Reject missing/forged context, non-eligible status, mismatched account, stale revision, forced mode bypass, and legacy `cid_legacy`. Return opaque `phone_ref` plus masked last-four labels only. Never enumerate another account.

- [x] **Step 3: Add primary-preserving phone add**

Implement:

```python
def apply_trusted_phone_operation(*, operation_id: str, account_id: str, action: str, phone_e164: str, expected_auth_revision: int) -> dict: ...
```

Under the registry-wide lock, re-read all rows, normalize input, reject invalid or cross-account collisions, preserve the primary JSON key, append only to `trusted_phones`, increment `auth_revision`, and write an operation receipt. Same-account membership is an idempotent no-op. Do not allow the model to pass raw destination data into the final commit.

- [x] **Step 4: Enforce phone-removal invariants**

Remove by opaque `phone_ref` only. Reject primary, last trusted, or current verification phone removal. On successful removal, increment auth revision and invalidate all capabilities/proofs tied to the old revision. Recheck the account under the same lock before write. Add concurrent-add and concurrent-remove tests.

- [x] **Step 5: Add crash/recovery reconciliation**

Persist operation intent and receipt ids before registry replacement; implement `reconcile_pending_operations()`. Inject failures before/after replacement and before/after SQLite audit finalization. On ambiguous recovery return `pending_reconciliation` and block more account mutations; never blindly replay a revocation or report success without exact read-back.

### Task 3: Add server-controlled verification and confirmation boundary

**Files:**
- Modify: `mcp-server/account_verification.py`
- Modify: `voice-agent/voice_auth.py`
- Modify: `voice-agent/agent.py`
- Create/modify: `voice-agent/lifecycle_voice.py`
- Test: `voice-agent/tests/test_account_lifecycle_voice.py`
- Test: `mcp-server/tests/test_account_verification.py`

**Interfaces:**
- Consumes: verified provider call session and injected SMS sender/fake OTP adapter.
- Produces: short-lived `AuthContext`, destination challenge references, and single-use confirmation tokens; secret input is never passed through LLM tool arguments.

- [x] **Step 1: Define server-only context and feature flag**

Implement `AuthContext` and `TrustedInputEvent` as server-created records containing session id, account id, caller transport binding, auth revision, action, expiry, and capability id. Add `ACCOUNT_LIFECYCLE_ENABLED` defaulting to false and reject all lifecycle mutations when false.

- [x] **Step 2: Add action-bound owner step-up**

Implement:

```python
def begin_step_up(*, action: str, ctx) -> dict: ...
def complete_step_up(*, challenge_id: str, secret_input_ref: str, ctx) -> dict: ...
def request_owner_verification(*, auth, purpose: str, operation_id: str | None = None) -> dict: ...
```

Send only to a trusted verification phone resolved from the account, with expiry, attempt limits, resend cooldown, account/session throttles, and fake adapter injection. Production with missing sender fails closed; debug code paths are test-only. Codes must be keyed-verifier protected, single-use, purpose-bound, and redacted from recordings/logs/traces.

- [x] **Step 3: Add private destination capture and verification**

Implement:

```python
def capture_account_phone(*, auth, secret_input_ref: str) -> dict: ...
def request_destination_verification(*, auth, operation_id: str, send_consent_event) -> dict: ...
def verify_destination_challenge(*, auth, operation_id: str, challenge_id: str, secret_input_ref: str) -> dict: ...
```

Destination input must arrive through a validated private event, normalize to E.164, and return only an opaque reference. Do not send before owner step-up plus explicit send consent. Destination proof alone cannot authorize an account mutation.

- [x] **Step 4: Add deterministic read-back and keypad confirmation**

Implement:

```python
def get_confirmation_readback(*, auth, operation_id: str) -> dict: ...
def capture_lifecycle_confirmation(*, auth, operation_id: str, readback_digest: str, event: TrustedInputEvent) -> str: ...
```

Require a server-controlled “press 1 to confirm / 2 to cancel” event after the exact read-back. Bind token to session, action, operation digest, page version/phone ref, account id, auth revision, and expiry. Reject model `confirmed=true`, ordinary speech “yes,” pre-readback confirmation, replay, wrong session, and changed payload.

### Task 4: Wire AI411, owner_updates, bridge, MCP wrappers, and route

**Files:**
- Modify: `voice-agent/ai411.py`
- Modify: `voice-agent/owner_updates.py`
- Modify: `voice-agent/mcp_bridge.py`
- Modify: `mcp-server/server.py`
- Modify: `voice-agent/server.py`
- Test: `voice-agent/tests/test_account_lifecycle_wiring.py`
- Test: `voice-agent/tests/test_mcp_http.py`
- Test: `mcp-server/tests/test_server.py`

**Interfaces:**
- Consumes: lifecycle service functions and server-owned `AuthContext`.
- Produces: identical guarded model-facing tools in AI411 and owner_updates plus public read-only client route.

- [x] **Step 1: Add model-facing schemas without raw auth inputs**

Add these tools to both modes with arguments limited to safe request data:

```text
request_account_step_up(action)
get_account_lifecycle_status(operation_id?)
prepare_client_page(title, body)
prepare_trusted_phone_add()
prepare_trusted_phone_removal(phone_ref)
prepare_client_page_removal(page_id)
cancel_account_operation(operation_id)
```

Do not expose OTP codes, raw destination phone, account id, caller phone, confirmation boolean, or auth context in schemas. Update prompts to distinguish client account pages from free personal pages, explain masked read-backs, and state that drafts/pending results are not success.

- [x] **Step 2: Wire bridge dispatch with injected context**

Add explicit names to the bridge allowlist and dispatch. Construct context from call state/validated transport only; ignore or reject forged model fields. Route both AI411 and owner_updates to the same lifecycle service. Return structured safe results and fail closed when disabled or unavailable.

- [x] **Step 3: Add authenticated MCP wrappers**

Add wrappers in `mcp-server/server.py` only for authenticated service transport with short-lived audience-bound capability checks. Anonymous/public calls must receive denial. Do not authorize from `caller_phone`, `account_id`, or `confirmed` request fields.

- [x] **Step 4: Add read-only public client route**

Add `GET /clients/{slug}` to `voice-agent/server.py`, backed by the lifecycle page store. No HTTP mutation route is allowed. Return active HTML with `no-store`, tombstoned pages as exact 410, and no page body for unpublished/unknown pages. Validate slug path safety.

- [x] **Step 5: Add wiring tests**

Assert both modes expose the same lifecycle names, forced AI411 does not bypass gates, MCP wrappers reject forged context, anonymous HTTP mutations do not exist, and public reads do not reveal account phones or operations.

### Task 5: Add autonomous website publishing and package the repository slice

**Files:**
- Modify: `mcp-server/sitepr.py`
- Modify: `mcp-server/server.py`
- Modify: `voice-agent/Dockerfile`
- Create: `docs/fmws-account-lifecycle.md`
- Modify only if required: `voice-agent/config.py`
- Test: `voice-agent/tests/test_account_lifecycle_publication.py`

**Interfaces:**
- Consumes: all lifecycle modules from Tasks 1–4.
- Produces: a buildable voice image with lifecycle support packaged but disabled by default and a documented Noah-only release gate.

- [x] **Step 1: Add explicit autonomous website publishing**

Add `SITE_PR_AUTOMERGE` with a false default and a provider-independent backend method that merges only the just-created site-update PR after the existing `shipped`/owner-confirmed ChangeRequest checks. Persist `published_at`, commit/PR identifiers, and deployment intent on the request; make retries idempotent and return a stored receipt. The autonomous path must never merge arbitrary PRs, touch files outside the generated site path, or treat a model-supplied approval field as authority. Add fake-backend tests for disabled mode, enabled mode, failure after push, and retry/read-back behavior. Do not enable the flag in deployment configuration during this pass.

- [x] **Step 2: Package all new modules and persistent paths**

Copy `account_lifecycle.py`, `account_pages.py`, and `account_verification.py` into the voice image. Add `/data/account-lifecycle.sqlite3` and non-secret default-off configuration. Extend the build-time import check. Do not add Twilio Verify or provider credentials.

- [x] **Step 3: Write operator documentation**

Document account/page state transitions, masked refs, no-primary-removal rule, default-off behavior, fake SMS testing, audit/recovery semantics, `/clients/{slug}` response rules, and Noah-only production gates. Explicitly state that `personal_pages.py` and generated outreach pages are separate systems.

- [x] **Step 4: Run the complete test matrix**

Run from the relevant directories:

```bash
cd mcp-server && python3 -m pytest tests/test_account_lifecycle.py tests/test_account_pages.py tests/test_account_lifecycle_phones.py tests/test_account_lifecycle_authorization.py tests/test_account_lifecycle_recovery.py tests/test_account_verification.py -q
cd ../voice-agent && python3 -m pytest tests/test_account_lifecycle_voice.py tests/test_account_lifecycle_wiring.py tests/test_account_lifecycle_publication.py tests/test_mcp_http.py tests/test_agent_mode.py tests/test_owner_auth_gate.py -q
```

The suite must prove unauthorized requests cause zero registry writes and zero SMS sends; authorized fake flows produce one durable receipt; retries are idempotent; crashes are recoverable; logs/results/public HTML contain no raw phones or OTPs.

- [x] **Step 5: Build and smoke-test the image locally without deployment**

Run from repo root:

```bash
docker build -f voice-agent/Dockerfile -t fmws-voice-account-lifecycle:test .
docker run --rm -e ACCOUNT_LIFECYCLE_ENABLED=false -e CUSTOMERS_PATH=/tmp/customers.json -e ACCOUNT_LIFECYCLE_DB=/tmp/account-lifecycle.sqlite3 fmws-voice-account-lifecycle:test python -c "import sys; sys.path[:0]=['/app/mcp-server','/app/voice-agent']; import account_lifecycle, account_pages, account_verification; print('lifecycle_imports=PASS')"
```

Do not deploy the image, update Flux, modify provider configuration, enable lifecycle writes, or enable autonomous website publishing in this pass.

### Task 6: Security review and release handoff

**Files:**
- Read: all implementation files and tests from Tasks 1–5
- Create: `docs/superpowers/reviews/2026-09-16-ai411-account-lifecycle-review.md`

- [x] **Step 1: Run a privacy/bypass scan**

Search source and test output for raw phone/OTP logging, model-visible secret fields, anonymous mutation routes, `cid_legacy` use, direct `confirmed` trust, and reuse of `personal_pages.py` or generated-sites. Any hit is a blocker until fixed or explicitly documented as test-fixture-only.

- [x] **Step 2: Record unresolved production gates**

Document that live enablement still needs Noah approval for account-id/auth-revision migration, collision audit, persistent-volume backup/locking, SMS delivery/spend limits, DTMF/recording redaction, recovery policy, public base URL/cache behavior, controlled real-phone tests, and rollback that preserves tombstones/revocations.

- [x] **Step 3: Final repository verification**

Run:

```bash
git diff --check
git status --short
git diff --name-only main...HEAD
```

Confirm only the allowlisted repository files changed, the lifecycle feature remains disabled by default, and no production state was mutated.

- [x] **Step 4: Commit the implementation and review evidence**

Use focused commits for lifecycle store, auth/phones, wiring, packaging/docs, and review. Push only after all local tests and image smoke tests pass. Do not enable production writes or send live SMS.
