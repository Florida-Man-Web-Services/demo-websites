# Update your demo site

Call the owner-updates desk from your registered phone to change hours, phone, address, or copy on your legacy demo HTML page.

This guide is for accounts with status `paid` or `active_owner` whose site is a `generated-sites` HTML page. If the business publishes through the hosted CMS, use that CMS for content edits. That path is separate; this desk still applies hours, phone, address, and copy only to `generated-sites` HTML.

## Before you start

You must:

- Have account status `paid` or `active_owner`. After payment, inbound calls on that line use the owner-updates desk.
- Have a demo page at `generated-sites/<slug>.html`.
- Call from a phone registered on that account.

The desk looks up the business from the number you call from. Caller ID is a lookup hint, not proof of ownership. The desk warns you that it matches by caller ID only. Don't request changes for a site you don't own.

## File a change by phone

1. Call from your registered phone. The desk identifies as an AI owner-updates desk.
2. Confirm the business and page. If the number matches more than one business, name the business. If several pages are on the account, name the page. If the desk already matched a single business, it does not ask you to pick one.
3. Wait while the desk loads the site outline (page title and headings) from `generated-sites/<slug>.html`.
4. State the change. When you have both, give the current text and the replacement text.
5. Listen to the read-back. Confirm only if it is correct. The desk files a ChangeRequest after that spoken confirmation.
6. Optional: Ask the desk to apply the request now. Applying writes the local demo HTML. It does not open a live pull request unless shipping flags are on.
7. Optional: Ask the desk to text you the demo link.

A filed request starts as `pending` with an id such as `cr-` followed by a short token. If you asked to apply and apply succeeded, the request status is `shipped` for the local file.

If the outline or store result is missing, the desk says so. It does not invent hours, phone numbers, or copy.

## Change types the desk can apply

These item types update the demo HTML:

| Type | What apply changes |
|------|--------------------|
| `hours` | Text in a Hours or Open section |
| `phone` | Visible phone text and `tel:` links |
| `address` | Address section text or the `<address>` body |
| `copy` | Heading or body text, using current→replacement text or a named target |

You can also file other item types (for example menu, service, image, color, or adding or removing a section). Apply skips those types; a skip is not a hard failure. Apply stops and marks the request `failed` if a supported item cannot be matched on the page.

## Apply a request versus ship a pull request

Applying a ChangeRequest writes `generated-sites/<slug>.html` on disk and marks the request `shipped`. That step does not open a GitHub pull request.

Shipping the file is a separate step:

| Flag | Default | Effect |
|------|---------|--------|
| `SITE_PR_ENABLED` | off | Required to push and open a pull request. When off, the ship step returns a dry-run plan only. |
| `SITE_PR_AUTO` | off | When on, a successful apply may then try to open the pull request. |

Don't treat the public demo URL as updated unless the result includes a pull-request or merge receipt.

Applying can require extra verification on the same call. Complete that check with the desk; don't read codes to anyone else.

## Review or cancel a pending request

Ask the desk what is already pending for your page.

To drop an open request, confirm that you want it cancelled. You cannot cancel a request that is already `shipped`, `rejected`, or `failed`.
