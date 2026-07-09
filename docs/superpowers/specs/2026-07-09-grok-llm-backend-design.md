# Grok LLM backend for the voice sales agent

Date: 2026-07-09
Status: implemented (autonomous background job — assumptions stated below)

## Request

Background job titled "Website Sales Agent Grok". Interpreted as: make the
voice website-sales agent able to run on xAI's Grok as its conversation LLM.
The user was away, so the design proceeds on stated assumptions rather than
interactive brainstorming.

## Assumptions

- Grok is wanted as a *switchable* backend, not a replacement: Claude stays
  the default so nothing changes for existing setups.
- Selection belongs in env config (`LLM_PROVIDER=grok`), consistent with the
  rest of `config.py`.
- No `XAI_API_KEY` exists in `.env` yet; the code must fail fast with a
  readable message when the grok provider is selected without one.

## Facts checked (2026-07-09, docs.x.ai)

- xAI's current docs support the **OpenAI-compatible** endpoint
  `https://api.x.ai/v1/chat/completions` with SSE streaming and function
  calling (max 128 functions, `tools` array).
- The Anthropic-SDK compatibility path (base_url swap) is **no longer
  documented** — rejected as a foundation.
- Current text models include `grok-4.5` (500k ctx, $2/$6 per 1M tokens,
  billed as their fastest general model) and `grok-4.3` (1M ctx,
  $1.25/$2.50). Default here: `grok-4.3`, overridable via `GROK_MODEL`.

## Design

One seam in `agent.py`: `run_turn()` keeps all call logic (turn caps,
sentence streaming to TTS, tool dispatch, end-of-call handling) and delegates
the provider-specific parts to a backend object owned by `CallState`:

- `_ClaudeBackend` — the existing `anthropic` streaming code, unchanged in
  behavior (`output_config effort=low`, Anthropic-native message history).
- `_GrokBackend` — `openai` client with `base_url=https://api.x.ai/v1`,
  OpenAI-format message history, streamed deltas fed to the same sentence
  splitter, streamed `tool_calls` accumulated by index and JSON-decoded.

Backend interface (duck-typed, both classes):

- `has_history() -> bool`
- `add_user(text)`
- `stream(sys_prompt, on_delta) -> _TurnResult(text_parts, tool_calls)`
- `add_tool_results([(tool_call_id, result_text)])`

`TOOLS` stays in Anthropic schema as the single source of truth;
`_openai_tools()` converts (`input_schema` -> `function.parameters`).

Config additions (`config.py`, `.env.example`):

- `LLM_PROVIDER` — `anthropic` (default) | `grok`
- `XAI_API_KEY`
- `GROK_MODEL` — default `grok-4.3`

`openai` becomes a dependency in `requirements.txt` but is imported lazily so
Claude-only installs never need it at runtime.

## Error handling

- Unknown `LLM_PROVIDER` or missing `XAI_API_KEY` with `grok`: raise
  `SystemExit` with the same style of message as `config.require()` — at
  backend construction (call start), not mid-call.
- Malformed streamed tool-call JSON: treated as `{}` so `_run_tool` returns a
  readable error string to the model instead of crashing the call.

## Addendum: Grok realtime voice bridge (same day)

The user followed up with a JS snippet for `wss://api.x.ai/v1/realtime` and a
console voice-agent id — the fuller intent is speech-to-speech. Added
`VOICE_BACKEND=grok-realtime`:

- `/voice/inbound|outbound` return `<Connect><Stream url=wss://…/voice/stream>`
  TwiML with direction/number/slug `<Parameter>`s instead of the Gather loop.
- `realtime.py` bridges Twilio Media Streams to the xAI realtime API. Both
  sides use G.711 μ-law 8 kHz base64 (`audio/pcmu` is natively supported), so
  media payloads pass through untouched.
- Per-call `session.update` carries the shared `system_prompt()` (minus the
  pre-recorded-opener rules, which only exist to mask TTS latency) plus the
  same three tools converted to the realtime flat function format; VAD is
  `server_vad` with configurable `silence_duration_ms`.
- Barge-in: `input_audio_buffer.speech_started` → Twilio `clear`.
- Hangup: after `end_call`, on `response.done` a Twilio `mark` is sent; when
  Twilio echoes it (farewell audio finished) the socket closes, which ends
  the call since nothing follows `<Connect>`. A 10 s watchdog covers a lost
  mark.
- `XAI_VOICE_AGENT_ID` connects with `?agent_id=…` (console voice/config),
  otherwise `?model=grok-voice-latest`; instructions/tools are still applied
  per call either way.
- `realtime_chat.py`: text-mode session smoke test (no Twilio/audio), SMS
  stubbed like `chat.py`.

## Testing

- Mocked-stream unit check of `_GrokBackend` (delta accumulation, tool-call
  assembly across chunks, tool-result message format) — no network needed.
- Live smoke of the default Claude path via `chat.py` to confirm the refactor
  did not change behavior.
- Live Grok verification requires an `XAI_API_KEY`, which does not exist in
  this environment — left to the user (documented in README).
