# AI 411 evergreen “things to do” — design

**Status:** approved to implement (operator 2026-09-17), flags default off.  
**Scope:** `/home/noahtjones/demo-websites` only.

## Problem

Live `/data/events.json` is 387 Visit Gainesville **dated** events. “What’s going on tonight” works; “what is there to do in Gainesville” cannot honestly name springs, parks, or pickleball without inventing a `start` (those rows would expire). Community ingest exists but is empty. Hipp already arrives via the VG organizer feed.

## Decision

Keep **events** unchanged. Add a **separate** evergreen store. Do not fake dates.

| Store | Path | Answers |
|-------|------|---------|
| Events | `EVENTS_PATH` `/data/events.json` | tonight / weekend / Hipp / movies |
| Activities | `ACTIVITIES_PATH` `/data/activities.json` | untimed “things to do” |

## Event contract (do not change)

`mcp-server/events.py`: require `id`, `title`, ISO `start`. Expiry = `end` else `start`; missing → expired. VG ingest replaces only `source=visitgainesville`. Community ids `community-<broadcast_id>`. `kind=film_showtime` and Hipp query/venue stay.

## Evergreen model

One published page → one activity in v1 (no hub-page splitting).

Required: `id` (`vg-page-<wp_id>`), `title`, `kind=evergreen_activity`, `source=visitgainesville`, `source_kind=wp_pages`, `source_url`, `source_page_id`, `fetched_at`, `last_verified_at`, `status` (`draft` \| `published` \| `unpublished`).

Optional: `description`, `venue`, `address`, `website`, `tags`, `category`, `free` (bool or omitted). **Omit** unknown NAP/hours/price. `free=true` only if source copy explicitly says free. `free_only` must not match omitted `free`.

No `start`/`end`. Search must not call `_is_expired`.

Freshness: published **and** `last_verified_at` within 30 days. Failed fetch keeps last-known-good; does not bump verification. Confirmed gone → `unpublished`. Empty/malformed HTTP must not wipe the file.

## Sources

- **Keep:** VG tribe REST (live events).
- **Evaluate only:** VG WP pages `search=things-to-do`. Allowlist in `mcp-server/activities_allowlist.json`. `publish: true` required before ingest writes a published row. Initial file has `publish: false` for all candidates.
- **Community:** existing approved broadcasts only (events, not evergreen).
- **Hipp:** existing `search_events`; no scraper.
- **Out:** UF LiveWhale, city HTML, Eventbrite, Meetup, CivicMedia.

## Flags (default off)

- `AI411_EVERGREEN_INGEST_ENABLED`
- `AI411_EVERGREEN_SEARCH_ENABLED`

Search off → `search_activities` returns `{ok:true, count:0, activities:[], disabled:true}` with no store reads required for answers. Ingest off → no HTTP, no writes.

## Voice

New tool `search_activities(query, tags, free_only, limit, category, source)`. No time filters. Do not imply the place is open now. Timed requests stay on `search_events`. Untimed browse may call both once search is enabled. Event category totals never include activities.

## Non-goals

Scrapers, extra submitter queues, fake seeds, collapsing a venue’s events into an activity, enabling production ingest in this pass.
