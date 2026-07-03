"""Text-to-speech via Sesame CSM-1B.

Default backend is DeepInfra's hosted csm-1b endpoint (the Apache-2.0 model
Sesame open-sourced), which returns whole utterances as WAV. Generated audio
is cached on disk keyed by a hash of the text, so repeated lines (greetings,
objection responses) cost nothing after the first call.
"""

import base64
import hashlib
import logging
import os

import httpx

import config

log = logging.getLogger("voice-agent.tts")

config.AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# CSM-1B without conditioning ("none") produces a random voice per utterance.
# Pin a preset so the agent sounds like one consistent person. DeepInfra
# options: conversational_a, conversational_b, read_speech_a..d, none.
SESAME_VOICE = os.getenv("SESAME_VOICE", "conversational_a")
# Default cap is 10s, which truncates longer replies mid-sentence.
MAX_AUDIO_MS = int(os.getenv("SESAME_MAX_AUDIO_MS", "30000"))


def _cache_key(text: str) -> str:
    return hashlib.sha256(f"{SESAME_VOICE}|{text}".encode()).hexdigest()[:24]


def _decode_audio(payload) -> bytes:
    """DeepInfra returns audio as a data URI or bare base64 string."""
    if isinstance(payload, str):
        if payload.startswith("data:"):
            payload = payload.split(",", 1)[1]
        return base64.b64decode(payload)
    raise ValueError(f"Unexpected audio payload type: {type(payload)}")


def synthesize(text: str) -> str:
    """Generate speech for `text`, returning the cache key of the WAV file.

    The server exposes the file at /audio/{key}.wav for Twilio's <Play>.
    """
    key = _cache_key(text)
    path = config.AUDIO_CACHE_DIR / f"{key}.wav"
    if path.exists():
        return key

    resp = httpx.post(
        config.SESAME_TTS_URL,
        headers={"Authorization": f"bearer {config.DEEPINFRA_API_KEY}"},
        json={
            "text": text,
            "preset_voice": SESAME_VOICE,
            "max_audio_length_ms": MAX_AUDIO_MS,
        },
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    audio = body.get("audio")
    if audio is None:
        raise RuntimeError(f"TTS response had no audio field: {list(body)}")

    path.write_bytes(_decode_audio(audio))
    log.info("synthesized %d chars -> %s.wav", len(text), key)
    return key


def audio_path(key: str):
    return config.AUDIO_CACHE_DIR / f"{key}.wav"


def warm() -> None:
    """Nudge DeepInfra so the model stays loaded on a GPU.

    Idle models get unloaded, and reloading costs a 60-90s cold start on the
    next real request. Calling this every few minutes (cost: ~$0.00001) keeps
    first-reply latency in the warm 3-5s range. Bypasses the disk cache.
    """
    httpx.post(
        config.SESAME_TTS_URL,
        headers={"Authorization": f"bearer {config.DEEPINFRA_API_KEY}"},
        json={"text": ".", "preset_voice": SESAME_VOICE, "max_audio_length_ms": 1000},
        timeout=120,
    )
