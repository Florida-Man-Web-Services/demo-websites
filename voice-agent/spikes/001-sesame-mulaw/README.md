# Spike 001 — Sesame CSM WAV 24 kHz → PCMU 8 kHz

**Verdict: do not flip prod.** Keep `VOICE_BACKEND=grok-realtime` + `GROK_VOICE=eve`.
Leave `XAI_VOICE_AGENT_ID` unset.

Measured 2026-08-29 DeepInfra `sesame/csm-1b` (not Vina CPUs):

| Probe | Result |
|-------|--------|
| REST “A411 here.” | HTTP 5.879 s · WAV 24 kHz 16-bit mono |
| `stream=true` | TTFB **1.89 s** · still RIFF WAVE, not μ-law |

Twilio Media Streams need raw PCMU 8 kHz. This harness only transcodes. It is
not imported by `server.py`.

```bash
python3 -c "from transcode import wav24_to_pcmu8; print(len(wav24_to_pcmu8(open('x.wav','rb').read())))"
```
