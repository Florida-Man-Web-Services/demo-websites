"""FastAPI server wiring Twilio <-> the voice agent.

Flow per call (VOICE_BACKEND=pipeline, default):
  Twilio answers/places the call -> hits /voice/inbound or /voice/outbound ->
  we speak a Sesame-generated greeting and open a <Gather input="speech"> ->
  Twilio transcribes the caller and POSTs /voice/turn -> the LLM replies (and
  may text the demo link / log the outcome / hang up) -> repeat.

Flow per call (VOICE_BACKEND=grok-realtime):
  /voice/inbound|outbound return <Connect><Stream> TwiML -> Twilio pumps raw
  call audio over the /voice/stream websocket -> realtime.py bridges it to
  xAI's speech-to-speech API (same tools, same per-call prompt).

Run:  uvicorn server:app --port 8035
Then point your Twilio number's Voice webhook at {PUBLIC_BASE_URL}/voice/inbound
and Messaging webhook at {PUBLIC_BASE_URL}/sms/inbound.
"""

import asyncio
import os
import time
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import Connect, Gather, VoiceResponse

import config
import realtime
import tts
from agent import CallState, flush_call_transcript, run_turn
from businesses import Business, by_phone, by_slug

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("voice-agent.server")

app = FastAPI(title="demo-websites voice agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Active call state, keyed by Twilio CallSid. In-memory is fine for a
# single-process, one-call-at-a-time operation; swap for redis if scaling.
CALLS: dict[str, CallState] = {}

# Inbound SMS multi-turn threads, keyed by From E.164. Values: (CallState, last_ts).
SMS_SESSIONS: dict[str, tuple[CallState, float]] = {}


def _make_state(
    call_sid: str,
    business: Business,
    direction: str,
    number: str,
    *,
    outbound_slug: str | None = None,
) -> CallState:
    """Build CallState with per-phone mode routing (AGENT_MODE=auto)."""
    from agent import resolve_call_mode

    mode, customer = resolve_call_mode(
        number, direction=direction, outbound_slug=outbound_slug
    )
    # Paid owners always get owner_updates tools even if business match is weak.
    if customer.get("demo_url") and (
        not business.demo_url or business.name == "your business"
    ):
        from businesses import Business as Biz

        business = Biz(
            name=customer.get("business_name") or business.name,
            category=customer.get("category") or business.category,
            phone=customer.get("phone") or number,
            demo_url=customer.get("demo_url") or business.demo_url,
            slug=customer.get("slug") or business.slug,
        )
    state = CallState(
        call_sid=call_sid,
        business=business,
        direction=direction,
        caller_number=number,
        mode=mode,
        customer=customer or {},
    )
    try:
        import voice_auth

        voice_auth.apply_auth_to_state(state)
    except Exception as e:  # noqa: BLE001
        log.warning("voice_auth init failed: %s", e)
    log.info(
        "call %s mode=%s auth=%s customer=%s business=%s",
        call_sid,
        mode,
        getattr(state, "auth_level", "?"),
        (customer or {}).get("id") or "-",
        business.name,
    )
    return state


UNKNOWN_BUSINESS = Business(
    name="your business", category="", demo_url=config.DEMO_BASE_URL + "/../index.html"
)


_TTS_POOL = ThreadPoolExecutor(max_workers=4)

# Pre-opened xAI realtime sessions, keyed by CallSid. The TLS + session
# handshake to api.x.ai costs ~2-3s; starting it at webhook time overlaps it
# with Twilio's own media-stream setup instead of adding it on top, and the
# greeting requested at prime time buffers in the socket so the caller hears
# the voice almost as soon as the stream opens.
PRIMED_XAI: dict = {}  # CallSid -> concurrent.futures.Future[websocket]
PRIME_TTL_S = 30  # closed unclaimed after this (call never reached /voice/stream)
_LOOP: "asyncio.AbstractEventLoop | None" = None

# On top of priming, keep ONE spare pre-opened xAI connection at all times:
# the TLS + websocket handshake to api.x.ai costs ~2s, so a call that claims
# the spare skips it entirely and only pays greeting generation (~1s), which
# Twilio's own stream setup fully hides. xAI emits a ping event every 10s and
# times sessions out after 15min idle, so a parked spare must be actively
# read: the watcher drains events (unread pings trip flow control and get the
# transport killed in ~3min) and reopens the spare when the server closes it.
# Because a dead socket can still look open for tens of seconds, claims are
# additionally verified with a round-trip in _prime_xai before being trusted.
_SPARE_XAI: "asyncio.Task | None" = None
_SPARE_WATCHER: "asyncio.Task | None" = None


async def _open_xai():
    import websockets

    ws = await websockets.connect(
        realtime.xai_url(),
        additional_headers={"Authorization": f"Bearer {config.XAI_API_KEY}"},
    )
    # Eat the session.created greeting: the bridge doesn't need it, and an
    # empty receive buffer is what lets a claim-time recv() prove liveness.
    await asyncio.wait_for(ws.recv(), timeout=10)
    return ws


def _restock_spare() -> None:
    global _SPARE_XAI, _SPARE_WATCHER
    task = asyncio.ensure_future(_open_xai())
    _SPARE_XAI = task

    async def watch():
        try:
            ws = await task
            while True:
                await ws.recv()  # drain pings; raises when the server closes
        except asyncio.CancelledError:
            return  # claimed: the call owns the socket now, don't touch it
        except Exception:
            pass
        await asyncio.sleep(1)  # backoff so a flapping network can't spin us
        if _SPARE_XAI is task:  # died while still parked -> replace it
            _restock_spare()

    _SPARE_WATCHER = asyncio.ensure_future(watch())


async def _take_xai():
    """The spare connection if it's alive (fresh connect otherwise), and
    restock for the next call either way."""
    global _SPARE_XAI, _SPARE_WATCHER
    spare, _SPARE_XAI = _SPARE_XAI, None
    watcher, _SPARE_WATCHER = _SPARE_WATCHER, None
    if watcher is not None:
        # Stop draining before the call starts reading — and WAIT for the
        # drain's recv() to unwind, or the claim's first recv() lands while
        # it's still registered and dies with a concurrency error.
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
    _restock_spare()
    if spare is not None:
        try:
            ws = await spare  # usually already done; else it's ahead of a fresh dial
            if ws.close_code is None:
                return ws
        except Exception:
            pass
    return await _open_xai()


@app.on_event("startup")
async def _capture_loop():
    global _LOOP
    _LOOP = asyncio.get_running_loop()
    if config.VOICE_BACKEND == "grok-realtime":
        _restock_spare()


def _prime_xai(call_sid: str, business: Business, direction: str, number: str, slug: str = "") -> None:
    """Kick off the xAI session from the (sync, threadpool) webhook handler."""
    if _LOOP is None or not call_sid:
        return

    async def connect():
        state = _make_state(
            call_sid, business, direction, number,
            outbound_slug=slug or None,
        )
        ws = await _take_xai()
        for retry in (False, True):
            try:
                await ws.send(json.dumps(realtime.session_update(state)))
                await ws.send(json.dumps(realtime.opening_response_create(state)))
                # A silently-dead socket accepts sends; only a live server can
                # reply (session.updated / response.created — the bridge needs
                # neither). No reply in time = spare died undetected: redial.
                await asyncio.wait_for(ws.recv(), timeout=2)
                return ws
            except BaseException:
                try:
                    await ws.close()
                except Exception:
                    pass
                if retry:
                    raise
                log.warning("call %s: spare xAI socket was dead; redialing", call_sid)
                ws = await _open_xai()

    PRIMED_XAI[call_sid] = asyncio.run_coroutine_threadsafe(connect(), _LOOP)
    _LOOP.call_soon_threadsafe(_LOOP.call_later, PRIME_TTL_S, _expire_primed, call_sid)


def _expire_primed(call_sid: str) -> None:
    fut = PRIMED_XAI.pop(call_sid, None)
    if fut is None:
        return

    def close(f):
        try:
            ws = f.result()
        except BaseException:
            return
        asyncio.run_coroutine_threadsafe(ws.close(), _LOOP)

    fut.add_done_callback(close)
    fut.cancel()


_SIGNATURE_VALIDATOR = RequestValidator(config.TWILIO_AUTH_TOKEN)


async def _twilio_signature(request: Request) -> None:
    """Reject webhook posts that weren't signed by our Twilio account.

    On a stable public domain anyone who finds the URL could otherwise drive
    the agent (LLM turns, SMS sends). Twilio signs every request over the
    exact public URL plus the sorted form params; we reconstruct the URL from
    PUBLIC_BASE_URL because the app sits behind a proxy and never sees the
    original scheme/host.
    """
    if not config.VALIDATE_TWILIO_WEBHOOKS:
        return
    url = config.PUBLIC_BASE_URL + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    form = await request.form()  # cached by Starlette; Form(...) params still work
    signature = request.headers.get("X-Twilio-Signature", "")
    if not _SIGNATURE_VALIDATOR.validate(url, dict(form), signature):
        log.warning("rejected unsigned webhook post to %s", request.url.path)
        raise HTTPException(status_code=403, detail="invalid Twilio signature")


_SIGNED = [Depends(_twilio_signature)]


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
        try:
            flush_call_transcript(state, backend="pipeline")
        except Exception:
            log.exception("call %s: transcript flush failed", state.call_sid)
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


def _twiml_stream(direction: str, number: str, slug: str = "") -> Response:
    """TwiML for the grok-realtime backend: hand the call's raw audio to our
    /voice/stream websocket. Nothing follows <Connect>, so when the bridge
    closes the socket, Twilio ends the call."""
    vr = VoiceResponse()
    connect = Connect()
    ws_url = config.PUBLIC_BASE_URL.replace("https://", "wss://", 1) + "/voice/stream"
    stream = connect.stream(url=ws_url)
    stream.parameter(name="direction", value=direction)
    stream.parameter(name="number", value=number)
    if slug:
        stream.parameter(name="slug", value=slug)
    vr.append(connect)
    return Response(content=str(vr), media_type="application/xml")


@app.post("/voice/inbound", dependencies=_SIGNED)
def voice_inbound(CallSid: str = Form(...), From: str = Form("")):
    business = by_phone(From) or UNKNOWN_BUSINESS
    log.info("inbound call %s from %s -> matched %s", CallSid, From, business.name)
    if config.VOICE_BACKEND == "grok-realtime":
        _prime_xai(CallSid, business, "inbound", From)
        return _twiml_stream("inbound", From)
    state = _make_state(CallSid, business, "inbound", From)
    CALLS[CallSid] = state
    return _twiml_turn(state, run_turn(state, None))


@app.post("/voice/outbound", dependencies=_SIGNED)
def voice_outbound(slug: str, CallSid: str = Form(...), To: str = Form("")):
    business = by_slug(slug)
    if business is None:
        vr = VoiceResponse()
        vr.hangup()
        log.error("outbound call %s: unknown slug %s", CallSid, slug)
        return Response(content=str(vr), media_type="application/xml")
    log.info("outbound call %s to %s (%s)", CallSid, To, business.name)
    if config.VOICE_BACKEND == "grok-realtime":
        _prime_xai(CallSid, business, "outbound", To, slug=slug)
        return _twiml_stream("outbound", To, slug=slug)
    state = _make_state(CallSid, business, "outbound", To, outbound_slug=slug)
    CALLS[CallSid] = state
    return _twiml_turn(state, run_turn(state, None))


class _TwilioSocket:
    """Adapter: FastAPI websocket -> dict-in/dict-out with stream_sid."""

    def __init__(self, ws: WebSocket, stream_sid: str):
        self._ws = ws
        self.stream_sid = stream_sid

    async def send(self, msg: dict) -> None:
        await self._ws.send_text(json.dumps(msg))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return json.loads(await self._ws.receive_text())
        except Exception:  # disconnect mid-call
            raise StopAsyncIteration


class _XaiSocket:
    """Adapter: websockets client connection -> dict-in/dict-out."""

    def __init__(self, ws):
        self._ws = ws

    async def send(self, msg: dict) -> None:
        await self._ws.send(json.dumps(msg))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return json.loads(await self._ws.recv())
        except Exception:
            raise StopAsyncIteration


@app.websocket("/voice/stream")
async def voice_stream(ws: WebSocket):
    """Bidirectional Twilio Media Stream endpoint (grok-realtime backend)."""
    await ws.accept()
    # Twilio opens with "connected" then "start" (params + streamSid).
    start = None
    async for raw in ws.iter_text():
        msg = json.loads(raw)
        if msg.get("event") == "start":
            start = msg["start"]
            break
    if start is None:
        await ws.close()
        return

    params = start.get("customParameters", {})
    direction = params.get("direction", "inbound")
    number = params.get("number", "")
    business = (
        by_slug(params["slug"]) if params.get("slug") else by_phone(number)
    ) or UNKNOWN_BUSINESS
    state = _make_state(
        start.get("callSid", ""),
        business,
        direction,
        number,
        outbound_slug=params.get("slug"),
    )
    CALLS[state.call_sid] = state
    log.info(
        "realtime %s call %s <-> %s (%s)",
        direction, state.call_sid, business.name, config.GROK_VOICE or "default voice",
    )

    # Prefer the session primed at webhook time (greeting already buffering);
    # fall back to a fresh connect if it's missing or its handshake failed.
    xai_ws, primed = None, False
    primed_fut = PRIMED_XAI.pop(state.call_sid, None)
    if primed_fut is not None:
        try:
            xai_ws = await asyncio.wrap_future(primed_fut)
            primed = True
        except Exception:
            log.warning(
                "call %s: primed xAI session failed; connecting fresh", state.call_sid
            )
    try:
        if xai_ws is None:
            xai_ws = await _take_xai()
        await realtime.run_call(
            _TwilioSocket(ws, start.get("streamSid", "")),
            _XaiSocket(xai_ws),
            state,
            primed=primed,
        )
    except Exception:
        log.exception("realtime bridge for call %s failed", state.call_sid)
    finally:
        if not state.transcript_flushed:
            try:
                await asyncio.to_thread(flush_call_transcript, state, backend="grok-realtime")
            except Exception:
                log.exception("call %s: transcript flush failed", state.call_sid)
        CALLS.pop(state.call_sid, None)
        if xai_ws is not None:
            try:
                await xai_ws.close()
            except Exception:
                pass
        try:
            await ws.close()  # closing the stream is what hangs up the call
        except Exception:
            pass


@app.post("/voice/turn", dependencies=_SIGNED)
def voice_turn(CallSid: str = Form(...), SpeechResult: str = Form("")):
    state = CALLS.get(CallSid)
    if state is None:
        vr = VoiceResponse()
        vr.say("Sorry, something went wrong on my end. Goodbye.")
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")
    log.info("call %s heard: %r", CallSid, SpeechResult)
    return _twiml_turn(state, run_turn(state, SpeechResult or None))


@app.post("/voice/status", dependencies=_SIGNED)
def voice_status(CallSid: str = Form(...), CallStatus: str = Form("")):
    if CallStatus in ("completed", "failed", "busy", "no-answer", "canceled"):
        state = CALLS.pop(CallSid, None)
        if state is not None and not state.transcript_flushed:
            try:
                flush_call_transcript(state)
            except Exception:
                log.exception("call %s: transcript flush on status failed", CallSid)
        log.info("call %s finished: %s", CallSid, CallStatus)
    return PlainTextResponse("ok")


@app.get("/audio/{key}.wav")
def audio(key: str):
    path = tts.audio_path("".join(c for c in key if c.isalnum()))
    if not path.exists():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(path, media_type="audio/wav")



def _sms_trim(body: str, limit: int = 1500) -> str:
    body = (body or "").strip()
    if len(body) <= limit:
        return body
    return body[: limit - 1].rstrip() + "…"


def _get_sms_session(from_number: str, message_sid: str) -> CallState:
    """Resume or start an SMS conversation for this phone number."""
    now = time.time()
    ttl = float(getattr(config, "SMS_SESSION_TTL_S", 3600) or 3600)
    # Drop expired sessions
    expired = [k for k, (_, ts) in SMS_SESSIONS.items() if now - ts > ttl]
    for k in expired:
        st, _ = SMS_SESSIONS.pop(k)
        if not st.transcript_flushed:
            try:
                flush_call_transcript(st, backend="sms")
            except Exception:
                log.exception("sms %s: transcript flush on expire failed", k)

    hit = SMS_SESSIONS.get(from_number)
    if hit is not None:
        state, _ = hit
        SMS_SESSIONS[from_number] = (state, now)
        return state

    business = by_phone(from_number) or UNKNOWN_BUSINESS
    state = _make_state(
        message_sid or f"SMS-{from_number}-{int(now)}",
        business,
        "sms",
        from_number,
    )
    SMS_SESSIONS[from_number] = (state, now)
    log.info(
        "sms session start %s from %s -> %s",
        state.call_sid,
        from_number,
        business.name,
    )
    return state


@app.post("/sms/inbound", dependencies=_SIGNED)
def sms_inbound(
    From: str = Form(""),
    Body: str = Form(""),
    MessageSid: str = Form(""),
):
    """Twilio Messaging webhook — agent replies over SMS (same brain as voice).

    Console: number → Messaging → "A message comes in" →
    {PUBLIC_BASE_URL}/sms/inbound (HTTP POST).
    """
    from_number = (From or "").strip()
    body = (Body or "").strip()
    if not from_number:
        vr = MessagingResponse()
        vr.message("Sorry — I could not read your number. Please try calling instead.")
        return Response(content=str(vr), media_type="application/xml")

    state = _get_sms_session(from_number, MessageSid or "")
    log.info("sms %s from %s: %r", state.call_sid, from_number, body[:200])

    user_text = body if body else "<empty text message>"
    try:
        reply = run_turn(state, user_text)
    except Exception:
        log.exception("sms %s: run_turn failed", state.call_sid)
        reply = (
            "Sorry, something went wrong on my end. "
            f"You can call {config.OWNER_CALLBACK_NUMBER} and ask for {config.OWNER_NAME}."
        )

    reply = _sms_trim(reply)
    if not reply:
        reply = "Got it — thanks!"

    # Persist transcript + clear session when the agent ends the thread.
    if state.ended:
        try:
            flush_call_transcript(state, backend="sms")
        except Exception:
            log.exception("sms %s: transcript flush failed", state.call_sid)
        SMS_SESSIONS.pop(from_number, None)
    else:
        SMS_SESSIONS[from_number] = (state, time.time())

    vr = MessagingResponse()
    vr.message(reply)
    return Response(content=str(vr), media_type="application/xml")



from pydantic import BaseModel, Field


class OnboardRegisterIn(BaseModel):
    phone: str
    business_name: str = ""
    contact_name: str = ""
    email: str = ""
    source: str = "ai411_web"


def _customers_mod():
    """Import mcp-server/customers with monorepo path layout (image + checkout)."""
    import sys
    from pathlib import Path

    mcp = Path(__file__).resolve().parent.parent / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    try:
        import customers
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"customers registry unavailable: {e}",
        ) from e
    return customers


@app.post("/api/onboarding/register")
def api_onboard_register(body: OnboardRegisterIn):
    """Public: queue a phone for onboarding callback (AI 411 landing form)."""
    customers = _customers_mod()

    result = customers.register_callback(
        body.phone,
        business_name=body.business_name,
        contact_name=body.contact_name,
        email=body.email,
        source=body.source or "ai411_web",
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "bad request")
    return {
        "ok": True,
        "message": "You are on the list — we will call shortly to design your free demo site.",
        "customer": result.get("customer"),
        "voice_number": getattr(config, "PUBLIC_VOICE_NUMBER", "") or config.TWILIO_PHONE_NUMBER,
    }


@app.get("/api/onboarding/customers")
def api_onboard_list(status: str = "", limit: int = 100):
    """Internal desk: list customers (protect at the edge in prod)."""
    customers = _customers_mod()
    return customers.list_customers(status=status or None, limit=limit)


class StripeMarkPaidIn(BaseModel):
    phone: str
    stripe_customer_id: str = ""



class NotifyUpdateIn(BaseModel):
    phone: str
    demo_url: str = ""
    message: str = ""


@app.post("/api/sms/notify-updated")
def api_notify_updated(body: NotifyUpdateIn):
    """Send an SMS notification that the website has been updated."""
    from agent import _twilio

    to = (body.phone or "").strip()
    if not to:
        raise HTTPException(status_code=400, detail="phone required")
    url = body.demo_url.strip()
    text = (body.message or "").strip()
    if not text:
        text = (
            f"Hi from Florida Man Web Services! Your free demo website has been "
            f"updated and is ready to view: {url or config.DEMO_BASE_URL}. "
            f"Reply or call {config.OWNER_CALLBACK_NUMBER} to take it live."
        )
    try:
        twilio = _twilio()
        msg = twilio.messages.create(
            to=to, from_=config.TWILIO_PHONE_NUMBER, body=text
        )
        log.info("notify-updated SMS sent to %s (%s)", to, msg.sid)
        return {"ok": True, "sid": msg.sid, "to": to}
    except Exception as e:
        log.warning("notify-updated SMS to %s failed: %s", to, e)
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/billing/mark-paid")
def api_mark_paid(body: StripeMarkPaidIn):
    """Mark customer paid → future calls use owner_updates. Wire Stripe webhook here."""
    customers = _customers_mod()

    result = customers.mark_paid(body.phone, stripe_customer_id=body.stripe_customer_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "bad request")
    return result



@app.get("/health")
def health():
    customers_ok = False
    try:
        import sys
        from pathlib import Path

        mcp = Path(__file__).resolve().parent.parent / "mcp-server"
        if str(mcp) not in sys.path:
            sys.path.insert(0, str(mcp))
        import customers  # noqa: F401

        customers_ok = True
    except Exception:
        customers_ok = False
    return {
        "ok": True,
        "active_calls": len(CALLS),
        "active_sms_sessions": len(SMS_SESSIONS),
        "customers_registry": customers_ok,
        "agent_mode": getattr(config, "AGENT_MODE", ""),
    }


_KEYS = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "PUBLIC_BASE_URL"]
if config.VOICE_BACKEND == "grok-realtime":
    _KEYS.append("XAI_API_KEY")
elif config.VOICE_BACKEND == "pipeline":
    _KEYS.append("DEEPINFRA_API_KEY")
    _KEYS.append("XAI_API_KEY" if config.LLM_PROVIDER == "grok" else "ANTHROPIC_API_KEY")
else:
    raise SystemExit(
        f"Unknown VOICE_BACKEND {config.VOICE_BACKEND!r}; use 'pipeline' or 'grok-realtime'."
    )
config.require(*_KEYS)
