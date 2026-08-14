# Owner voice authentication — Design Spec

**Date:** 2026-08-14  
**Status:** implemented phases 0–4 (2026-08-14)  
**Product:** FMWS owner site-updates desk (`owner_updates` / paid customers)  
**Plan task:** `t-fmws-product-loop` (parent) · implement via phased slices below  
**Companions:** [PRODUCT_LOOP.md](../../PRODUCT_LOOP.md) · [ARCHITECTURE.md](../../ARCHITECTURE.md) · voice modes skill ref

## Overview

Business owners update demo sites by calling the public/voice line. Today ownership is
**weak Factor 1 only**: Twilio caller ID matched to a registered phone
(`lookup_business` / `unified.caller_owns`). Spoken read-back confirms *content*, not
*identity*.

**Goal:** phone number = Factor 1 (account key); **voice biometrics** = Factor 2,
running **passively** while the owner talks so day-to-day updates feel seamless. High-risk
actions still step up (SMS OTP / challenge). Enrollment and biometric processing are
**consented and disclosed once** — seamless after that, never covert first-use collection.

**Non-goals (this design):**
- Replacing AI disclosure on the call
- Banking-grade identity proofing for domain transfer / legal entity changes
- Using the LLM (“are you the owner?”) as an auth control
- Covert voiceprint collection without enrollment consent

---

## Threat model (owner desk)

| Threat | Mitigated by |
|--------|----------------|
| Random caller claims a business by name | F1: CID must match `trusted_phones` + paid status |
| Family/employee on owner’s phone | F2: speaker verify vs enrolled owner (or enrolled delegate) |
| Replay of enrollment audio / crude deepfake | Liveness on step-up; continuous score + anti-spoof when vendor supports |
| SIM swap / number port | Out-of-band number change; step-up on new CID; dormancy rules |
| LLM jailbreak to skip confirm | **Server-side** `auth_level` gates in bridge/store — never prompt-only |
| Spoofed “confirmation_spoken=true” | Bridge ignores model claim unless session flag set from ASR/tool policy (phase 1+) |

Blast radius today is mostly **local demo HTML / ChangeRequests**. As apply→PR/prod
automates, raise thresholds and always step-up publish paths.

---

## Auth model

### Factors

| Factor | Signal | Source of truth |
|--------|--------|-----------------|
| **F1 — Phone** | Twilio `From` ∈ customer `trusted_phones` (default: primary `phone`) and `status ∈ {paid, active_owner}` | `customers.json` |
| **F2 — Voice** | 1:1 speaker verification score vs enrolled template(s) | Voice auth service + template id on customer |
| **Content** | Spoken read-back + owner affirm | Existing `confirmation_spoken` (content only) |
| **Step-up** | SMS OTP or short liveness phrase | Twilio SMS + session challenge |

### Auth levels (`CallState.auth_level`)

| Level | How earned | Allowed tools / effects |
|-------|------------|-------------------------|
| `anonymous` | No useful CID | AI411 only; no owner tools |
| `cid_only` | F1 match; no usable F2 yet | Read: outline, list open CRs; talk; **no writes** if policy `require_voice_for_write` |
| `voice_soft` | F1 + EMA score ≥ soft after `min_windows` | `create_change_request` (pending queue); cancel own open CRs |
| `voice_hard` | Higher threshold or soft + extra window | Optional auto-queue `apply_change_request` (local demo) |
| `step_up_ok` | SMS OTP or liveness challenge passed this call | High-risk actions (below) |
| `locked` | Too many F2 fails | Human desk / callback; speakable lockout |

**Default policy for v1 owner desk:** writes require ≥ `voice_soft` once enrolled;
unenrolled paid owners may file **pending** CRs at `cid_only` with louder disclosure,
or be forced through enrollment first (product flag `VOICE_ENROLL_REQUIRED_FOR_WRITE`).

### High-risk actions (always need `step_up_ok` even if `voice_hard`)

- Change owner phone / email / trusted_phones
- Payment / Stripe / billing
- Publish / open production PR / domain
- Delete-heavy or whole-site rewrite batches
- Add/remove delegates
- First call after long dormancy (e.g. >90d) or new ANI pattern

### Seamless continuous F2 (runtime)

```text
media windows (VAD speech 2–5s)
    → speaker_verify(template_id, pcm)
    → update score_ema, n_windows, liveness_ok
    → maybe promote auth_level
mutate tool call
    → auth_gate(tool.required_level)
    → mcp_bridge / store  OR  speakable deny / “keep talking” / OTP
```

Owner UX when soft-pending: agent continues natural conversation; after enough speech,
tools unlock without a passphrase. Soft-fail path: polite SMS code, not “biometric failed.”

---

## Customer registry extensions

Extend `mcp-server/customers.py` / `CUSTOMERS_PATH` rows (additive; old rows valid):

```json
{
  "+13550000100": {
    "id": "cust-…",
    "phone": "+13550000100",
    "status": "active_owner",
    "trusted_phones": ["+13550000100"],
    "slug": "cool-cafe",
    "voice_auth": {
      "consent_version": "2026-08-14",
      "consented_at": "2026-08-14T18:00:00+00:00",
      "enrolled_at": "2026-08-14T18:05:00+00:00",
      "vendor": "none|self_host|microsoft|other",
      "template_id": "",
      "quality": null,
      "last_verify_at": null,
      "fail_streak": 0
    },
    "delegates": [
      {
        "phone": "+13550000999",
        "name": "Manager",
        "voice_auth": { "template_id": "", "enrolled_at": null, "consent_version": "" }
      }
    ]
  }
}
```

**Rules:**
- `trusted_phones` empty → treat `[phone]` as sole trusted line.
- Mode resolve stays status-based; **tool gates** enforce auth_level.
- “Forget me” / offboarding deletes templates + clears `voice_auth` (and local enrollment blobs).

---

## CallState / bridge contract

### `CallState` (additions)

| Field | Type | Notes |
|-------|------|-------|
| `auth_level` | str | see table |
| `voice_score_ema` | float \| null | |
| `voice_windows` | int | |
| `voice_enrolled` | bool | |
| `step_up_ok` | bool | this call only |
| `customer` | dict | existing |

### Tool required levels (owner_updates)

| Tool | Min level |
|------|-----------|
| `lookup_business` | `cid_only` (always scope to caller’s businesses) |
| `get_site_outline` | `cid_only` |
| `list_open_change_requests` | `cid_only` |
| `create_change_request` | `voice_soft` if enrolled else policy |
| `cancel_change_request` | `voice_soft` |
| `apply_change_request` | `voice_hard` |
| High-risk profile tools (future) | `step_up_ok` |
| `send_sms_links` | `cid_only` |
| `end_call` | any |

**Hard rule:** `mcp_bridge.run_owner_updates_tool` (or a thin `auth_gate` wrapper) checks
level **before** store calls. Prompt only *narrates*; it cannot grant access.

**LLM sees only:** `identity_verified` / `auth_level` summary strings — never embeddings,
raw scores, or template ids in the system prompt body beyond booleans needed for UX.

---

## Enrollment flow

1. Customer reaches `paid` / `active_owner` (Stripe / mark-paid).
2. Next owner call (or SMS deep link → short web/IVR enroll):
   - Disclose: voice used to recognize them on future update calls; retention; delete path.
   - Capture consent → `voice_auth.consent_version` + `consented_at`.
3. Collect **30–60s** natural speech (first update conversation works) **or** 3–5 guided phrases.
4. Create vendor/self-host template → `template_id`, `enrolled_at`, `quality`.
5. Optional: enroll delegates with their phone + consent + template.
6. Re-enroll on quality drop, yearly, or after repeated verify fails.

Enrollment audio: prefer **ephemeral** process → template; do not retain raw WAV on PVC
longer than needed for quality QA (env `VOICE_ENROLL_RAW_TTL_HOURS`, default 0–24).

---

## Runtime verification path

### Preferred integration point

Fork **PCM/μ-law frames** already crossing `realtime.py` / Twilio Media Streams **without**
breaking Grok realtime. Sidecar or in-process `voice_auth.py`:

```text
Twilio WS → voice-agent
              ├─→ realtime (conversation)
              └─→ voice_auth.on_speech_frame()  # async, non-blocking
```

### Verify API (internal)

```python
def verify_window(*, template_id: str, pcm: bytes, sample_rate: int) -> dict:
    """Return {ok, score, liveness_ok?, error?} — never raise into call loop."""
```

### Thresholds (env, tunable)

| Env | Default (starting point) | Meaning |
|-----|--------------------------|---------|
| `VOICE_AUTH_SOFT` | `0.75` | promote to `voice_soft` |
| `VOICE_AUTH_HARD` | `0.85` | promote to `voice_hard` |
| `VOICE_AUTH_MIN_WINDOWS` | `3` | windows before promote |
| `VOICE_AUTH_FAIL_LOCK` | `5` | consecutive hard fails → `locked` |
| `VOICE_ENROLL_REQUIRED_FOR_WRITE` | `false` initially | flip `true` after enroll UX ships |
| `VOICE_AUTH_VENDOR` | `none` | phase 0–1 stub |

When `VOICE_AUTH_VENDOR=none`, levels stay `cid_only` (phase 0–1 architecture) so gates
and tests exist before a real SV backend.

### Step-up SMS OTP

Reuse Twilio SMS helper pattern (see sales notify design):
- 6-digit code, 5–10 min TTL, store hash on session only
- Max attempts; then `locked`
- Speakable: “I texted a quick code — what are the digits?”

---

## Phased delivery

| Phase | Deliverable | Done when |
|-------|-------------|-----------|
| **0 — Harden F1** | `trusted_phones`; server-side phone match on every CR create; block owner mutate if status not paid/active_owner; ambiguous multi-biz prompt stays | Tests: wrong CID cannot create CR for another slug |
| **1 — Auth levels** | `CallState.auth_level`; tool→level map; bridge gate; stub F2 (`none` vendor); speakable denies | Owner tools refuse writes at `anonymous`; reads at `cid_only` |
| **2 — Enroll + verify** | Consent + enroll API/call flow; vendor or self-host 1:1 verify; gate create at `voice_soft` | Paid owner enrolls once; subsequent call reaches soft after natural speech |
| **3 — Continuous + step-up** | EMA windows; OTP step-up; high-risk list; delegates | Seamless happy path; rare OTP on risk |
| **4 — Anti-spoof harden** | Liveness challenges; template aging; anomaly (dormancy/new ANI) | Documented runbook + metrics |

**Do not block** product-loop ship of `AGENT_MODE=auto` / Stripe webhook on phase 2+.
Phase 0–1 can land in parallel with image/auto cutover.

---

## Code touch map (implementation)

| Area | Change |
|------|--------|
| `mcp-server/customers.py` | `trusted_phones`, `voice_auth`, delegates helpers; normalize multi-phone lookup |
| `mcp-server/changerequests.py` | Enforce caller phone ownership server-side on create/cancel |
| `voice-agent/owner_updates.py` | Prompt: seamless verify, never claim biometric details; enroll script |
| `voice-agent/mcp_bridge.py` | `auth_gate` before owner tools |
| `voice-agent/server.py` / `agent.py` / `realtime.py` | CallState init; optional frame fork |
| `voice-agent/voice_auth.py` | **New** — enroll/verify/stub vendor adapters |
| `voice-agent/tests/` | `test_owner_auth_gate.py`, extend `test_owner_updates.py` |
| Docs | PRODUCT_LOOP § owner auth; ARCHITECTURE § security; this spec |

---

## Compliance / privacy (ops, not legal advice)

- Disclose biometric purpose at **enrollment** (and policy URL when available).
- Store **templates**, minimize raw audio retention.
- Honor delete: template + customer voice fields + related call-side embeddings.
- Multi-state owners may trigger biometric-identifier rules (e.g. BIPA-like regimes) —
  **counsel review before national scale-out**.
- Keep existing TCPA/FTSA AI-voice program separate; this design is **inbound owner
  service**, not outbound artificial-voice sales.
- Do not market “unforgeable voice ID.”

Flag for counsel when: multi-state launch, selling voice auth as a feature, or retaining
raw audio > short QA window.

---

## Observability

Log (no raw audio in logs):
- `call_sid`, `auth_level` transitions, `n_windows`, score **buckets** (not full embedding)
- enroll success/fail, OTP send/verify, lockouts
- metric: `% owner calls reaching voice_soft before first CR`

Desk (site-tracker, later): badge `voice enrolled` / `cid only` on customer row.

---

## Testing plan

```bash
cd voice-agent
unset PYTHONPATH
.venv/bin/python -m pytest \
  tests/test_owner_updates.py \
  tests/test_owner_auth_gate.py \
  tests/test_customer_routing.py -q
```

Cases:
1. Non-owner CID → cannot create CR for slug X  
2. Owner CID, vendor none, enroll required false → phase policy behavior  
3. Owner CID, mock score path → promote soft → create OK  
4. Low score → deny create; OTP path sets `step_up_ok` → high-risk OK  
5. Prompt isolation: no other product brand strings; no embedding material in prompts  

---

## Open decisions (resolved defaults)

| Topic | Default until operator overrides |
|-------|----------------------------------|
| Build vs buy SV | **Stub + adapter interface first**; pick vendor at phase 2 spike (≤1 day) |
| Enroll required for write | **false** until enroll UX live, then **true** |
| Delegates | Schema ready phase 2; UI/voice UX phase 3 |
| Auto-apply local HTML | Still prefers read-back; `voice_hard` optional later |

---

## Acceptance (product)

- Owner calls from enrolled cell, talks naturally, files hour/phone/copy CR **without**
  saying a passphrase or “authenticate me.”
- Attacker on unknown number cannot mutate.
- Attacker on stolen phone without voice match cannot reach write without OTP (once F2 on).
- Counsel/privacy checklist stubbed in company compliance when scaling.

---

## Related

- Weak phone auth today: `voice-agent/owner_updates.py`, `unified.caller_owns`
- Modes pattern: skill `demo-websites` → `references/voice-agent-modes.md`
- Product loop: `docs/PRODUCT_LOOP.md` step E → owner_updates
- Arete plan: `t-fmws-product-loop` (`fmws-cash`)


---

## Implementation status (2026-08-14)

Phases **0–4 delivered** in `demo-websites` main:

| Phase | Deliverable |
|-------|-------------|
| 0 | F1 trusted phones + CR ownership (`OWNER_CR_AUTH`) |
| 1 | `CallState.auth_level` + tool gates (`voice_auth.py`) |
| 2 | Enroll + mock/local_stub promote |
| 3 | Realtime speech windows + SMS OTP step-up |
| 4 | Dormancy/new ANI/template age/fail streak; PCM replay guard; pluggable vendors (`none`/`mock`/`local_stub`/`http`) |

Prod default remains `VOICE_AUTH_VENDOR=none` (F1 + OTP on apply/anomalies). Wire a real SV backend via `VOICE_AUTH_VENDOR=http` + `VOICE_AUTH_HTTP_URL`.
