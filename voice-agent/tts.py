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
import re

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
    # Voice and length cap are part of the output, so key on them too —
    # otherwise changing SESAME_MAX_AUDIO_MS keeps serving stale/truncated WAVs.
    return hashlib.sha256(
        f"{SESAME_VOICE}|{MAX_AUDIO_MS}|{text}".encode()
    ).hexdigest()[:24]


# Abbreviations that end in a period but do NOT end a sentence. Used to avoid
# splitting "123 Main St. Gainesville" into a choppy "St." fragment.
_ABBREV = {
    "st", "ave", "rd", "blvd", "ln", "ct", "pl", "hwy", "apt", "ste", "no",
    "mr", "mrs", "ms", "dr", "jr", "sr", "vs", "etc", "inc", "ltd", "co",
    "dept", "gen", "fig", "approx", "min",
}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    """Split into sentences for per-sentence TTS, without chopping on
    abbreviations or initials. Shared by the streaming and TwiML paths."""
    out: list[str] = []
    for piece in _SENTENCE_SPLIT.split(text.strip()):
        if not piece:
            continue
        if out and out[-1].endswith("."):
            last = out[-1].rsplit(None, 1)[-1].rstrip(".").lower()
            if last in _ABBREV or len(last) == 1:  # "St." or an initial "J."
                out[-1] = f"{out[-1]} {piece}"
                continue
        out.append(piece)
    return out or [text]


def _decode_audio(payload) -> bytes:
    """DeepInfra returns audio as a data URI or bare base64 string."""
    if isinstance(payload, str):
        if payload.startswith("data:"):
            _, _, payload = payload.partition(",")  # tolerate a malformed URI
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


def prewarm_phrases(phrases) -> None:
    """Synthesize a list of stock phrases into the disk cache (idempotent).

    Run at startup so the agent's fixed openers play with zero synthesis
    delay on every call.
    """
    for phrase in phrases:
        try:
            synthesize(phrase)
        except Exception as e:
            log.warning("prewarm failed for %r: %s", phrase, e)


def warm() -> None:
    """Nudge DeepInfra so the model stays loaded on a GPU.

    Idle models get unloaded, and reloading costs a 60-90s cold start on the
    next real request. Calling this every few minutes (cost: ~$0.00001) keeps
    first-reply latency in the warm 3-5s range. Bypasses the disk cache.
    """
    httpx.post(
        config.SESAME_TTS_URL,
        headers={"Authorization": f"bearer {config.DEEPINFRA_API_KEY}"},
        json={"text": "okay", "preset_voice": SESAME_VOICE, "max_audio_length_ms": 2000},
        timeout=120,
    )
