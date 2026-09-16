# AI411 client account lifecycle design

## Decision

Implement a **default-off, owner-assisted self-service** lifecycle in the FMWS `demo-websites` repo. AI411 may explain and initiate the flow, but only an existing `paid` or `active_owner` account with fresh server-controlled step-up proof may mutate account resources. Unknown callers cannot claim accounts, create client pages, add trusted phones, remove trusted phones, or trigger destination OTP delivery.

This design is based on the approved Astra architecture pass saved at:
`/home/noahtjones/.hermes/astra/runs/20260916T035254Z/round-3-astra.out.md`.

## Boundaries

- Keep free `personal_pages.py`, outreach NAP/catalog data, generated outreach HTML, and existing ChangeRequest semantics unchanged.
- Use a separate SQLite-backed client-page and operation store with an authenticated `/clients/{slug}` route.
- Do not add an anonymous HTTP mutation endpoint or accept model-authored `caller_phone`, `account_id`, destination, or confirmation booleans as authority.
- Keep provider changes, real SMS, live registry migration, deployment enablement, and recovery/primary-phone replacement outside this repository implementation.
- Feature flag defaults off and the test SMS adapter is the only enabled adapter in repository tests.

## Authorization and operations

1. Resolve exactly one eligible account from server-owned call context. CID is a lookup hint, not proof. Exclude legacy `cid_legacy`/soft owner exceptions.
2. Require a fresh action-bound step-up challenge. The verification destination must be an already trusted phone resolved server-side.
3. For phone additions, collect the destination through a private/redacted input path, obtain explicit consent to send an OTP, prove destination control, and require a separate final keypad confirmation. Destination proof alone never grants owner access.
4. For page creation and removals, prepare an immutable operation with a payload digest, read back the exact safe summary, then accept server-controlled keypad confirmation. Ordinary model text such as “yes” is insufficient.
5. Commit under rechecked account/auth revisions and registry locks. Return `verified_success` only after exact private read-back; otherwise return pending/denied/failure.
6. Removing the primary, last, or active verification phone is blocked and routed to Noah recovery. Removing a page creates a permanent tombstone and the public route returns `410` with `Cache-Control: no-store`.

## Proposed repository surface

Existing files allowed to change:

- `voice-agent/ai411.py`
- `voice-agent/owner_updates.py`
- `voice-agent/mcp_bridge.py`
- `voice-agent/agent.py`
- `voice-agent/voice_auth.py`
- `voice-agent/server.py`
- `voice-agent/Dockerfile`
- `mcp-server/customers.py`
- `mcp-server/server.py`

New files:

- `mcp-server/account_lifecycle.py`
- `mcp-server/account_pages.py`
- `mcp-server/account_verification.py`
- `tests/test_fmws_account_lifecycle.py`
- `tests/test_fmws_account_lifecycle_wiring.py`
- `docs/fmws-account-lifecycle.md`

Any implementation-driven allowlist expansion requires review before editing. Do not modify `personal_pages.py`, `hosting/Dockerfile`, outreach data, or infrastructure/provider configuration in this slice.

## Data model

Customer registry remains authoritative for account status, primary phone, trusted phones, and ownership. Add stable `account_id` and monotonic `auth_revision` without changing the primary-phone JSON key. A normalized phone may belong to only one account across primary and trusted memberships; collision and existing ambiguity fail closed.

The lifecycle store holds:

- `client_pages`: page id, account id, random reserved slug, title, sanitized body, digest, revision, state, timestamps, deletion operation
- `operations`: operation id, account/action, canonical payload digest, expected revisions, session/proof references, idempotency key, expiry, state, result
- `challenges`: account/session/action binding, purpose, attempts, expiry, consumed state, protected verifier reference
- `audit_events`: operation/account/action, safe actor reference, revisions, digests, outcome, timestamp, correlation reference

Raw phone values and OTPs stay out of model results, ordinary logs, public HTML, and audit payloads. Use masked labels and opaque references.

## Acceptance gates

- Stranger CID, forged model fields, forced AI411 mode, `cid_legacy`, wrong/expired/replayed/cross-session OTP, and unconfigured provider all fail closed without mutation or SMS.
- Secondary-phone add preserves the primary key, requires owner step-up plus destination proof plus final confirmation, and rejects cross-account collisions under concurrency.
- Primary/last/verification-phone removal is rejected; revoked phones invalidate later sessions and proofs.
- Account A cannot enumerate, edit, remove, or confirm Account B resources.
- Changed payloads, expired drafts, duplicate idempotency keys with different payloads, and replayed confirmation cannot commit.
- Page creation accepts bounded structured text only; scripts, arbitrary HTML, account phones, and links are rejected.
- Page deletion produces an audited tombstone, exact-route `410`, permanent slug reservation, and no immediate recreation.
- Crash/restart, lock contention, disk/audit failure, and read-back timeout never produce false success.
- In-process AI411 dispatch and authenticated MCP wrappers enforce the same gates.

## Release boundary

Repository tests and a locally built voice image must pass with lifecycle writes default-off and fake SMS only. Production enablement requires a separate Noah approval package covering public route/domain, persistence and backups, phone migration/collision audit, OTP spending and delivery, recording redaction, recovery policy, and controlled live SMS tests. Do not deploy or enable lifecycle writes in this implementation pass.
