# FMWS cluster operations — DNS, Authentik, secrets, Flux

Runbook for hwcopeland RKE2 **theswamp** namespace and floridamanweb DNS.
Complements [ARCHITECTURE.md](./ARCHITECTURE.md) and [PRODUCT_LOOP.md](./PRODUCT_LOOP.md).

**IAC checkout:** `/home/noahtjones/iac`  
**RBAC reality:** FMB can create/update Deployments, Services, Secrets, PVCs in
`theswamp`. FMB **cannot** create HTTPRoutes or DNSRecords — those ship via
**Flux** after merge to `hwcopeland/iac` main.

---

## 1. Topology (FMWS)

```text
Cloudflare (proxied A → 69.180.240.158)
    → Cilium Gateway hwcopeland-gateway
        ├─ floridamanweb-https (*.floridamanweb.online)
        │     ├─ demo-sites          (apex + hash paths + /ai411/)
        │     ├─ ai411-landing       (ai411.floridamanweb.online)
        │     └─ site-tracker        (sites.floridamanweb.online → Authentik)
        ├─ flmanbiosci-https
        │     ├─ voice-agent         (voice.flmanbiosci.net)
        │     ├─ demo-mcp
        │     └─ site-tracker-legacy-redirect (sites.flmanbiosci.net → new host)
        └─ Authentik embedded outpost (full-proxy providers)
```

Certificate: `cf-floridamanweb-wildcard-cert-secret` covers
`floridamanweb.online` and `*.floridamanweb.online`.

---

## 2. DNS (cloudflare-operator)

**File:** `iac/rke2/kube-system/floridamanweb-dnsrecord.yaml`

| DNSRecord name | FQDN | Type | Content |
|----------------|------|------|---------|
| floridamanweb-root | floridamanweb.online | A | 69.180.240.158 |
| floridamanweb-www | www.floridamanweb.online | A | 69.180.240.158 |
| floridamanweb-ai411 | ai411.floridamanweb.online | A | 69.180.240.158 |
| floridamanweb-sites | sites.floridamanweb.online | A | 69.180.240.158 |

All `proxied: true`, `ttl: 1`, `interval: 5m`.

Legacy desk DNS remains on flmanbiosci zone until cutover complete
(`sites.flmanbiosci.net` in `flmanbiosci-dnsrecord.yaml`).

**Verify after Flux:**

```bash
host -t A ai411.floridamanweb.online
host -t A sites.floridamanweb.online
curl -sSI https://ai411.floridamanweb.online/ | head -15
curl -sSI https://sites.floridamanweb.online/ | head -15
curl -sSI https://sites.flmanbiosci.net/ | head -15   # expect 302 → floridamanweb
```

---

## 3. HTTPRoutes

| File | Host | Backend | Auth |
|------|------|---------|------|
| `httproute-demosites.yaml` | floridamanweb.online | demo-sites:80 | none |
| `httproute-ai411.yaml` | ai411.floridamanweb.online | demo-sites (`/`→`/ai411/`) | none |
| `httproute-tracker.yaml` | sites.floridamanweb.online | authentik-server | Authentik annotation |
| `httproute-tracker-legacy-redirect.yaml` | sites.flmanbiosci.net | redirect | n/a |
| `httproute-voice.yaml` | voice.flmanbiosci.net | voice-agent:8035 | none (Twilio sig) |

Kustomization includes all of the above under `theswamp/kustomization.yaml`.

### AI411 route behavior

1. Exact `/` → 302 `/ai411/`
2. Prefix `/ai411` → demo-sites (static form)
3. Default backend demo-sites (assets)

Form JS posts to `https://voice.flmanbiosci.net/api/onboarding/register`
(override with `window.AI411_API` or `data-api` on the form).

---

## 4. Authentik (site-tracker)

### 4.1 Blueprint (source of truth for editors)

**File:** `iac/rke2/authentik/blueprints/providers-sitetracker.yaml`

| Field | Value |
|-------|--------|
| Provider name | `Provider for Site Tracker` |
| Mode | proxy (full-proxy) |
| `external_host` | `https://sites.floridamanweb.online` |
| `internal_host` | `http://site-tracker.theswamp.svc.cluster.local:8040` |
| App slug | `site-tracker` |
| Groups | Florida Man Bioscience, Infrastructure (`policy_engine_mode: any`) |

### 4.2 ConfigMap mirror (what the pod loads)

**File:** `iac/rke2/authentik/blueprints-configmap.yaml`  
Key: `providers-sitetracker.yaml`

**Must stay byte-equivalent in meaning** with the blueprint file.  
`update.sh` does **not** rebuild the ConfigMap automatically.

### 4.3 Outpost

Embedded outpost already lists `Provider for Site Tracker`
(`blueprints/outpost.yaml`). No change required for host rename if provider
name is unchanged.

### 4.4 After Flux applies Authentik

1. Confirm blueprint apply in Authentik admin (Events / System tasks).
2. Open `https://sites.floridamanweb.online` → login challenge.
3. Identity headers reach app: `X-authentik-username`, `X-authentik-email`.

---

## 5. Deployments

### 5.1 voice-agent

**File:** `deployment-voice.yaml`  
**Image:** `zot.hwcopeland.net/florida-man-bioscience/voice-agent:main@sha256:…`  
**PVC:** `voice-agent-data` (RWO 2Gi Longhorn) → `/data`  
**Replicas:** 1, strategy Recreate  

**envFrom:**

1. `voice-agent-keys` (required) — Bitwarden ExternalSecret  
2. `voice-agent-extra` (optional) — Honcho / extras  

**Important env:**

| Name | Production value |
|------|------------------|
| `PUBLIC_BASE_URL` | `https://voice.flmanbiosci.net` |
| `VOICE_BACKEND` | `grok-realtime` |
| `AGENT_MODE` | `ai411` until auto-capable image |
| `CUSTOMERS_PATH` | `/data/customers.json` |
| `BUILDER_BRIEFS_DIR` | `/data/builder-briefs` |
| `MEMORY_DIR` | `/data/customer-memory` |
| `CALL_DB` | `/data/call-log.db` |
| `CORS_ALLOW_ORIGINS` | ai411 + floridamanweb origins |

**Health:** `GET /health` → `{"ok":true,…}`

### 5.2 site-tracker

**File:** `deployment-tracker.yaml`  
**PVC:** `site-tracker-data` (SQLite)  
**env:**

| Name | Value |
|------|--------|
| `SITES_BASE_URL` | `https://floridamanweb.online` |
| `DESK_PUBLIC_HOST` | `sites.floridamanweb.online` |
| `CUSTOMERS_API` | `http://voice-agent.theswamp.svc.cluster.local:8035` |
| `CUSTOMERS_PATH` | `/data/customers.json` (fallback) |
| `TRACKER_DB` | `/data/tracker.db` |

Tracker **image** must include the Python client that calls `CUSTOMERS_API`
(see monorepo `site-tracker/app.py`). Until that image is built/pushed, API
env is present but old code only reads file.

### 5.3 demo-sites

Serves hashed demos + `/ai411/index.html` from `hosting/Dockerfile`.  
Rebuild when `hosting/ai411/**` or `generated-sites/**` change.

---

## 6. Secrets

### 6.1 voice-agent-keys (Bitwarden ExternalSecret)

**File:** `external-secret-voice.yaml`  
**Bitwarden item UUID:** `f9ca9aff-363f-4f81-a0cb-ab235170f221`  
**Store:** ClusterSecretStore `bitwarden-fields`

Fields (names exact):

- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`
- `DEEPINFRA_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`
- `OWNER_CALLBACK_NUMBER`

Missing field → entire ExternalSecret sync fails.

### 6.2 voice-agent-extra (Honcho / Stripe / email)

**Live (cluster only, not git):**

```bash
kubectl -n theswamp get secret voice-agent-extra
# keys: HONCHO_API_KEY, HONCHO_APP_ID
```

**Staged manifest:** `external-secret-voice-extra.yaml`  
Not in kustomization until every listed Bitwarden custom field exists
(`HONCHO_API_KEY`, `HONCHO_APP_ID`, `STRIPE_PAYMENT_LINK_DEFAULT`,
`EMAIL_FROM`, `RESEND_API_KEY` — use `unused` for unused).

**Add to Bitwarden UI:** item `voice-agent-keys` → custom fields → then add
resource to kustomization and remove any temporary kubectl Secret if ESO owns it.

**Never commit API keys.**

### 6.3 Rotate Honcho

1. Issue new key in Honcho console.  
2. `kubectl -n theswamp create secret generic voice-agent-extra \
     --from-literal=HONCHO_API_KEY=... --from-literal=HONCHO_APP_ID=fmws-voice \
     --dry-run=client -o yaml | kubectl apply -f -`  
3. `kubectl -n theswamp rollout restart deploy/voice-agent`  
4. Update Bitwarden when using ESO.

---

## 7. Flux / PR workflow

### Apply path for DNS + routes + Authentik

1. Land commits on branch tracked by PR (e.g. `feat/theswamp-authentik-flmanbiosci`).  
2. Merge **hwcopeland/iac** PR (**#96** as of 2026-08-11).  
3. Flux reconciles `kube-system` DNSRecords + `theswamp` HTTPRoutes + Authentik CM.  
4. Verify with curl/host commands in §2.

### FMB can apply without Flux

```bash
# Deployments / secrets only
kubectl -n theswamp apply -f rke2/tooling/flux/theswamp/deployment-voice.yaml
kubectl -n theswamp apply -f rke2/tooling/flux/theswamp/deployment-tracker.yaml
# WARNING: Flux on main may overwrite until PR merges — re-apply or suspend Kustomization if needed
```

### Image builds

| Component | Workflow | Image |
|-----------|----------|--------|
| voice-agent | `build-voice-agent.yml` | `…/voice-agent:main` |
| demo-sites | `build-demo-sites.yml` | `…/demo-sites:main` |
| site-tracker | (tracker CI) | `…/site-tracker:main` |
| demo-mcp | `build-demo-mcp.yml` | `…/demo-mcp:main` |

---

## 8. Operational runbooks

### 8.1 Voice unhealthy

```bash
kubectl -n theswamp get pods -l app=voice-agent
kubectl -n theswamp logs -l app=voice-agent --tail=100
curl -fsS https://voice.flmanbiosci.net/health
```

Common:

| Log / event | Fix |
|-------------|-----|
| `Unknown AGENT_MODE 'auto'` | Set `AGENT_MODE=ai411` or ship new image |
| Multi-Attach PVC | Wait; ensure replicas=1 Recreate |
| ImagePull 503 zot | Retry; check zot.hwcopeland.net |
| Crash on missing secret key | ESO item incomplete; fix Bitwarden fields |

### 8.2 Re-apply FMWS voice env after Flux revert

```bash
cd /home/noahtjones/iac
kubectl -n theswamp replace -f rke2/tooling/flux/theswamp/deployment-voice.yaml
kubectl -n theswamp set env deploy/voice-agent AGENT_MODE=ai411   # if image old
kubectl -n theswamp rollout status deploy/voice-agent
```

### 8.3 Inspect customers on PVC

```bash
kubectl -n theswamp exec deploy/voice-agent -- cat /data/customers.json
kubectl -n theswamp exec deploy/voice-agent -- ls -la /data/builder-briefs
```

### 8.4 Builder on operator laptop

```bash
cd /home/noahtjones/demo-websites
./scripts/sync-customers-for-builder.sh
export CUSTOMERS_PATH=$PWD/data/customers.json
```

### 8.5 Twilio

Console → number:

- Voice: `https://voice.flmanbiosci.net/voice/inbound` POST  
- Status: `https://voice.flmanbiosci.net/voice/status`  
- Messaging: `https://voice.flmanbiosci.net/sms/inbound` POST  

### 8.6 Stripe mark-paid (manual until webhook)

```bash
curl -X POST https://voice.flmanbiosci.net/api/billing/mark-paid \
  -H 'content-type: application/json' \
  -d '{"phone":"+1...","stripe_customer_id":"cus_..."}'
```

### 8.7 AI 411 Visit Gainesville events ingest

Refresh the events store used by AI 411 (preserves `source=community`, replaces `visitgainesville`, deletes legacy seed fakes):

```bash
# Local / laptop
unset PYTHONPATH
python3 scripts/ingest_visitgainesville_events.py --out /tmp/events.json
# stdout: DIGEST total=… visitgainesville=… vg_sha256_16=…  (stable; exit 0)

# Prod PVC (after image has events.py + script)
kubectl -n theswamp exec deploy/voice-agent -- \
  python3 /app/scripts/ingest_visitgainesville_events.py
kubectl -n theswamp exec deploy/voice-agent -- \
  python3 -c "import json;print(json.load(open('/data/events.json')).get('events',[]) and 'ok')"
```

Optional CronJob (not Flux-applied by default): monorepo `docs/cronjob-visitgainesville-events.yaml` — every 6h, Forbid concurrency, PVC `voice-agent-data`.

---

## 9. Cutover checklist (DNS + Authentik)

- [ ] PR merged; Flux healthy  
- [ ] `host ai411.floridamanweb.online` resolves  
- [ ] `host sites.floridamanweb.online` resolves  
- [ ] Form loads on ai411 host  
- [ ] Register API succeeds from browser (CORS)  
- [ ] Authentik login on sites.floridamanweb.online  
- [ ] Legacy sites.flmanbiosci.net redirects  
- [ ] voice health 200; HONCHO env present  
- [ ] tracker lists customers via CUSTOMERS_API  
- [ ] demo-sites image includes `/ai411/index.html`  
- [ ] Bitwarden Honcho field + ESO (optional cleanup)  
- [ ] voice image with `auto` shipped; AGENT_MODE flipped  

---

## 10. Related skills

| Skill | Use |
|-------|-----|
| `hwcopeland-cluster-auth` | Authentik patterns, RBAC, outpost |
| `flmanbiosci-ops` | Host matrix, email routing, theswamp honesty |
| `demo-websites` | HTML demos, voice modes, MCP |
| `github-workflows` | PR merge to iac |

---

## 11. PR reference

- **IAC:** https://github.com/hwcopeland/iac/pull/96  
  Branch: `jonesnoaht:feat/theswamp-authentik-flmanbiosci`  
  Includes PeptOdyssey SSO work **and** FMWS DNS/routes/Authentik desk cutover commit.

---

*Last verified: 2026-08-11.*
