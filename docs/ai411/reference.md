# AI 411 reference

Exact names for modes, flags, HTTP routes, and front-desk tools. For procedures, use the how-to pages in this directory.

## Voice modes

Set `AGENT_MODE` or alias `VOICE_AGENT_MODE` before importing `voice-agent/config.py`. Allowed values: `sales`, `ai411`, `owner_updates`, `unified`, `onboarding`, `auto`, `front_desk`.

| Mode | Who it serves |
|------|----------------|
| `ai411` | Public Gainesville directory and events |
| `onboarding` | Website requirements interview |
| `sales` | Demo delivery / payment link |
| `owner_updates` | Paid owner ChangeRequests |
| `front_desk` | One published CMS tenant (process pin) |
| `auto` | Per-phone routing from the customer registry |
| `unified` | Directory plus owner tools when caller ID matches |

`customers.resolve_mode` when `env_mode=auto`: unknown → `ai411`; onboarding funnel → `onboarding`; demo/sales statuses → `sales`; `paid` / `active_owner` → `owner_updates`. It does not return `front_desk`.

`AGENT_MODE=front_desk` without `FRONT_DESK_ENABLED` and `FRONT_DESK_TENANT_SLUG` exits at import.

## Feature flags (default off)

| Variable | Effect |
|----------|--------|
| `BUSINESS_CMS_ENABLED` | CMS store and `/businesses`, `/cms`, `/api/business-cms` routes |
| `BUSINESS_CMS_DATA_DIR` | Required when CMS is enabled |
| `BUSINESS_CMS_SESSION_KEY` | Owner session HMAC; falls back to `ACCOUNT_LIFECYCLE_SERVICE_KEY` |
| `BUSINESS_CMS_PUBLIC_ORIGIN` | Canonical origin for published HTML (not the request `Host`) |
| `FRONT_DESK_ENABLED` | Front-desk tools and knowledge |
| `FRONT_DESK_TENANT_SLUG` | Slug for the pinned front-desk process |
| `SITE_PR_ENABLED` | Allow opening a generated-sites PR after a shipped ChangeRequest |
| `SITE_PR_AUTO` | Apply may open that PR |
| `ACCOUNT_LIFECYCLE_ENABLED` | Client-page and trusted-phone lifecycle mutations |

Truthy flag values: `1`, `true`, `yes`, `on` (case-insensitive).

## Public and owner HTTP (voice app)

Mounted on the voice FastAPI app. CMS routes 404 with `feature_disabled` when the CMS flag is off.

| Method | Path | Role |
|--------|------|------|
| GET | `/businesses/{slug}/` | Published CMS HTML |
| POST | `/businesses/{slug}/requests` | Public inbox append |
| GET | `/cms/businesses/{slug}/` | Owner CMS page (session) |
| POST/DELETE | `/api/business-cms/{slug}/session` | Owner session |
| GET/PUT | `/api/business-cms/{slug}/draft` | Draft |
| POST | `/api/business-cms/{slug}/preview` | Preview (no publish) |
| POST | `/api/business-cms/{slug}/publish` | Publish |
| GET | `/api/business-cms/{slug}/versions` | Releases |
| POST | `/api/business-cms/{slug}/restore` | Restore to a new draft |
| GET/PATCH/DELETE | `/api/business-cms/{slug}/inbox[/{id}]` | Owner inbox |
| GET | `/clients/{slug}` | Lifecycle client page (not CMS) |
| POST | `/api/onboarding/register` | Landing callback signup |

Landing: `https://ai411.floridamanweb.online/ai411/`. Voice HTTP: `https://voice.flmanbiosci.net`. Hash demos: `https://floridamanweb.online/<12-char-hash>/`.

## Front-desk tools

Model arguments never include tenant slug, OTP, raw phone, or `contact_ref`. The bridge injects those from call state.

| Tool | Does |
|------|------|
| `front_desk_get_business` | Read published public JSON (`section`) |
| `front_desk_leave_message` | Inbox message |
| `front_desk_request_appointment` | Appointment **request** (not a booking) |
| `front_desk_request_owner_callback` | Callback **request** (not a live transfer) |

## Owner ChangeRequest types (legacy HTML)

`hours`, `phone`, `address`, `copy` on `generated-sites/<slug>.html`. See [Update your site](update-your-site.md).

## CMS wave-1 publish

May go public: identity, hours, homepage blocks, services, FAQ, forms, SEO. Visible staff, specials, events, media, or extra pages fail publish; they may remain on a draft.
