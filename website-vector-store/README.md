# Gainesville Website Vector Store (`wvs`)

A tool that builds and **regularly refreshes** a semantic vector store of the
websites of Gainesville businesses — the retrieval corpus behind Florida Man Web
Services' sales agent.

**Why FMWS wants this.** It indexes the *existing* web presence of local
businesses (so the voice/sales agent can reason over the competitive landscape,
cite what a business's neighbors have, and tailor a pitch), and it tracks the
businesses with **no website at all** as sales prospects. Two outputs from one
crawl: a searchable index of who has what, and a fresh lead list.

## How it works

```
business list(s) ──▶ resolve website ──▶ crawl (polite, shallow) ──▶ chunk
      │                    │                                            │
      ▼                    ▼                                            ▼
 prospects (no site)   skip if fresh/unchanged                     embed ──▶ sqlite vector store ──▶ query
```

- **Incremental & idempotent.** A site indexed within `WVS_FRESHNESS_DAYS` (or
  unchanged by content hash) is skipped, so scheduled runs only do real work on
  new or stale sites. Re-indexing a business *replaces* its chunks — never
  duplicates.
- **Runs offline out of the box.** Default embedder is a dependency-free hashing
  embedder and the store is plain sqlite + numpy brute-force cosine — no API key,
  no vector-DB service. Fine for Gainesville scale (hundreds of sites).
- **Production embeddings** are one env var away (any OpenAI-compatible endpoint).

## Quickstart

```bash
pip install -r requirements.txt

python -m wvs.cli build --limit 20     # crawl + embed + index the first 20
python -m wvs.cli query "who does emergency AC repair in gainesville?"
python -m wvs.cli stats                # sites_indexed / prospects / errors / chunks
python -m wvs.cli prospects            # businesses with no website (FMWS leads)

python tests/test_smoke.py             # offline end-to-end test (no network/keys)
```

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `WVS_STORE` | `data/wvs.sqlite` | vector store path |
| `WVS_EMBED_PROVIDER` | `hashing` | `hashing` (offline) or `openai` |
| `WVS_EMBED_BASE_URL` | `https://api.openai.com/v1` | any OpenAI-compatible `/embeddings` endpoint |
| `WVS_EMBED_API_KEY` | — | required for the `openai` provider |
| `WVS_EMBED_MODEL` | `text-embedding-3-small` | production embedding model |
| `WVS_FRESHNESS_DAYS` | `7` | skip re-crawling sites indexed within N days |
| `WVS_MAX_PAGES` | `5` | pages crawled per site (homepage + internal links) |
| `WVS_ROBOTS` | `1` | respect robots.txt |

## Business data

Sources are JSON arrays of business dicts (see `wvs/config.py`). Fields accepted:
`name` (required), `address`, `category`/`category_label`/`search_category`,
`phone`, and `website`/`url`/`homepage` (optional). Two feeds:

- `../gainesville-no-website/gainesville_no_website.json` — the 252 businesses
  **without** a site → tracked as **prospects** (no crawl).
- `data/gainesville_businesses.json` — the full list **with** `website` fields →
  the **crawl targets** that get indexed. See
  `data/gainesville_businesses.example.json` for the schema. (Discovery of a
  website from name+address via a Google Places key is stubbed in
  `sources.resolve_website`.)

## Regularly updated

Pick the scheduler for your host — both run `wvs.cli update` (incremental):

- **Kubernetes/Flux** (matches this repo's hosting): `deploy/cronjob.yaml` — a
  daily `CronJob` (04:17), `concurrencyPolicy: Forbid`, persistent volume for the
  store, embeddings key from a secret.
- **Plain cron / systemd:** `deploy/crontab.example`.

Build the image from the included `Dockerfile` (`python:3.12-slim`).

## Agent integration

`mcp_tool.py` exposes `search_business_sites(query)` and `website_prospects()` as
FastMCP tools. Register them in `../mcp-server/server.py` so the voice agent can
pull competitor/landscape context mid-call and read off fresh leads.
