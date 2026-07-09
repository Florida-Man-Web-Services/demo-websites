# Outreach Correspondence — Gainesville demo-site campaign

Everything needed to reach the 252 Gainesville businesses whose demo sites are live at
`https://florida-man-bioscience.github.io/demo-websites/generated-sites/<slug>.html`.

## Files

| File | What it is |
| --- | --- |
| `outreach-data.csv` | **Master mail-merge sheet** — one row per business (252). Columns below. Feed this to any mail-merge tool. |
| `rendered-emails.md` | All 252 emails already personalized (business name + live demo URL filled in), grouped by variant. Copy-paste ready. |
| `email-templates.md` | The 6 source email variants (A–F) with merge fields, if you want to edit the wording. |
| `letter-template.md` | One-page physical mailer for #10 envelopes (print + QR the demo URL). |
| `phone-script.md` | 60-second call script + voicemail + objection handling. |
| `call-order.csv` | **Prioritized outreach order** — all 252 ranked by (rating × review volume) + category ticket-value + callability. `priority` column: top-50 → week-2 → later. `channel` column routes call vs mailer. |
| `qr/<slug>.png` | 246 QR codes, one per business, encoding its live demo URL. Drop into the printed mailer. |

## `outreach-data.csv` columns

`name, category, variant, subject, demo_url, demo_live, has_phone, phone, address, rating, shared_demo, google_maps_url`

- **variant** — which email template (A–F) fits this business's category.
- **subject** — subject line with the business name already merged.
- **demo_url** — the live demo site (all 252 verified `demo_live=yes`).
- **has_phone / phone** — 200 of 252 have a phone; use those for the phone script, the rest for the mailer.
- **shared_demo** — `yes` for the 12 rows (6 pairs) whose names slugify identically and therefore share one demo file.

## Variant → category map

- **A** Bars, Restaurants, Cafes, Food Trucks, Bakeries (30)
- **B** Hair, Barber, Nails, Spas (27)
- **C** Auto Repair, Tire, Car Wash / Detailing (24)
- **D** Contractors, Electricians, Plumbers, HVAC, Roofers, Painters, Handymen, Landscapers, Lawn, Pest, Movers, Cleaning (75)
- **E** Daycares, Dance, Martial Arts, Gyms (14)
- **F** Accountants, Notary, Insurance, Print, Jewelers, Upholstery, Dry Cleaners, Laundromats, Boutiques, Grocery, Convenience, Vets, Tattoo, other (82)

## Before you send

1. Replace `[YOUR EMAIL]` and `[YOUR PHONE]` throughout (they're placeholders — your real contact isn't in the repo).
2. In variants A and E, fill the small bracketed hints (`[type of place]`, `[neighborhood]`, etc.).
3. Business email addresses are **not** in the dataset (these businesses have no web presence). Use the phone script / mailer to make first contact; send the matching rendered email once you have their address.
4. The demo is free; the pitch is a flat one-time fee to take it live (own domain + Google listing). No monthly fees.

Regenerate `outreach-data.csv` / `rendered-emails.md` from `gainesville-no-website/gainesville_no_website.json` if the business list or templates change.
