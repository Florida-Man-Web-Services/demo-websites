"""Sesame spike transcode: 24 kHz PCM16 WAV → μ-law 8 kHz. No DeepInfra."""
from __future__ import annotations

import io
import math
import struct
import sys
import wave
from pathlib import Path

SPIKE = Path(__file__).resolve().parent.parent / "spikes" / "001-sesame-mulaw"
sys.path.insert(0, str(SPIKE))
from transcode import wav24_to_pcmu8  # noqa: E402


def _sine_wav(rate=24000, seconds=1.0, freq=440.0) -> bytes:
    n = int(rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = b"".join(
            struct.pack(
                "<h",
                int(16000 * math.sin(2 * math.pi * freq * i / rate)),
            )
            for i in range(n)
        )
        wf.writeframes(frames)
    return buf.getvalue()


def test_wav24_to_pcmu8_length():
    wav = _sine_wav(rate=24000, seconds=1.0)
    pcmu = wav24_to_pcmu8(wav)
    assert len(pcmu) == 8000
