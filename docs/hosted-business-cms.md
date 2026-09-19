# Hosted-business CMS (wave 1)

Local-file CMS for paid/active_owner tenants. Default **off**.

## Enable locally

```bash
export BUSINESS_CMS_ENABLED=true
export BUSINESS_CMS_DATA_DIR=/tmp/fmws-cms
export BUSINESS_CMS_SESSION_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
export BUSINESS_CMS_PUBLIC_ORIGIN=http://127.0.0.1:8035
export CUSTOMERS_PATH=/path/to/customers.json   # paid owner with matching slug
```

Data lives under `$BUSINESS_CMS_DATA_DIR/tenants/<slug>/` (drafts, immutable releases, inbox). This backend is single-host; it is not clustered storage.

## Owner loop

1. `POST /api/business-cms/{slug}/session` with a paid owner account → HttpOnly session + CSRF cookie.
2. `PUT /api/business-cms/{slug}/draft` (CSRF header) to save identity, hours, services, FAQ, homepage, SEO.
3. `POST /api/business-cms/{slug}/preview` — no-store, does not publish.
4. `POST /api/business-cms/{slug}/publish` with expected draft/release revisions.
5. Public page: `GET /businesses/{slug}/`
6. Inbox: `GET/PATCH/DELETE /api/business-cms/{slug}/inbox...`

Staff/specials/events/media and extra pages may exist on a draft; publishing them visible is rejected.

## Rollback

Unset `BUSINESS_CMS_ENABLED`. Public and owner mutation routes fail closed. Legacy `generated-sites/` hash URLs and ChangeRequests are unchanged.

No production DID, ingress, or notification claims. Flags stay off in deploy until a later ops wave.
