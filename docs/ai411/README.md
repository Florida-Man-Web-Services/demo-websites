# AI 411

AI 411 is Florida Man Web Services' Gainesville information line and the entry to the demo-website product loop.

Use this set to operate the surfaces that exist in this repository. Flags for the hosted CMS and front desk default **off**. This set does not assign a production phone number or turn on cluster flags.

## Who each page is for

| If you want to | Open |
|----------------|------|
| Look up a Gainesville business or event | [Call the public directory](call-the-directory.md) |
| Request a free demo website | [Request a website](request-a-website.md) |
| Change a paid demo HTML page by phone | [Update your site](update-your-site.md) |
| Publish a tenant CMS page locally | [Use the hosted CMS](use-the-hosted-cms.md) |
| Pin a receptionist to one published tenant | [Run a front desk](run-a-front-desk.md) |
| Look up modes, flags, routes, and tools | [Reference](reference.md) |

## Surfaces (do not mix)

| Surface | Job |
|---------|-----|
| Public AI 411 | Directory and events for unknown callers |
| Onboarding | Requirements interview after web signup |
| Owner updates | ChangeRequests on `generated-sites` HTML |
| Hosted CMS | Draft/publish at `GET /businesses/{slug}/` |
| Front desk | Receptionist for one published CMS slug |
| Personal / client pages | Separate opt-in or lifecycle pages |

Operator funnel: [Product loop](../PRODUCT_LOOP.md). Architecture: [Architecture](../ARCHITECTURE.md). HTTP APIs: [API](../API.md).
