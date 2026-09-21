# AI 411 operator runbook

Task-focused procedures for the human on-call operator. Last verified:
2026-09-21. Companion: [PROMISES.md](./PROMISES.md) ·
[ARCHITECTURE.md](./ARCHITECTURE.md) · [PRODUCT_LOOP.md](./PRODUCT_LOOP.md) ·
[OPS_CLUSTER.md](./OPS_CLUSTER.md) · [ai411-audit/](./ai411-audit/).

---

## 1. Daily health check (2 minutes)

```bash
curl -s https://voice.flmanbiosci.net/health | python3 -m json.tool
```

| Field | Healthy | Meaning |
|-------|---------|---------|
| `ok` | `true` | Voice service is up |
| `active_calls` / `active_sms_sessions` | number | In-flight conversations |
| `customers_registry` | `true` | customers.json loads on the PVC |
| `personal_pages` | `true` | Personal-page store loads |
| `agent_mode` | `auto` | Per-call mode routing active |
| `voice_auth_vendor` | `none` | Voice biometrics OFF (F2 not enabled — expected) |

Then:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://ai411.floridamanweb.online/ai411/
curl -s -o /dev/null -w '%{http_code}\n' https://floridamanweb.online/
```

Both must be `200`. If `/health` is down: check pods (§2), then Twilio console
for webhook errors. **Never** answer customer questions about incidents until
you have read the health output yourself.

## 2. Live configuration

```bash
kubectl -n theswamp get deploy voice-agent -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}{"="}{.value}{"\n"}{end}'
kubectl -n theswamp get deploy demo-mcp   -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}{"="}{.value}{"\n"}{end}'
```

Secrets (`voice-agent-keys`, `voice-agent-extra`) are Bitwarden-backed External
Secrets — values are never in the Deployment spec.

## 3. Customer funnel surgery

Source of truth: `/data/customers.json` on PVC `voice-agent-data` (voice pod
only). **Never hand-edit the JSON while pods are running** — use the customers
API on the voice service or the MCP `customers` tools.

```bash
# List / inspect
curl -s http://voice-agent.theswamp.svc.cluster.local:8035/api/onboarding/customers
```

Statuses: `prospect → callback_queued → onboarding → requirements_ready →
building → demo_ready / sales_ready → paid / active_owner → churned /
do_not_call`. Mode routing (AGENT_MODE=auto) is derived from status — a wrong
status sends the caller to the wrong persona. Change it deliberately and note
the reason in the row.

**`do_not_call` is a hard suppression**: callback dialing refuses it.

## 4. Callback queue

Callbacks are dialled from queued customers by `callback_dial.py`.
Idempotency: one dial per phone per 24 h (row field `callback_sid` /
`callback_last_call`; window override `CALLBACK_DEDUPE_WINDOW_S`).

- **Missed/failed callback:** check the customer row status; requeue by setting
  status back to `callback_queued` (clears nothing else) and re-trigger from the
  site desk or wait for the owner to call in.
- **Duplicate-call complaint:** check `callback_sid` on the row before
  assuming a bug; a second call within the window should be impossible.

## 5. Change requests (owner edits)

Lifecycle: `create_change_request` (voice) → `apply_change_request`
(deterministic edit) → `mark_request_shipped` → `open_site_update_pr` →
automerge → CI → demo-sites image roll.

```bash
gh run list --workflow=build-demo-sites.yml --limit 1   # CI status
gh run view <run-id> --log | grep 'exporting manifest list'   # image digest
kubectl -n theswamp set image deploy/demo-sites \
  demo-sites=zot.hwcopeland.net/florida-man-bioscience/demo-sites:main@sha256:<digest>
kubectl -n theswamp rollout status deploy/demo-sites
```

- Flux reconcile is **disabled** on `demo-sites` and `demo-mcp` — image bumps
  are manual (above) or via the ship pipeline.
- CI runs an **asset-graph audit**; a red build means a clone's JS graph is
  broken or cache-poisoned — do not force-roll a stale image. See
  `mcp-server/assetgraph.py` and the Astra audit (G06).
- If a PR fails the asset-graph audit at PR time, the voice pod's copy of the
  HTML is stale — resync `/app/generated-sites` + `/data/generated-sites` from
  the repo before shipping.

## 6. Rolling demo-mcp / voice-agent

```bash
gh run list --workflow=build-demo-mcp.yml --limit 1
gh run view <run-id> --log | grep 'exporting manifest list'
kubectl -n theswamp set image deploy/demo-mcp \
  demo-mcp=zot.hwcopeland.net/florida-man-bioscience/demo-mcp:main@sha256:<digest>
kubectl -n theswamp rollout status deploy/demo-mcp
```

voice-agent equivalent: workflow `build-voice-agent.yml`, deployment
`voice-agent`. After rolling voice-agent, re-run the §1 health check.

## 7. Tests before any push

```bash
cd voice-agent && python3 -m pytest tests/ -q     # 152 must pass
cd mcp-server && python3 -m pytest tests/ -q
python3 scripts/audit_asset_graphs.py             # from repo root
```

## 8. Known gaps (Astra audit, 2026-09-21)

Full list: [ai411-audit/2026-09-21-astra-audit-G01-G48.md](./ai411-audit/2026-09-21-astra-audit-G01-G48.md).
Headline P0s: payment activation is manual (G11); no automated backups of the
voice PVC (G09); authority matrix not yet formally verified (G03); Stripe
webhooks not yet implemented (G18); abuse/moderation loop is primitives-only
(G10). No production-sensitive enablement (voice biometrics, A2P bulk SMS)
without explicit Noah approval.

## 9. Escalation

- **Noah only:** entity/legal/contract questions, TCPA/A2P decisions, cash &
  pricing policy, stop-the-line (legal threat, breach, public efficacy claim).
- **Cluster/DNS/Authentik:** see OPS_CLUSTER.md; IAC lives in
  `hwcopeland/iac` (private — Noah reviews; branches mirror to
  `jonesnoaht/iac`).
- **Astra consults:** briefs in `~/.hermes/astra/briefs/`, runs in
  `~/.hermes/astra/runs/`.
