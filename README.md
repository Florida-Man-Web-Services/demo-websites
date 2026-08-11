# demo-websites (Florida Man Web Services)

Monorepo for **Gainesville demo landing pages**, the **AI 411 / owner-updates voice agent**, and the **demo MCP server**. Outreach product: self-contained HTML demos for local businesses without a web presence, plus agent tooling to pitch, answer directory questions, and take owner site-change requests.

## What's live vs stub

| Surface | Path | Status |
|--------|------|--------|
| Demo catalog (browse all) | `index.html` | **Live** — 246 sites linked by category |
| Demo landing pages | `generated-sites/<slug>.html` | **Live** — single-file HTML (inline CSS/JS) |
| Public hashed hosting | `hosting/` → floridamanweb.online | **Live** — content-hash paths (see Deploy) |
| Root nginx Dockerfile | `Dockerfile` + `arcade-bar-south.html` | **Legacy sample** — one-off Arcade Bar image; prefer `hosting/Dockerfile` |
| MCP server | `mcp-server/` | **Live** — lookup, knowledge, ChangeRequests, events, broadcasts |
| Voice agent | `voice-agent/` | **Live** — `AGENT_MODE=auto` routes AI411 / onboarding / sales / owner |
| Product loop | [`docs/PRODUCT_LOOP.md`](docs/PRODUCT_LOOP.md) | AI411 web → interview → build → Stripe → owner |
| AI 411 landing | `hosting/ai411/` | Phone callback form → `/api/onboarding/register` |
| Site tracker | `site-tracker/` | Supporting UI/gen helper |
| Website vector store | `website-vector-store/` | Crawl/index of *existing* GNV sites + prospects |
| NAP source list | `gainesville-no-website/` | Source JSON for demos |
| Site-gen prompts | `docs/site-generation/` | STANDARD_PROMPT, fill_prompt, rubric |

Do **not** invent NAP (name/address/phone), hours grids, awards, staff, or fake testimonials on demo pages.

## Quick map

```text
index.html                 # Human-facing catalog of all demos
generated-sites/           # Shipped demo HTML (one file per business)
hosting/                   # Production static image (hashed URLs)
mcp-server/                # FastMCP tools + siteedit/sitepr
voice-agent/               # Twilio/realtime brain + mcp_bridge
website-vector-store/      # Competitive / prospect vector index
gainesville-no-website/    # Business list (no website) → demos
docs/site-generation/      # Generate / improve prompts
data/                      # Runtime seed data (e.g. events)
```

## Browse locally

```bash
# Catalog + relative links to generated-sites/
python3 -m http.server 8080 --directory .
# open http://localhost:8080/
```

Or open any page directly:

```bash
xdg-open generated-sites/bob-s-barber-shop.html
```

## Deploy — demo sites (production)

Production serves **content-hashed** paths so each page URL is stable until the HTML bytes change:

`https://floridamanweb.online/<sha256(file)[:12]>/`

Hash rule **must** match `demo_site_hash()` in `voice-agent/businesses.py` (same as `hosting/Dockerfile`).

```bash
# Build from REPO ROOT
docker build -f hosting/Dockerfile -t demo-sites:local .
docker run --rm -p 8080:80 demo-sites:local
# Root shows hosting/root-index.html; each site at /<12-hex-hash>/
```

**CI:** push to `main` touching `generated-sites/**` or `hosting/**` runs `.github/workflows/build-demo-sites.yml` →  
`zot.hwcopeland.net/florida-man-bioscience/demo-sites:main` → Flux rolls out in-cluster (iac `theswamp` manifests).

Manual: **Actions → Build demo-sites → Run workflow**.

> Note: the root `Dockerfile` / `docker-compose.yml` only ship `arcade-bar-south.html` as a historical single-site sample. Use `hosting/Dockerfile` for the full catalog.

## Deploy — MCP server

```bash
cd mcp-server
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v
MCP_AUTH_TOKEN=devtoken .venv/bin/python server.py   # http://localhost:8036/mcp
```

**CI:** `.github/workflows/build-demo-mcp.yml` → `zot.hwcopeland.net/florida-man-bioscience/demo-mcp:main`.  
Details, tool table, and auth: [`mcp-server/README.md`](mcp-server/README.md).

## Deploy — voice agent

```bash
cd voice-agent
cp .env.example .env   # fill keys
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python doctor.py       # checklist
# Nix: nix develop   # from repo root (default shell = voice-agent)
```

Modes via `AGENT_MODE` (see `voice-agent/README.md` and
[`docs/PRODUCT_LOOP.md`](docs/PRODUCT_LOOP.md)): `sales`, `ai411`,
`owner_updates`, `unified`, `onboarding`, **`auto`** (per-phone routing).
Production public line currently runs **`ai411`** until the image includes
`auto`. CI: `.github/workflows/build-voice-agent.yml`.

## Generate / improve a demo page

```bash
python3 docs/site-generation/fill_prompt.py --name "Bob's Barber Shop"
python3 docs/site-generation/fill_prompt.py --slug baby-j-s-bar --mode improve
```

Write output only to `generated-sites/<slug>.html`. Slug rule: lowercase, non-alnum → `-` (same as `voice-agent/businesses.py`).  
Checklist: `docs/site-generation/quality-rubric.md`.

After adding/renaming pages, keep **`index.html`** in sync (catalog links).

## Related docs

| Doc | Topic |
|-----|--------|
| [`docs/README.md`](docs/README.md) | **Doc index** |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System architecture, components, domains |
| [`docs/PRODUCT_LOOP.md`](docs/PRODUCT_LOOP.md) | AI411 → onboarding → build → Stripe → owner |
| [`docs/OPS_CLUSTER.md`](docs/OPS_CLUSTER.md) | DNS, Authentik, Flux, secrets, runbooks |
| [`docs/API.md`](docs/API.md) | Voice + desk HTTP APIs |
| [`mcp-server/README.md`](mcp-server/README.md) | MCP tools, local run, k8s deploy |
| [`voice-agent/README.md`](voice-agent/README.md) | Twilio, TTS, AGENT_MODE, doctor |
| [`docs/site-generation/README.md`](docs/site-generation/README.md) | Prompt fill + improve wave |
| [`website-vector-store/README.md`](website-vector-store/README.md) | Crawl/index + CronJob |
| [`BULLETS-second-demo-site.md`](BULLETS-second-demo-site.md) | Ops backlog: next demo-site work |

## License / product

Internal Florida Man Web Services demo & outreach monorepo. Demo pages are previews for local businesses — not live client production sites unless explicitly launched.
