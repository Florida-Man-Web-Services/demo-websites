# Voice Sales Agent — Sesame voice + Claude brain

An AI phone agent that sells the Gainesville demo websites. It speaks with
[Sesame's](https://www.sesame.com) open-source **CSM-1B** voice model, thinks
with **Claude**, and runs calls over **Twilio**. It knows every business in
`correspondences/outreach-data.csv`, pitches from the campaign's
`phone-script.md`, texts demo links on request, and logs every outcome to
`call-log.csv`.

```
 caller ── Twilio (phone + speech-to-text)
              │  webhook per spoken turn
              ▼
        server.py (FastAPI)
              │
     agent.py ── Claude (claude-opus-4-8, effort=low) or Grok (LLM_PROVIDER=grok)
              │      tools: send_demo_link_sms · log_call_outcome · end_call
              ▼
       tts.py ── Sesame CSM-1B via DeepInfra ── cached WAVs ── <Play> to caller
```

## Why DeepInfra for the Sesame voice?

As of July 2026 Sesame has **no public first-party API** (developer access is
invite-only). The options for their voice models:

| Route | Notes |
|---|---|
| **DeepInfra hosted `sesame/csm-1b`** (default here) | Pay-per-use ($7 / 1M characters ≈ half a cent per call), no GPU needed. Same Apache-2.0 model Sesame open-sourced. |
| Self-host [`sesame/csm-1b`](https://huggingface.co/sesame/csm-1b) | Needs a GPU (RTX 4090 / L40S class). Set `SESAME_TTS_URL` to your endpoint. |
| [Vogent](https://www.vogent.ai/sesame) | Voice-agent platform with a low-latency re-architected CSM-1B (200–400 ms). The upgrade path if turn latency here becomes a problem — email them for TTS API access. |

## Setup

```bash
cd voice-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the five keys
```

On Nix: `nix develop` from anywhere in the repo (or `nix-shell` in this
directory) replaces all of the above — it bootstraps `.venv`, creates `.env`
from the example, brings in ffmpeg + libopus + ngrok (nixpkgs pinned by the
root `flake.lock`), and runs `doctor.py`: a live checklist of the remaining
human steps (keys, Twilio, public URL) with the exact next command for each.
Re-run `python doctor.py` after editing `.env`.

You need: an Anthropic API key, a DeepInfra API key, and a Twilio account with
a voice+SMS phone number (~$1.15/mo; buy a 352 number so callbacks look local).

### Running on Grok instead of Claude

The conversation brain is provider-switchable. To run calls on xAI's Grok, set
in `.env`:

```bash
LLM_PROVIDER=grok
XAI_API_KEY=xai-...        # from console.x.ai
# GROK_MODEL=grok-4.3      # default; grok-4.5 if latency matters more than cost
```

Same prompt, same tools, same latency tricks (streamed sentences into TTS).
Claude remains the default (`LLM_PROVIDER=anthropic`); with Grok selected the
Anthropic key is unused. Grok tool calls arrive via xAI's OpenAI-compatible
API (`https://api.x.ai/v1`).

### Grok realtime voice (speech-to-speech)

`VOICE_BACKEND=grok-realtime` replaces the whole per-turn pipeline
(Twilio speech-to-text → LLM → Sesame TTS) with a bidirectional bridge:
Twilio streams the call's raw audio to `/voice/stream`, which pumps it to
[xAI's realtime API](https://docs.x.ai/developers/model-capabilities/audio/voice-agent)
and plays Grok's spoken replies back. Both sides talk G.711 μ-law at 8 kHz,
so audio passes through untranscoded — turn latency drops to well under a
second, with real barge-in (the agent stops when interrupted).

```bash
VOICE_BACKEND=grok-realtime
XAI_API_KEY=xai-...
# XAI_VOICE_AGENT_ID=agent_...   # see warning below before setting this
# GROK_VOICE=eve                 # eve | ara | rex | sal | leo | custom ID
# GROK_VAD_SILENCE_MS=600
```

The same per-call system prompt and the same three tools (SMS the demo link,
log the outcome, hang up) are wired into the realtime session, so behavior
and `call-log.csv` records match the pipeline. DeepInfra/Sesame and the
Anthropic key are unused in this mode.

> **Leave `XAI_VOICE_AGENT_ID` unset.** A console-configured voice agent
> auto-starts its own greeting the moment the socket connects — in its
> default 24 kHz PCM16 format, racing (and usually beating) this bridge's
> mu-law `session.update`. Twilio plays that PCM as mu-law: a loud static
> blast at the top of every call. The bridge supplies its own instructions,
> tools, and voice, so a console agent adds nothing here anyway.

Test without a phone:

```bash
python realtime_chat.py <business-slug>   # text in, spoken transcript out
```

Expose the server and start it:

```bash
ngrok http 8035                      # copy the https URL into PUBLIC_BASE_URL
uvicorn server:app --port 8035
```

In the Twilio console, set the phone number's **Voice webhook** to
`{PUBLIC_BASE_URL}/voice/inbound` (POST) and the **status callback** to
`{PUBLIC_BASE_URL}/voice/status`.

### Test it on yourself first

Call your Twilio number — the agent answers as an inbound call. Then try an
outbound call to your own cell:

```bash
python call.py --to +1YOURCELL hayes-jewelry-ltd
```

## Daily use

```bash
python call.py --list          # next 10 businesses in the priority queue
python call.py --next          # call the top uncalled business (asks first)
python call.py ole-barn        # call a specific business by slug
```

Every call appends a row to `call-log.csv` (outcome, email captured, callback
time, notes). `--next` skips anything already logged. Businesses that call
back are recognized by caller ID and greeted by name.

## Compliance — read before dialing strangers

Outbound calls that use an AI-generated voice are legally "artificial voice"
calls. The FCC's Feb 2024 ruling puts them squarely under the **TCPA**:
calling a **cell phone** with an artificial voice requires the recipient's
*prior express consent*, and many small-business numbers are cell phones.
Violations carry $500–$1,500 statutory damages **per call**. Florida's FTSA
adds state-level exposure. This is why `call.py` is deliberately a
one-at-a-time, human-confirmed dialer and not a batch robodialer.

The lower-risk playbook this repo supports:

1. **Inbound first.** Send the letters/emails/voicemails from
   `correspondences/` yourself, with the Twilio number as the callback. The
   agent answering calls *they* place to *you* is consent-safe.
2. **Human-initiated outbound, sparingly.** You review and confirm each dial.
   The agent identifies itself as an AI in its first sentence (hard-coded into
   the system prompt) and honors "don't call again" via the `do_not_call`
   outcome, which permanently removes the business from `--next`.
3. Call only 8am–8pm local time, and check numbers against the DNC registry.
4. **SMS:** A2P messaging from a Twilio number requires 10DLC campaign
   registration (one-time, in the Twilio console) or texts will be filtered.

None of this is legal advice; if outbound volume ever matters, spend an hour
with an actual TCPA attorney first.

## Costs (rough, per 3-minute call)

- Twilio voice: ~$0.042 (1.4¢/min) + STT ~$0.06
- Claude (Opus 4.8, ~8 short turns): ~$0.05–0.15
- Sesame TTS via DeepInfra (~1,200 chars): ~$0.008
- **Total ≈ $0.15–0.25 per call** — one converted site pays for hundreds.

## AI 411 mode (`AGENT_MODE=ai411`)

The default agent is the **sales** pitch for Florida Man Web Services demo
sites. Set `AGENT_MODE=ai411` (or `VOICE_AGENT_MODE=ai411`) to run as
**Gainesville AI 411** — a local directory / events / community-broadcast
operator with **no** $999 sales pitch.

```bash
# voice-agent/.env
AGENT_MODE=ai411
# same Twilio / LLM / VOICE_BACKEND keys as sales mode
```

| | Sales (default) | AI 411 |
|---|---|---|
| Identity | Owner's AI assistant pitching free demos | Gainesville AI 411 community operator |
| Greeting flavor | Sales disclosure + demo offer | "Gainesville AI 411 — events, businesses, or post something?" |
| Tools | `send_demo_link_sms`, `log_call_outcome`, `end_call` | `search_business_knowledge`, `lookup_business`, `search_events`, `get_event`, `get_caller_profile`, `update_caller_profile`, `forget_caller`, `submit_event_broadcast`, `submit_notice_broadcast`, `list_recent_broadcasts`, `send_sms_links`, `end_call` |
| Safety | AI disclosure; TCPA / do-not-call | AI disclosure; emergencies → 911; no medical/legal advice |

**Live stores (#51):** when `AGENT_MODE=ai411`, tool calls dispatch via
`mcp_bridge.py` to the same tools as `mcp-server/` (`knowledge`, `events`,
`callers`, `broadcasts`, `lookup`). Results are JSON strings for the LLM. On
failure, tools return a speakable stub so calls do not crash.
`send_sms_links` / `end_call` stay local in `agent.py` (Twilio when configured).

| `MCP_MODE` | Behavior |
|---|---|
| `inproc` (default) | Import store modules from the monorepo `mcp-server/` tree (no HTTP hop). |
| `http` | Streamable HTTP `tools/call` against `MCP_URL` with `Authorization: Bearer MCP_AUTH_TOKEN`. |
| `auto` | Try inproc import; if it fails and `MCP_URL` is set, fall back to HTTP. |

Use `http` or `auto` when the voice container does **not** share the mcp-server
data filesystem (e.g. production: `MCP_URL=https://mcp.flmanbiosci.net/mcp`).

| Env | Default / notes |
|---|---|
| `MCP_MODE` | `inproc` \| `http` \| `auto` |
| `MCP_URL` | e.g. `https://mcp.flmanbiosci.net/mcp` (required for http) |
| `MCP_AUTH_TOKEN` | Bearer token for remote MCP (required for http) |
| `KNOWLEDGE_DIR` | `generated-sites/` (HTML knowledge index; inproc) |
| `EVENTS_PATH` | `/data/events.json` or repo `data/events.json` (auto-seeded; inproc) |
| `CALLERS_PATH` | `/data/callers.json` (inproc) |
| `BROADCASTS_PATH` | `/data/broadcasts.jsonl` (inproc) |

Sales mode is unchanged when `AGENT_MODE` is unset or `sales`.

Implementation: `ai411.py` (prompt + tool schemas), `mcp_bridge.py` (inproc +
HTTP dispatch), selected from `agent.system_prompt` / `agent.get_tools()` /
`_run_tool` via `config.AGENT_MODE`.

## Owner updates mode (`AGENT_MODE=owner_updates`)

Phone intake desk for **business owners** filing structured site
`ChangeRequest`s against demo pages — not the sales pitch and not AI 411.

```bash
# voice-agent/.env
AGENT_MODE=owner_updates
# same Twilio / LLM / VOICE_BACKEND keys as other modes
```

| | Sales (default) | AI 411 | Owner updates |
|---|---|---|---|
| Identity | Pitch free demos | Gainesville AI 411 | Owner site-updates desk |
| Auth | Outbound target business | Optional caller memory | Weak: match caller phone → business (`lookup_business`); ambiguous → ask which; spoken warning |
| Tools | SMS demo, log outcome, end | Directory / events / broadcasts | `lookup_business`, `get_site_outline`, `create_change_request`, `list_open_change_requests`, `cancel_change_request`, `apply_change_request` (optional), `send_sms_links`, `end_call` |
| Flow | Pitch → SMS link | Search / post | Outline → capture items → read back → `create_change_request` with `confirmation_spoken=true` |

**Live stores (#52 intake):** tools dispatch **in-process** to
`mcp-server/changerequests.py` and `lookup.py` via `mcp_bridge.run_owner_updates_tool`.
`items` may be a list or a JSON array string; `caller_phone` defaults from the
call state. `apply_change_request` is optional — warn that it updates local
demo HTML only; shipping a PR is a separate step.

| Env | Default / notes |
|---|---|
| `CHANGE_REQUESTS_PATH` | repo `data/change-requests.jsonl` (or `/data/…`) |
| `GENERATED_SITES_DIR` | repo `generated-sites/` |

Implementation: `owner_updates.py` (prompt + tool schemas), `mcp_bridge.py`
(owner dispatch), selected via `config.AGENT_MODE` / `config.is_owner_updates()`.

## Unified mode (`AGENT_MODE=unified`) — one public number

Everyone calls the same number. Every caller gets the full **Gainesville
AI 411** surface (directory / events / broadcasts); a caller whose caller ID
matches a business's phone line additionally gets the **owner updates**
surface — change requests scoped to *their* site only, with that site's text
injected into the prompt so edits are grounded in what the page says.

```bash
# voice-agent/.env
AGENT_MODE=unified
# same Twilio / LLM / VOICE_BACKEND keys as other modes
```

Ownership is verified by caller ID (`unified.caller_owns`) and enforced in
`agent._run_tool` — owner tool calls from a non-matching number are refused
in code, not just discouraged in the prompt. Businesses with no phone on
file can never unlock owner tools. The safety net is unchanged from owner
updates mode: requests are structured intake, `apply_change_request` touches
the local demo HTML only, and shipping anything live is a separate reviewed
step.

Implementation: `unified.py` (tool-name union + prompt layering over
`ai411.system_prompt`), routing by tool name in `agent._run_tool` via
`unified.OWNER_TOOL_NAMES`.

## Server deployment (hwcopeland's cluster)

Push to `main` → GitHub Actions builds
`zot.hwcopeland.net/florida-man-bioscience/voice-agent:main` → Flux image
automation rolls out the `voice-agent` Deployment in `theswamp`
(manifests: iac repo, `rke2/tooling/flux/theswamp/*-voice.yaml`), serving
`https://voice.flmanbiosci.net`. Same pipeline as `mcp-server/`.

- `PUBLIC_BASE_URL=https://voice.flmanbiosci.net` is set in the Deployment;
  point the Twilio number's Voice webhook at
  `https://voice.flmanbiosci.net/voice/inbound` and the status callback at
  `/voice/status` (one-time Twilio console change, replacing the ngrok URL).
- API keys come from the Bitwarden item `voice-agent-keys` via
  External Secrets (custom fields, one per env var).
- `call-log.csv` and the audio cache persist on a Longhorn PVC at `/data`.
  To migrate laptop history (do-not-call permanence lives there):
  `kubectl -n theswamp cp voice-agent/call-log.csv <pod>:/data/call-log.csv`
  before pointing Twilio at the cluster.
- `call.py` keeps working from anywhere — it talks to Twilio's REST API, and
  Twilio then webhooks whichever server `PUBLIC_BASE_URL` names. Run it
  locally with `PUBLIC_BASE_URL=https://voice.flmanbiosci.net`.

## Notes & limits

- Turn latency is ~2–4 s (TTS is generated per-utterance, not streamed).
  Common lines are disk-cached after first synthesis, which helps a lot.
  If you outgrow this, the streaming upgrade path is Vogent's hosted CSM or a
  Pipecat pipeline with a self-hosted model.
- Call state lives in server memory — restart the server and in-flight calls
  drop. Fine for one call at a time (`replicas: 1` in the Deployment).
- Webhook posts are rejected unless Twilio's `X-Twilio-Signature` validates
  against `PUBLIC_BASE_URL` (see `VALIDATE_TWILIO_WEBHOOKS`, on by default).
  If webhooks 403 after a domain change, the base URL and the Twilio console
  URL have drifted apart.
