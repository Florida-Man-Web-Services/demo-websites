# Use the hosted CMS

Enable the local hosted-business CMS, save a tenant draft, publish a public page, and manage the owner inbox.

This how-to is for an operator or paid owner on a local checkout. CMS flags default **off**. These steps do not enable production ingress, a cluster flag, or a live DID.

For the short contract, see [Hosted-business CMS (wave 1)](../hosted-business-cms.md). The receptionist runtime is a separate job: [Hosted-business front desk (wave 1)](../front-desk.md).

## Before you begin

You need:

- A local clone of this repository
- A tenant slug (`lowercase-kebab`, for example `cool-cafe`)
- A writable directory for CMS files
- A customer registry file you can point at with `CUSTOMERS_PATH`

The owner loop in this guide uses HTTP on the voice FastAPI app (`uvicorn server:app --port 8035` from `voice-agent/`). You do not need Twilio, front-desk mode, or `site-tracker/` for these steps.

Do not use this CMS to overwrite outreach HTML. `generated-sites/<slug>.html` and content-hash demo URLs stay frozen. Do not treat `site-tracker/` as the owner CMS. Do not treat `GET /clients/{slug}` as a hosted-business page.

## Enable the CMS locally

`BUSINESS_CMS_ENABLED` is off unless its value is `1`, `true`, `yes`, or `on` (case-insensitive). When the flag is off, `/businesses/…`, `/cms/…`, and `/api/business-cms/…` return `404` with `{"ok": false, "code": "feature_disabled"}`.

1. Export the CMS variables in the shell that will start the voice app:

   ```bash
   export BUSINESS_CMS_ENABLED=true
   export BUSINESS_CMS_DATA_DIR=/tmp/fmws-cms
   export BUSINESS_CMS_SESSION_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
   export BUSINESS_CMS_PUBLIC_ORIGIN=http://127.0.0.1:8035
   export CUSTOMERS_PATH=/tmp/fmws-customers.json
   ```

   | Variable | Role |
   |----------|------|
   | `BUSINESS_CMS_ENABLED` | Turns on CMS storage and HTTP routes. Default off. |
   | `BUSINESS_CMS_DATA_DIR` | Required when enabled. Tenant files live under `$BUSINESS_CMS_DATA_DIR/tenants/<slug>/`. |
   | `BUSINESS_CMS_SESSION_KEY` | HMAC key for the owner session cookie. If unset, the CMS falls back to `ACCOUNT_LIFECYCLE_SERVICE_KEY`. `POST …/session` fails with `key_unset` when both are empty. |
   | `BUSINESS_CMS_PUBLIC_ORIGIN` | Canonical origin written into rendered HTML. The renderer does not use the request `Host` header. If unset, the origin is `http://127.0.0.1`. |
   | `CUSTOMERS_PATH` | Customer registry JSON. Opening a session and every owner mutation re-check that the slug has a `paid` or `active_owner` row. |

2. Confirm the data directory is a local filesystem path you control. The store is single-host lock-and-rename storage. It is not clustered.

Tenant layout after the first save:

```text
$BUSINESS_CMS_DATA_DIR/tenants/<slug>/state.json
$BUSINESS_CMS_DATA_DIR/tenants/<slug>/drafts/<revision>.json
$BUSINESS_CMS_DATA_DIR/tenants/<slug>/releases/<release-id>/{cms.json,public.json,index.html}
$BUSINESS_CMS_DATA_DIR/tenants/<slug>/inbox/<request-id>.json
```

`FRONT_DESK_ENABLED`, `FRONT_DESK_TENANT_SLUG`, and `AGENT_MODE=front_desk` are not required for the owner CMS HTTP loop.

## Record a paid owner for the slug

The session cookie is not enough by itself. `POST /api/business-cms/{slug}/session` and later owner calls re-read the registry. Caller ID is not proof of ownership.

Stay in the same shell as the exports.

1. Create an empty registry if the path does not exist:

   ```bash
   printf '{}\n' > "$CUSTOMERS_PATH"
   ```

2. Export `OWNER_E164` to the owner's E.164 number. From the repository root, upsert a `paid` or `active_owner` row whose `slug` matches the tenant:

   ```bash
   PYTHONPATH=mcp-server python3 - <<'PY'
   import os
   import customers
   customers.upsert(
       os.environ["OWNER_E164"],
       status="active_owner",
       slug="cool-cafe",
       business_name="Cool Cafe",
   )
   PY
   ```

   Replace the slug and name. Eligible write statuses are `paid` and `active_owner` only.

## Start the voice HTTP service

1. In the same shell (so the CMS variables stay set):

   ```bash
   cd voice-agent
   uvicorn server:app --port 8035
   ```

2. Leave that process running. The examples that follow use `http://127.0.0.1:8035`.

The curl examples match the stdlib dispatcher and the FastAPI mount. They are not a production runbook.

## Open an owner session

Owner mutations need two cookies:

- `fmws_cms_session` — HMAC session bound to the tenant slug, account id, expiry, and CMS actions
- `fmws_cms_csrf` — CSRF secret

When the voice app sets those cookies, it marks them **HttpOnly**, **SameSite=lax**, `path=/`. Default session TTL is 3600 seconds (clamped between 60 seconds and 12 hours).

`POST /api/business-cms/{slug}/session` does not require a CSRF header. Every other non-GET owner call does.

1. Open a session and store cookies:

   ```bash
   curl -sS -c /tmp/cms-cookies -b /tmp/cms-cookies \
     -H 'Content-Type: application/json' \
     -d '{"account_id":"acct-1","actor":"owner-1"}' \
     http://127.0.0.1:8035/api/business-cms/cool-cafe/session
   ```

   A successful body is `{"ok": true}`.

2. Copy the CSRF cookie into a header value:

   ```bash
   CSRF=$(python3 -c '
   from http.cookiejar import MozillaCookieJar
   jar = MozillaCookieJar("/tmp/cms-cookies")
   jar.load(ignore_discard=True, ignore_expires=True)
   print(next(c.value for c in jar if c.name == "fmws_cms_csrf"))
   ')
   ```

3. Optional: open the stub owner page (`GET /cms/businesses/cool-cafe/`) in the same cookie jar. Without a session, that page returns `401` HTML (`Sign in required.`).

Send CSRF as the `X-CSRF-Token` header, or as form field `csrf_token`. A missing or mismatched token returns `403` `{"ok": false, "code": "csrf"}`. A session for another slug returns `401` `wrong_tenant`.

To end the session:

```bash
curl -sS -c /tmp/cms-cookies -b /tmp/cms-cookies \
  -X DELETE \
  http://127.0.0.1:8035/api/business-cms/cool-cafe/session
```

## Save a draft

Draft saves are compare-and-swap. `expected_draft_rev` must match `state.json`. Use `null` for the first save. A mismatch returns `409` `stale_draft`.

Wave-1 fields you can publish later: identity, hours, homepage blocks (`hero`, `text`, `hours`, `services`, `faq`, `contact`), services, FAQ, forms, and SEO. Staff, specials, events, media, and extra pages may sit on the draft. Publishing them as **visible** is rejected.

Do not invent missing name, address, phone (NAP), hours, or prices. Leave them empty. Text fields must not contain HTML markup.

1. Save the first draft:

   ```bash
   curl -sS -c /tmp/cms-cookies -b /tmp/cms-cookies \
     -H 'Content-Type: application/json' \
     -H "X-CSRF-Token: $CSRF" \
     -X PUT \
     --data @- \
     http://127.0.0.1:8035/api/business-cms/cool-cafe/draft <<'JSON'
   {
     "expected_draft_rev": null,
     "document": {
       "schema_version": 1,
       "slug": "cool-cafe",
       "identity": {"name": "Cool Cafe"},
       "hours": {"unknown": true, "weekly": {}},
       "pages": [
         {
           "id": "home",
           "slug": "home",
           "title": "Cool Cafe",
           "visible": true,
           "blocks": []
         }
       ],
       "services": {"groups": []},
       "faq": [],
       "forms": {
         "contact": {"enabled": true},
         "message": {"enabled": true},
         "appointment": {"enabled": true}
       },
       "seo": {"title": "Cool Cafe", "canonical_path": "/", "indexable": true}
     }
   }
   JSON
   ```

2. Copy `draft_revision` from the JSON response. Later saves must send that value as `expected_draft_rev`.

3. Optional: `GET /api/business-cms/cool-cafe/draft` (session cookie; CSRF not required on GET) to reload the document and `state`.

A draft is not public. `GET /businesses/cool-cafe/` stays `404` with an empty body until you publish.

## Preview the draft

Preview renders the current draft. It does not create a release.

```bash
curl -sS -c /tmp/cms-cookies -b /tmp/cms-cookies \
  -H "X-CSRF-Token: $CSRF" \
  -X POST \
  http://127.0.0.1:8035/api/business-cms/cool-cafe/preview
```

The response is `text/html` with `Cache-Control: no-store`.

## Publish a release

Publish validates the draft with wave-1 **public** rules, writes an immutable release folder, and points `state.json` at that release.

1. POST publish. If you omit `expected_draft_rev` or `expected_release_id`, the handler fills them from current state (`published_release` is `null` before the first release):

   ```bash
   curl -sS -c /tmp/cms-cookies -b /tmp/cms-cookies \
     -H 'Content-Type: application/json' \
     -H "X-CSRF-Token: $CSRF" \
     -d '{}' \
     http://127.0.0.1:8035/api/business-cms/cool-cafe/publish
   ```

2. Confirm the body includes `"ok": true` and a `release_id` such as `rel-…`.

A concurrent publish with a stale release id returns `409` `stale_release`. Visible staff, specials, events, media, extra pages, or unsupported homepage block types return `400`.

Publish does not write `generated-sites/` or change content-hash demo URLs.

## Open the public page

```bash
curl -sS -D - http://127.0.0.1:8035/businesses/cool-cafe/
```

A live release returns `200` HTML and an `ETag` of the `release_id`. Canonical links in that HTML use `BUSINESS_CMS_PUBLIC_ORIGIN`, not the request host.

That URL is the stable hosted-CMS page. It is not the floridamanweb.online hash path for outreach HTML.

## Manage the inbox

The inbox is a separate store under `tenants/<slug>/inbox/`. Public visitors can only append rows. They cannot edit the CMS document.

Kinds: `contact`, `message`, `appointment`, `callback`. Owner statuses: `new`, `acknowledged`, `closed`.

1. Optional: post a public request (no session):

   ```bash
   curl -sS -H 'Content-Type: application/json' \
     -d '{"kind":"contact","message":"Please call about catering."}' \
     http://127.0.0.1:8035/businesses/cool-cafe/requests
   ```

   The visitor receipt is `{ok, request_id, kind, status: "pending_owner_review"}`. It does not include contact details. That POST does not change the public HTML.

2. List requests as the owner:

   ```bash
   curl -sS -c /tmp/cms-cookies -b /tmp/cms-cookies \
     -H "X-CSRF-Token: $CSRF" \
     http://127.0.0.1:8035/api/business-cms/cool-cafe/inbox
   ```

3. Acknowledge or close one row (`PATCH` requires CSRF):

   ```bash
   curl -sS -c /tmp/cms-cookies -b /tmp/cms-cookies \
     -H 'Content-Type: application/json' \
     -H "X-CSRF-Token: $CSRF" \
     -X PATCH \
     -d '{"status":"acknowledged"}' \
     http://127.0.0.1:8035/api/business-cms/cool-cafe/inbox/req-REPLACE_ME
   ```

4. Optional: `DELETE` the same `/inbox/{request_id}` path to remove the file.

## Restore a previous release to the draft

Restore copies `releases/<release-id>/cms.json` into a **new** draft. It does not change the live public page. Publish again if that restored draft should go public.

1. List releases:

   ```bash
   curl -sS -c /tmp/cms-cookies -b /tmp/cms-cookies \
     http://127.0.0.1:8035/api/business-cms/cool-cafe/versions
   ```

2. Restore one `release_id`:

   ```bash
   curl -sS -c /tmp/cms-cookies -b /tmp/cms-cookies \
     -H 'Content-Type: application/json' \
     -H "X-CSRF-Token: $CSRF" \
     -d '{"release_id":"rel-REPLACE_ME"}' \
     http://127.0.0.1:8035/api/business-cms/cool-cafe/restore
   ```

3. Preview, then publish if the restored draft is what you want live.

## Turn the CMS off

1. Stop the voice process if it is still running.
2. Unset `BUSINESS_CMS_ENABLED` (or set it to anything other than `1`, `true`, `yes`, or `on`).
3. Start the app again if you still need other voice routes.

Public and owner CMS routes fail closed. Outreach hash URLs and ChangeRequests are unchanged. Tenant files under `BUSINESS_CMS_DATA_DIR` remain on disk until you delete them.

## Wave-1 publish rules

| Collection or field | Draft | Public publish |
|---------------------|-------|----------------|
| Identity, hours, services, FAQ, forms, SEO | Allowed | Allowed |
| Homepage blocks `hero`, `text`, `hours`, `services`, `faq`, `contact` | Allowed | Allowed |
| Staff, specials, events, media | Allowed on the draft | Rejected if any item is `visible` |
| Extra pages besides `home` | Allowed on the draft | Rejected if `visible` |

Private keys (`provenance`, `owner_notes`, `private_contact`, `draft_only`) are stripped from the public JSON projection.

## Routes for this job

| Method and path | Who | CSRF | Effect |
|-----------------|-----|------|--------|
| `POST /api/business-cms/{slug}/session` | Owner | No | Sets `fmws_cms_session` and `fmws_cms_csrf` |
| `DELETE /api/business-cms/{slug}/session` | Owner | No | Clears both cookies |
| `GET /cms/businesses/{slug}/` | Owner | No | Stub editor page; `401` without a session |
| `GET` / `PUT /api/business-cms/{slug}/draft` | Owner | PUT yes | Load or CAS-save the draft |
| `POST /api/business-cms/{slug}/preview` | Owner | Yes | HTML preview; does not publish |
| `POST /api/business-cms/{slug}/publish` | Owner | Yes | Immutable release; public page updates |
| `GET /api/business-cms/{slug}/versions` | Owner | No | List release ids |
| `POST /api/business-cms/{slug}/restore` | Owner | Yes | Copy a release into a new draft |
| `GET /api/business-cms/{slug}/inbox` | Owner | No | List requests |
| `PATCH` / `DELETE /api/business-cms/{slug}/inbox/{request_id}` | Owner | Yes | Update status or delete |
| `GET /businesses/{slug}/` | Public | No | Published HTML, or empty `404` |
| `POST /businesses/{slug}/requests` | Public | No | Append an inbox row only |
