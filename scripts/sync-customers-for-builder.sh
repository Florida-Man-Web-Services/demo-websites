#!/usr/bin/env bash
# Sync customers registry + list builder briefs for coding agents on this host.
# Source of truth in-cluster: voice-agent PVC at CUSTOMERS_PATH=/data/customers.json
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${CUSTOMERS_LOCAL_PATH:-$ROOT/data/customers.json}"
BRIEFS="${BUILDER_BRIEFS_LOCAL:-$ROOT/data/builder-briefs}"
API="${CUSTOMERS_API:-https://voice.flmanbiosci.net}"
mkdir -p "$(dirname "$OUT")" "$BRIEFS"
echo "Fetching customers from $API ..."
if curl -fsS "$API/api/onboarding/customers" -o /tmp/customers-api.json; then
  python3 - <<'PY'
import json, pathlib, os
raw = json.loads(pathlib.Path("/tmp/customers-api.json").read_text())
rows = raw.get("customers") or []
out = {r["phone"]: r for r in rows if r.get("phone")}
path = pathlib.Path(os.environ.get("OUT", "data/customers.json"))
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(out, indent=2) + "\n")
print(f"wrote {len(out)} customers -> {path}")
for r in rows:
    if r.get("status") in ("requirements_ready", "building", "demo_ready"):
        print(f"  build-candidate {r.get('phone')} {r.get('business_name')} status={r.get('status')} brief={r.get('builder_brief_path')}")
PY
else
  echo "API fetch failed — if you have kubectl:"
  echo "  kubectl -n theswamp exec deploy/voice-agent -- cat /data/customers.json > $OUT"
fi
export OUT
