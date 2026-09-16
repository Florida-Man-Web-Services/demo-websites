# Khashayar Mahmoudi website design

## Scope

Create a polished, single-file personal website for Khashayar Mahmoudi under the FMWS demo-sites system. The site is name-led and contact-first because the available brief contains only his name and phone number. The phone number is intentionally both public contact information and a private owner association.

- Slug: `khashayar-mahmoudi`
- Public phone display: `(352) 888-3741`
- Public phone link: `tel:+13528883741`
- Private owner association: customer registry keyed by `+13528883741`
- No address, hours, occupation, credentials, client list, testimonials, awards, or other claims unless supplied later

## Audience and primary job

The page should give a visitor a clear, credible way to identify and contact Khashayar without implying an unsupported profession or business category. The primary action is calling him. The page should also be easy to extend when he supplies biography, services, work samples, or location details.

## Visual direction

Use an editorial personal-portfolio treatment rather than a generic SaaS layout:

- Warm off-white canvas, ink typography, cobalt as the focused accent, with restrained muted gray-blue support tones.
- A distinctive name-led hero with generous typography and a simple visual motif based on a cobalt vertical rule and offset paper panels.
- Intentional left alignment for the main reading column; contact actions may use a compact split layout.
- Use one expressive display face for the name/headlines and one highly legible sans-serif for body copy. Prefer hosted Google Fonts only when available; preserve a strong system fallback.
- Avoid invented imagery, stock-photo heroes, fake metrics, fake reviews, and decorative claims.
- Keep motion to one subtle page-load reveal and respect `prefers-reduced-motion`.

## Information architecture

1. **Hero** — Khashayar Mahmoudi, neutral positioning copy, primary call action.
2. **Profile** — concise statement explaining that this is a personal contact page, without unsupported biography.
3. **Capabilities / focus** — editable, non-claiming prompts such as “What I’m working on,” presented as areas for future content rather than factual services.
4. **Selected work** — quiet empty state inviting future work samples; no fabricated projects.
5. **Contact** — visible phone number and clickable call action.
6. **Closing CTA** — repeat the direct call action and identify the page as a personal site.

The page will contain at least five content sections, selectable text for all key facts, a page title and description, a visible phone number, and accessible keyboard focus states.

## Technical design

- Output one self-contained HTML file at `generated-sites/khashayar-mahmoudi.html`.
- Inline CSS and JavaScript only; no framework, Tailwind, Bootstrap, or required local assets.
- Include responsive layouts for narrow mobile screens and wide desktop screens.
- Use semantic headings, `<main>`, `<section>`, `<address>` or labeled contact block, and a `tel:+13528883741` link.
- Include an honest contact-hours note such as “Call to connect” rather than inventing an hours schedule.
- Do not copy the phone into unrelated NAP fields or claim a business category.
- Register the number as a private `active_owner` page manager for the `khashayar-mahmoudi` slug, with `trusted_phones` set to the same E.164 number. The owner record is separate from public site copy but this brief explicitly permits public display too.

## Deployment and verification

- Compute the content hash from the final HTML using the repository’s `demo_site_hash` rule.
- Verify the file’s structure and content: title/meta, minimum section count, phone display and exact `tel:` link, reduced-motion support, no fabricated social proof, and no unsupported address/hours claims.
- Commit only the new spec and website file; leave unrelated dirty work untouched.
- If the live customer registry is writable, upsert the owner association and read it back. Verify owner authorization for the exact slug and phone.
- Publish through the existing demo-sites deployment path only after the local artifact and owner record pass verification. Report the resulting URL or an explicit deployment blocker.
