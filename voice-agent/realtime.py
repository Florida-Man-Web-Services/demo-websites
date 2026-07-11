"""Twilio Media Streams <-> xAI Grok realtime speech-to-speech bridge.

Enabled with VOICE_BACKEND=grok-realtime. Instead of the per-turn pipeline
(Twilio speech-to-text -> LLM -> Sesame TTS), the call's raw audio is pumped
both ways between Twilio and wss://api.x.ai/v1/realtime. Both sides speak
G.711 mu-law at 8 kHz as base64, so payloads pass through untouched — no
transcoding, no per-turn webhooks, sub-second turn latency.

The same three call tools (send_demo_link_sms / log_call_outcome / end_call)
and the same per-call system prompt are wired into the realtime session, so
call behavior and logging match the pipeline backend.

run_call() only sees two duck-typed adapters (async send / async iterate /
close), which keeps the bridge testable without sockets.
"""

import asyncio
import json
import logging

import config
from agent import TOOLS, CallState, _run_tool, system_prompt

log = logging.getLogger("voice-agent.realtime")

MARK_GOODBYE = "goodbye-played"  # Twilio echoes this once the farewell audio ran
HANGUP_GRACE_S = 10  # close anyway if Twilio never echoes the mark

REALTIME_EXTRA_RULES = """

REALTIME VOICE NOTES
- You are speaking directly with your own voice on a live phone line; talk
  like a natural, warm phone conversation — brief turns, real pauses.
- When you use end_call, say your goodbye in the same turn; the line closes
  after it finishes playing.
"""


def realtime_tools() -> list:
    """agent.TOOLS (Anthropic schema) in the realtime API's flat format."""
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }
        for t in TOOLS
    ]


def session_update(state: CallState) -> dict:
    session = {
        "instructions": system_prompt(
            state.business, state.direction, state.caller_number, openers=False
        )
        + REALTIME_EXTRA_RULES,
        "tools": realtime_tools(),
        "turn_detection": {
            "type": "server_vad",
            "silence_duration_ms": config.GROK_VAD_SILENCE_MS,
        },
        "audio": {
            "input": {
                "format": {"type": "audio/pcmu", "rate": 8000},
                "transcription": {"language_hint": "en"},
            },
            "output": {"format": {"type": "audio/pcmu", "rate": 8000}},
        },
    }
    if config.GROK_VOICE:  # otherwise keep the session/console-agent default
        session["voice"] = config.GROK_VOICE
    return {"type": "session.update", "session": session}


def xai_url() -> str:
    # A console-configured voice agent wins; otherwise pick the model directly.
    if config.XAI_VOICE_AGENT_ID:
        return f"{config.XAI_REALTIME_URL}?agent_id={config.XAI_VOICE_AGENT_ID}"
    return f"{config.XAI_REALTIME_URL}?model={config.XAI_REALTIME_MODEL}"


async def run_call(twilio, xai, state: CallState, primed: bool = False) -> None:
    """Pump events both ways until the call ends.

    `twilio` and `xai` are adapters exposing `async send(dict)`, async
    iteration yielding dicts, and `stream_sid` on the Twilio side. Closing the
    Twilio websocket is what ends the phone call (nothing follows <Connect>
    in the TwiML), so this returns when the call should hang up.

    `primed` means the webhook already configured the session and requested
    the greeting while Twilio was still setting up its media stream.
    """
    if not primed:
        await xai.send(session_update(state))
        await xai.send({"type": "response.create"})  # model speaks first, both directions

    done = asyncio.Event()

    async def pump_twilio():
        try:
            async for msg in twilio:
                ev = msg.get("event")
                if ev == "media":
                    await xai.send(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": msg["media"]["payload"],
                        }
                    )
                elif ev == "mark" and msg.get("mark", {}).get("name") == MARK_GOODBYE:
                    log.info("call %s: goodbye played, hanging up", state.call_sid)
                    break
                elif ev == "stop":
                    log.info("call %s: caller hung up", state.call_sid)
                    break
        finally:
            done.set()

    async def pump_xai():
        try:
            async for ev in xai:
                t = ev.get("type")
                if t == "response.output_audio.delta":
                    # mu-law 8k base64 on both sides: pass straight through.
                    await twilio.send(
                        {
                            "event": "media",
                            "streamSid": twilio.stream_sid,
                            "media": {"payload": ev["delta"]},
                        }
                    )
                elif t == "input_audio_buffer.speech_started":
                    # Barge-in: drop any queued agent audio so it shuts up.
                    await twilio.send(
                        {"event": "clear", "streamSid": twilio.stream_sid}
                    )
                elif t == "response.function_call_arguments.done":
                    await _handle_tool(xai, state, ev)
                elif t == "response.done" and state.ended:
                    # Ask Twilio to tell us when the farewell finished playing.
                    await twilio.send(
                        {
                            "event": "mark",
                            "streamSid": twilio.stream_sid,
                            "mark": {"name": MARK_GOODBYE},
                        }
                    )
                    asyncio.get_running_loop().call_later(HANGUP_GRACE_S, done.set)
                elif t == "response.output_audio_transcript.done":
                    log.info("call %s agent: %r", state.call_sid, ev.get("transcript"))
                elif t == "conversation.item.input_audio_transcription.updated":
                    log.debug(
                        "call %s caller: %r", state.call_sid, ev.get("transcript")
                    )
                elif t == "error":
                    log.warning("call %s xai error: %s", state.call_sid, ev)
        finally:
            done.set()

    tasks = [asyncio.create_task(pump_twilio()), asyncio.create_task(pump_xai())]
    try:
        await done.wait()
    finally:
        for task in tasks:
            task.cancel()


async def _handle_tool(xai, state: CallState, ev: dict) -> None:
    try:
        args = json.loads(ev.get("arguments") or "{}")
    except ValueError:
        log.warning("call %s: unparseable tool args %r", state.call_sid, ev)
        args = {}
    name = ev.get("name", "")
    # Tool bodies do blocking I/O (Twilio REST, csv): keep the loop free.
    result = await asyncio.to_thread(_run_tool, state, name, args)
    log.info("call %s tool %s(%s) -> %s", state.call_sid, name, args, result)
    await xai.send(
        {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": ev.get("call_id", ""),
                "output": result,
            },
        }
    )
    # Let it speak the confirmation that follows the tool — except after
    # end_call, where the goodbye was spoken in the same turn and another
    # response would double it (the mark-then-hangup path is already armed).
    if not state.ended:
        await xai.send({"type": "response.create"})
