# Spike 002 — xAI custom voice from Sesame `conversational_a` timbre

**Goal:** option 2 — clone DeepInfra CSM preset `conversational_a` into an xAI
`voice_id`, keep `VOICE_BACKEND=grok-realtime`, leave `XAI_VOICE_AGENT_ID` unset.

**Prod (2026-09-01):** `GROK_VOICE=itxluk4gpnuj` (console clone name Noah). `XAI_VOICE_AGENT_ID` still unset. Not Sesame CSM.

## Measured 2026-08-31

- Reference: `conversational_a_ref.wav` — 24 kHz 16-bit mono, **57.26 s**, 10
  DeepInfra `sesame/csm-1b` `preset_voice=conversational_a` takes concatenated.
- `GET /v1/custom-voices` → 200, `voices: []`, `cap: 30`
- `POST /v1/custom-voices` → **403** `{"error":"Custom voices are not enabled for this team."}`
- This is **timbre only**. Not CSM inference. Not a prod image.

## Unblock

Enable Custom Voices on the xAI team (console.x.ai; US except Illinois). Then:

```bash
curl -X POST https://api.x.ai/v1/custom-voices \
  -H "Authorization: Bearer $XAI_API_KEY" \
  -F "name=A411 conversational_a timbre" \
  -F "language=en" \
  -F "file=@conversational_a_ref.wav;type=audio/wav"
```

Set `GROK_VOICE=<voice_id>` only after a listen test. Never set `XAI_VOICE_AGENT_ID`.

xAI console cloning also runs speaker-owner verification (passphrase). Synthetic
CSM audio may fail that check even after the team flag is on.
