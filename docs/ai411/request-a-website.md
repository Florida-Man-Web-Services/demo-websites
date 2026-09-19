# Request a demo website

Use the Gainesville AI 411 landing form to queue a free Florida Man Web Services demo callback, complete a short requirements interview, and receive a demo page. After payment is recorded, later calls on that number are for owner updates.

This is not the AI 411 directory (local businesses and events) and not the optional personal-page form on the same landing.

## Before you start

You need:

- A mobile number that can take a voice call. Standard rates may apply. You can hang up at any time.
- Optional: your business name, and an email address if you want the demo link by email.

Bring real name, address, phone, and hours if you want them on the demo. The product must not invent those facts.

## Fill the landing form

1. Open [Gainesville AI 411](https://ai411.floridamanweb.online/).
2. In **Free demo website callback**, enter **Mobile phone number** (`phone`). That field is required.
3. Optional: enter **Business name** (`business_name`) and **Email for the demo link** (`email`).
4. Click **Start my free business demo**.

The page sends JSON to `https://voice.flmanbiosci.net/api/onboarding/register` with `source` set to `ai411_web`. The landing form does not collect a contact name.

On success, the page shows the register API message (it may include the voice number that will call you). Your customer row is stored with status `callback_queued`. The phone is normalized to E.164. Voice places one Twilio outbound to that number with no sales `slug`. If the dial fails, you stay in the queue.

This page does not promise a callback time.

If registration fails, try the form again later.

## Complete the onboarding interview

Answer the call from Florida Man Web Services. The agent identifies as an AI helping design your free demo site.

If you miss it, call AI 411 back and say you are returning for your business demo.

The interview is open-ended, not a sales close. It collects a first-pass brief in this order:

1. Business name and what you do (`business_name`).
2. Who the site should help (`audience`).
3. What you want the site to accomplish (`goal`).
4. What the first version must include (`must_haves`).
5. How to follow up about the demo (`follow_up`).

You can volunteer extra detail (category, pages, branding, tone, content sources, timeline, email). Those details can improve the demo; they are not required to queue a first build.

The agent reads the plan back. After you confirm, it saves the brief (`requirements_ready`) and queues a website build.

## Wait for the demo

A coding agent builds a single HTML demo from your brief. The public URL looks like `https://floridamanweb.online/<hash>/`.

The demo must use facts you supplied. It must not invent:

- phone numbers
- street addresses
- a weekly hours grid
- staff, awards, or reviews

If you did not give hours, the page may omit a schedule or say to call for hours. It must not invent opening times. If you did not give a phone, the page must not invent one.

## Review the demo and payment link

When the demo is marked ready (`demo_ready`), you get a sales call. That call can send the demo URL and a Stripe payment link by SMS or email. SMS is the usual path for the payment URL.

Looking at the demo is free. Going live is discussed on that sales call. This page does not list Stripe prices.

## After you pay

Complete the Stripe payment link you were sent.

When payment is recorded (`POST /api/billing/mark-paid`), your status becomes `active_owner`. Later calls on that number are meant for owner updates: you request changes to the demo (hours, phone, address, or copy).

This page does not claim that Stripe webhooks always flip that status without an operator.

## Call routing

The intended product setting is `AGENT_MODE=auto`. In that mode, inbound and SMS routing follows your customer status:

| Status | Mode |
|--------|------|
| No customer row | `ai411` (directory) |
| `prospect`, `callback_queued`, `onboarding` | `onboarding` |
| `requirements_ready`, `building`, `demo_ready`, `sales_ready` | `sales` |
| `paid`, `active_owner` | `owner_updates` |

A process pin other than `auto` (or `unified`) forces one mode for every call. Operator docs also record a production pin of `AGENT_MODE=ai411` for voice images that only accept `sales` or `ai411`. This how-to does not claim which value is running.

`front_desk` is a separate pin (`AGENT_MODE=front_desk` plus a tenant slug). Paid owners in this funnel stay on `owner_updates`.

## Related paths

- **Directory:** Call AI 411 and ask for Gainesville businesses or events. That line is not the demo interview.
- **Personal page:** The landing also has **Prefer a personal page?** That opt-in is a free mini-page from shared interests, not a business website.
