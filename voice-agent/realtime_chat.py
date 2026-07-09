"""Text-mode smoke test for the xAI realtime session — no Twilio, no audio.

Types text in, prints the agent's spoken-transcript out. Verifies the API
key, agent/model selection, per-call instructions, and tool round-trips.
Like chat.py, SMS sending is stubbed — no real texts, but log_call_outcome
does append to the call log with call_sid REALTIME-CHAT.

Usage:
    python realtime_chat.py [business-slug]
"""

import asyncio
import json
import logging
import sys

import websockets

import agent
import config
import realtime
from agent import CallState

# Never send a real SMS from the simulator.
agent._send_sms = lambda state, to: (
    print(f"\n  [SIM] would text demo link to {to or state.caller_number or 'caller'}\n"),
    "SMS with the demo link sent.",
)[1]
from businesses import all_businesses, by_slug

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
log = logging.getLogger("voice-agent.realtime-chat")


async def main() -> None:
    config.require("XAI_API_KEY")
    business = by_slug(sys.argv[1]) if len(sys.argv) > 1 else all_businesses()[0]
    if business is None:
        raise SystemExit(f"Unknown business slug {sys.argv[1]!r} (see chat.py --list-slugs)")
    state = CallState(
        call_sid="REALTIME-CHAT", business=business, direction="outbound",
        caller_number="+15550000000",
    )
    print(f"— realtime chat as a call to {business.name} — Ctrl-C to quit —")

    async with websockets.connect(
        realtime.xai_url(),
        additional_headers={"Authorization": f"Bearer {config.XAI_API_KEY}"},
    ) as ws:
        update = realtime.session_update(state)
        # Text mode: audio formats aren't needed, transcripts are the output.
        del update["session"]["audio"]
        update["session"]["turn_detection"] = None
        await ws.send(json.dumps(update))
        await ws.send(json.dumps({"type": "response.create"}))

        async def pump():
            async for raw in ws:
                ev = json.loads(raw)
                t = ev.get("type")
                if t == "response.output_audio_transcript.delta":
                    print(ev.get("delta", ""), end="", flush=True)
                elif t == "response.output_audio_transcript.done":
                    print()
                elif t == "response.function_call_arguments.done":
                    await realtime._handle_tool(
                        realtime_ws_adapter(ws), state, ev
                    )
                    if state.ended:
                        print("— agent ended the call —")
                        return
                elif t == "error":
                    log.warning("xai error: %s", ev)

        def realtime_ws_adapter(raw_ws):
            class _A:
                async def send(self, msg: dict):
                    await raw_ws.send(json.dumps(msg))
            return _A()

        pump_task = asyncio.create_task(pump())
        loop = asyncio.get_running_loop()
        while not pump_task.done():
            try:
                text = await loop.run_in_executor(None, input, "you> ")
            except (EOFError, KeyboardInterrupt):
                break
            if not text.strip():
                continue
            await ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": text}]},
            }))
            await ws.send(json.dumps({"type": "response.create"}))
        pump_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
