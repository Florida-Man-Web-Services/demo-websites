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
     agent.py ── Claude (claude-opus-4-8, effort=low)
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

You need: an Anthropic API key, a DeepInfra API key, and a Twilio account with
a voice+SMS phone number (~$1.15/mo; buy a 352 number so callbacks look local).

Expose the server and start it:

```bash
ngrok http 8035                      # copy the https URL into PUBLIC_BASE_URL
uvicorn server:app --port 8035
```

In the Twilio console, set the phone number's **Voice webhook** to
`{PUBLIC_BASE_URL}/voice/inbound` (POST) and the **status callback** to
`{PUBLIC_BASE_URL}/voice/status`.

## Deploy with Docker

The server is containerized (`Dockerfile` + `docker-compose.yml`). The build
context is the repo root so the fallback business list
(`../gainesville-no-website/gainesville_no_website.json`) ships in the image.

```bash
cd voice-agent
cp .env.example .env      # fill in the five keys + PUBLIC_BASE_URL
docker compose up -d --build
```

The server listens on `8035` (published to the host). Point Twilio's webhooks
at your `PUBLIC_BASE_URL` exactly as above — the URL must resolve to this
container from the public internet (a VPS with a domain + TLS, or a tunnel like
`cloudflared` / `ngrok` in front of `localhost:8035`).

The `audio_cache/` and `call-log.csv` live on a named volume
(`voice-agent-data`, mounted at `/data`), so synthesized audio and call
outcomes survive restarts and redeploys. Health is exposed at `/health` and
wired to a container `HEALTHCHECK`.

Run the one-at-a-time dialer from inside the running container:

```bash
docker compose exec voice-agent python call.py --next
```

To build/run without compose (still from the repo root):

```bash
docker build -f voice-agent/Dockerfile -t demo-websites-voice-agent .
docker run -d -p 8035:8035 --env-file voice-agent/.env \
  -v voice-agent-data:/data demo-websites-voice-agent
```

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

## Notes & limits

- Turn latency is ~2–4 s (TTS is generated per-utterance, not streamed).
  Common lines are disk-cached after first synthesis, which helps a lot.
  If you outgrow this, the streaming upgrade path is Vogent's hosted CSM or a
  Pipecat pipeline with a self-hosted model.
- Call state lives in server memory — restart the server and in-flight calls
  drop. Fine for one call at a time.
- Webhook signature validation is off for simplicity; the URL is unguessable
  via ngrok, but add Twilio's `RequestValidator` before running this on a
  stable public domain.
