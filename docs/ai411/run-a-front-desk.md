# Run a hosted front desk

Run a dedicated voice-agent process as the automated receptionist for one published CMS tenant.

Front desk is a process pin (`AGENT_MODE=front_desk`). It is not Gainesville AI 411, not caller-ID routing, and not a production DID assignment. On `AGENT_MODE=auto`, `paid` and `active_owner` still resolve to `owner_updates`, and unknown numbers stay public AI 411.

## Before you start

You need:

- A voice-agent checkout you can start. See [Voice local development](../../voice-agent/README.md).
- Hosted-business CMS enabled, with a **published** public release for the tenant. See [Hosted-business CMS](../hosted-business-cms.md).
- The tenant slug. One process serves one slug.

Do not use this page to run the public Gainesville 411 line. That mode is `AGENT_MODE=ai411` (or `auto` for unknown callers).

## Pin the process

1. In the environment for this voice process, set the fail-closed pin:

   ```bash
   export FRONT_DESK_ENABLED=true
   export FRONT_DESK_TENANT_SLUG=TENANT-SLUG
   export AGENT_MODE=front_desk
   ```

   Replace `TENANT-SLUG` with the CMS tenant slug. The process lowercases the slug. `FRONT_DESK_ENABLED` must be `1`, `true`, `yes`, or `on` (case-insensitive). `VOICE_AGENT_MODE` is an alias for `AGENT_MODE`.

   If `AGENT_MODE` is `front_desk` without both `FRONT_DESK_ENABLED` and `FRONT_DESK_TENANT_SLUG`, importing `voice-agent/config.py` exits with:

   ```text
   AGENT_MODE=front_desk requires FRONT_DESK_ENABLED and FRONT_DESK_TENANT_SLUG.
   ```

2. Point the same process at the CMS store that holds the published release:

   ```bash
   export BUSINESS_CMS_ENABLED=true
   export BUSINESS_CMS_DATA_DIR=/path/to/cms-data
   ```

   Replace `/path/to/cms-data` with the directory that contains `tenants/<slug>/`. Front-desk tools import `mcp-server/business_front_desk.py` in-process. They fail with `feature_disabled` when CMS or front desk is off, and `unpublished` when the slug has no public release.

3. Start the voice agent as you do for other dedicated modes. From `voice-agent/`, the documented local server is `uvicorn server:app --port 8035`. See [Voice local development](../../voice-agent/README.md) for keys, `PUBLIC_BASE_URL`, and Twilio webhooks.

4. Optional: Point a Twilio number you control at this process if you want inbound calls. This runtime does not assign a production DID.

Every call on this process is front desk for `FRONT_DESK_TENANT_SLUG`. The trusted runtime injects the slug, published `release_id`, and an opaque callback `contact_ref`. The model cannot pass tenant, contact, or phone values.

## Use the receptionist tools

The model may use only these tools:

| Tool | What it does |
|------|----------------|
| `front_desk_get_business` | Reads published public facts. `section` is `identity`, `hours`, `services`, `faq`, `page`, `forms`, or `all`. |
| `front_desk_leave_message` | Files a message for the owner. |
| `front_desk_request_appointment` | Files an appointment **request** for owner review. This is not a booking. |
| `front_desk_request_owner_callback` | Files a callback **request**. This is not a live transfer. |

Answers come only from published tool results. Opening hours are not appointment availability. Messages and appointment requests stay pending owner review.

Ask the caller for consent before attaching a callback. If they refuse, still take the message. Owner callback requires consent and a runtime `contact_ref`; it does not connect the owner live.

The receptionist cannot edit the website, publish content, or view the owner inbox.

## Handle emergencies

If the caller has an emergency, tell them to hang up and call 911. Inbox messages are not dispatch.

## Keep phones off the model

The runtime hashes the caller number into `contact_ref` and injects it. Tool responses that reach the model are sanitized: phones, emails, short numeric codes, and capability-looking tokens are redacted. Fields named `contact_ref`, `phone`, `otp`, `token`, `cookie`, and `authorization` are dropped.

Do not put owner or caller phone numbers in prompts.

## What this does not change

- Public Gainesville 411 remains a different mode (`ai411`).
- `customers.resolve_mode` is unchanged: `paid` and `active_owner` still resolve to `owner_updates`. `front_desk_tenant_eligible` can check a paid or `active_owner` claim on a slug; it does not route calls.
- Unknown callers on `AGENT_MODE=auto` do not become receptionists.
- These tools file inbox requests only. They do not live-transfer a call or send owner SMS.

## Verify

1. Confirm this process has `AGENT_MODE=front_desk`, `FRONT_DESK_ENABLED`, and `FRONT_DESK_TENANT_SLUG` set, and that CMS is enabled against the published tenant directory.
2. Confirm `front_desk_get_business` returns published identity for that slug.
3. Confirm `front_desk_request_appointment` returns `booking` false and `status` `pending_owner_review`.

## Related pages

- [Hosted-business front desk](../front-desk.md)
- [Hosted-business CMS](../hosted-business-cms.md)
- [Product loop mode routing](../PRODUCT_LOOP.md)
- [Voice local development](../../voice-agent/README.md)
