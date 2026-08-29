"""24 kHz WAV PCM16 → G.711 μ-law 8 kHz. Not wired into server.py.

Measured 2026-08-29 DeepInfra sesame/csm-1b: REST ~5.9 s, stream TTFB ~1.89 s,
WAV 24 kHz — too slow and wrong codec for Twilio Media Streams. Do not set
VOICE_BACKEND to Sesame.
"""
from __future__ import annotations

import io
import struct
import wave

_BIAS = 0x84
_CLIP = 32635


def _lin2ulaw(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0x00
    if sample < 0:
        sample = -sample
    if sample > _CLIP:
        sample = _CLIP
    sample = sample + _BIAS
    exp = 7
    mask = 0x4000
    while exp > 0 and not (sample & mask):
        exp -= 1
        mask >>= 1
    mantissa = (sample >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mantissa)) & 0xFF


def wav24_to_pcmu8(wav_bytes: bytes) -> bytes:
    """Decode 16-bit mono WAV at any rate to μ-law 8 kHz bytes."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    if sw != 2:
        raise ValueError(f"need 16-bit PCM, got sample width {sw}")
    if nch < 1:
        raise ValueError("empty wav")
    # Interleaved int16 → mono left channel
    n = nframes
    samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    if nch > 1:
        samples = samples[0::nch]
        n = len(samples)
    if rate == 8000:
        picked = samples
    else:
        # Nearest-neighbor downsample (deterministic length = round(n * 8000 / rate))
        out_n = int(round(n * 8000 / rate))
        picked = tuple(samples[min(n - 1, int(i * rate / 8000))] for i in range(out_n))
    return bytes(_lin2ulaw(s) for s in picked)
