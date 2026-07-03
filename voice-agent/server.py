"""FastAPI server wiring Twilio <-> Claude <-> Sesame CSM.

Flow per call:
  Twilio answers/places the call -> hits /voice/inbound or /voice/outbound ->
  we speak a Sesame-generated greeting and open a <Gather input="speech"> ->
  Twilio transcribes the caller and POSTs /voice/turn -> Claude replies (and
  may text the demo link / log the outcome / hang up) -> repeat.

Run:  uvicorn server:app --port 8035
Then point your Twilio number's Voice webhook at {PUBLIC_BASE_URL}/voice/inbound.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Form, Response
from fastapi.responses import FileResponse, PlainTextResponse
from twilio.twiml.voice_response import Gather, VoiceResponse

import config
import tts
from agent import CallState, run_turn
from businesses import Business, by_phone, by_slug

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("voice-agent.server")

app = FastAPI(title="demo-websites voice agent")

# Active call state, keyed by Twilio CallSid. In-memory is fine for a
# single-process, one-call-at-a-time operation; swap for redis if scaling.
CALLS: dict[str, CallState] = {}

UNKNOWN_BUSINESS = Business(
    name="your business", category="", demo_url=config.DEMO_BASE_URL + "/../index.html"
)


_TTS_POOL = ThreadPoolExecutor(max_workers=4)


def _speak(vr_or_gather, text: str) -> None:
    """Attach spoken audio: Sesame TTS per sentence, Twilio <Say> as a
    per-sentence fallback.

    Sentences are synthesized in parallel and played back-to-back, so wall
    time is the slowest sentence rather than the sum. A failure on one
    sentence falls back to <Say> for THAT sentence only — the rest still
    play in the Sesame voice.
    """
    sentences = tts.split_sentences(text)
    futures = [(s, _TTS_POOL.submit(tts.synthesize, s)) for s in sentences]
    for sentence, fut in futures:
        try:
            key = fut.result()
            vr_or_gather.play(f"{config.PUBLIC_BASE_URL}/audio/{key}.wav")
        except Exception as e:
            log.warning("TTS failed for %r (%s); <Say> fallback", sentence, e)
            vr_or_gather.say(sentence)


def _twiml_turn(state: CallState, text: str) -> Response:
    """Speak `text`, then either hang up or listen for the next utterance."""
    vr = VoiceResponse()
    if state.ended:
        _speak(vr, text)
        vr.hangup()
        CALLS.pop(state.call_sid, None)
    else:
        gather = Gather(
            input="speech",
            action=f"{config.PUBLIC_BASE_URL}/voice/turn",
            method="POST",
            speech_timeout="auto",
            speech_model="experimental_conversations",
        )
        _speak(gather, text)  # nested so the caller can barge in
        vr.append(gather)
        # No speech detected -> loop back for a gentle check-in.
        vr.redirect(f"{config.PUBLIC_BASE_URL}/voice/turn", method="POST")
    return Response(content=str(vr), media_type="application/xml")


@app.post("/voice/inbound")
def voice_inbound(CallSid: str = Form(...), From: str = Form("")):
    business = by_phone(From) or UNKNOWN_BUSINESS
    state = CallState(
        call_sid=CallSid, business=business, direction="inbound", caller_number=From
    )
    CALLS[CallSid] = state
    log.info("inbound call %s from %s -> matched %s", CallSid, From, business.name)
    return _twiml_turn(state, run_turn(state, None))


@app.post("/voice/outbound")
def voice_outbound(slug: str, CallSid: str = Form(...), To: str = Form("")):
    business = by_slug(slug)
    if business is None:
        vr = VoiceResponse()
        vr.hangup()
        log.error("outbound call %s: unknown slug %s", CallSid, slug)
        return Response(content=str(vr), media_type="application/xml")
    state = CallState(
        call_sid=CallSid, business=business, direction="outbound", caller_number=To
    )
    CALLS[CallSid] = state
    log.info("outbound call %s to %s (%s)", CallSid, To, business.name)
    return _twiml_turn(state, run_turn(state, None))


@app.post("/voice/turn")
def voice_turn(CallSid: str = Form(...), SpeechResult: str = Form("")):
    state = CALLS.get(CallSid)
    if state is None:
        vr = VoiceResponse()
        vr.say("Sorry, something went wrong on my end. Goodbye.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")
    log.info("call %s heard: %r", CallSid, SpeechResult)
    return _twiml_turn(state, run_turn(state, SpeechResult or None))


@app.post("/voice/status")
def voice_status(CallSid: str = Form(...), CallStatus: str = Form("")):
    if CallStatus in ("completed", "failed", "busy", "no-answer", "canceled"):
        CALLS.pop(CallSid, None)
        log.info("call %s finished: %s", CallSid, CallStatus)
    return PlainTextResponse("ok")


@app.get("/audio/{key}.wav")
def audio(key: str):
    path = tts.audio_path("".join(c for c in key if c.isalnum()))
    if not path.exists():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(path, media_type="audio/wav")


@app.get("/health")
def health():
    return {"ok": True, "active_calls": len(CALLS)}


config.require(
    "ANTHROPIC_API_KEY",
    "DEEPINFRA_API_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "PUBLIC_BASE_URL",
)
