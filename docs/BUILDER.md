# Builder briefs & customers path

Coding agents that build demo sites after an onboarding interview need:

1. **Customer requirements** (JSON registry)
2. **Builder brief** (markdown written by `queue_website_build`)

## In-cluster (source of truth)

| Path on voice PVC | Env |
|-------------------|-----|
| `/data/customers.json` | `CUSTOMERS_PATH` |
| `/data/builder-briefs/*.md` | `BUILDER_BRIEFS_DIR` |

Only **voice-agent** mounts this PVC (RWO). Do not attach it to a second Deployment.

```bash
kubectl -n theswamp exec deploy/voice-agent -- ls -la /data/builder-briefs
kubectl -n theswamp exec deploy/voice-agent -- cat /data/customers.json
kubectl -n theswamp exec deploy/voice-agent -- cat /data/builder-briefs/<file>.md
```

## Host / Hermes agent

```bash
cd /home/noahtjones/demo-websites
./scripts/sync-customers-for-builder.sh
export CUSTOMERS_PATH=$PWD/data/customers.json
# Briefs: copy from cluster or regenerate via customers.write_builder_brief
```

API alternative (no kubectl):

```bash
curl -fsS https://voice.flmanbiosci.net/api/onboarding/customers | jq .
```

## Workflow

1. Find rows with `status` in `requirements_ready` | `building`.
2. Open `builder_brief_path` content (or re-run `write_builder_brief`).
3. Implement `generated-sites/<slug>.html` per **demo-websites** skill (NAP truth).
4. Commit/push; wait for demo-sites image / hash URL.
5. Call `customers.mark_demo_ready(phone, demo_url=..., slug=..., stripe_payment_link=...)`
   (Python on a machine with `CUSTOMERS_PATH` pointing at live JSON, or add an HTTP
   endpoint later).

## Desk

Authenticated `https://sites.floridamanweb.online/api/customers` lists the same
funnel via in-cluster `CUSTOMERS_API` to voice.
