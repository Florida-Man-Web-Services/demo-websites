"""TTFA stamps on grok-realtime; μ-law session preserved."""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import Mock

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))


class _Biz:
    name = "AI411"
    category = "directory"
    address = "Gainesville"
    rating = ""
    demo_url = ""
    slug = "ai411"


class FakeWS:
    def __init__(self, events, stream_sid="MS123", hold=False):
        self._events = list(events)
        self.sent = []
        self.stream_sid = stream_sid
        self._hold = hold
        self._closed = asyncio.Event()

    def close(self):
        self._closed.set()

    async def send(self, msg):
        self.sent.append(msg)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        if self._hold:
            await self._closed.wait()
        raise StopAsyncIteration


def _reload():
    os.environ["AGENT_MODE"] = "ai411"
    os.environ.pop("XAI_VOICE_AGENT_ID", None)
    import config
    import agent
    import realtime

    importlib.reload(config)
    importlib.reload(agent)
    importlib.reload(realtime)
    return agent, realtime


def test_session_update_is_pcmu_8k():
    agent, realtime = _reload()
    state = agent.CallState(
        call_sid="CA1",
        business=_Biz(),
        direction="inbound",
        caller_number="+13551110000",
    )
    state.mode = "ai411"
    msg = realtime.session_update(state)
    audio = msg["session"]["audio"]
    assert audio["input"]["format"]["type"] == "audio/pcmu"
    assert audio["input"]["format"]["rate"] == 8000
    assert audio["output"]["format"]["type"] == "audio/pcmu"
    assert audio["output"]["format"]["rate"] == 8000
    assert "agent_id" not in realtime.xai_url()


def test_ttfa_stamps_after_speech_stopped():
    agent, realtime = _reload()
    state = agent.CallState(
        call_sid="CA-ttfa",
        business=_Biz(),
        direction="inbound",
        caller_number="+13551110000",
    )
    state.mode = "ai411"
    state.llm = Mock()
    state.llm.messages = []
    twilio = FakeWS([], hold=True)
    xai = FakeWS(
        [
            {"type": "input_audio_buffer.speech_stopped"},
            {"type": "response.output_audio.delta", "delta": "QQ=="},
        ]
    )
    asyncio.run(asyncio.wait_for(realtime.run_call(twilio, xai, state, primed=True), timeout=2))
    ttfa = getattr(state, "ttfa", None) or {}
    assert ttfa.get("speech_stopped_to_first_delta_ms") is not None
    assert ttfa.get("first_delta_to_twilio_ms") is not None
    media = [m for m in twilio.sent if m.get("event") == "media"]
    assert len(media) == 1
    assert media[0]["media"]["payload"] == "QQ=="
