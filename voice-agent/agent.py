"""The conversation brain: Claude runs the sales call, one spoken turn at a time.

Each call holds a CallState (message history + business context). run_turn()
feeds the caller's transcribed speech to Claude, executes any tool calls
(text the demo link, log the outcome, hang up), and returns the sentence(s)
the agent should speak next.
"""

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime

import anthropic
from twilio.rest import Client as TwilioClient

import config
from businesses import Business
from tts import split_sentences

log = logging.getLogger("voice-agent.agent")

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)

MAX_TURNS = 40  # hard stop so a stuck call can't loop forever

TOOLS = [
    {
        "name": "send_demo_link_sms",
        "description": (
            "Text the business's live demo website link to a phone number. Use when "
            "the person agrees to receive the link by text. Default to the number "
            "they are calling from unless they give a different one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Destination number; omit to use the caller's number.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "log_call_outcome",
        "description": (
            "Record how the call went. Call this once, near the end of every call, "
            "before ending it. Use do_not_call whenever the person asks not to be "
            "contacted again."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": [
                        "interested",
                        "wants_email",
                        "callback_requested",
                        "sent_sms",
                        "not_interested",
                        "do_not_call",
                        "wrong_number",
                        "voicemail",
                        "other",
                    ],
                },
                "email": {
                    "type": "string",
                    "description": "Email address if they gave one.",
                },
                "callback_time": {
                    "type": "string",
                    "description": "When to call back, if they asked for that.",
                },
                "notes": {
                    "type": "string",
                    "description": "One or two sentences: what they said, next step.",
                },
            },
            "required": ["outcome", "notes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "end_call",
        "description": (
            "Hang up after your current reply is spoken. Use once the conversation "
            "has reached a natural end (they said goodbye, asked to stop, or the "
            "next step is settled)."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


# Fixed openers the agent leads every reply with. They're pre-synthesized to
# the disk cache (tts.prewarm_phrases), so the first thing the caller hears
# plays instantly while the rest of the reply is still being generated.
OPENERS = [
    "Hi there!",
    "Sure thing.",
    "Absolutely.",
    "Of course.",
    "Totally fair question.",
    "Good question.",
    "No problem at all.",
    "Totally understand.",
    "Sounds good.",
    "Got it.",
    "Thanks so much.",
    "Sorry about that.",
]


def _spoken_url(demo_url: str) -> str:
    return demo_url.removeprefix("https://")


def system_prompt(business: Business, direction: str, caller_number: str) -> str:
    """Build the per-call system prompt from the campaign's phone script."""
    ctx = f"""You are {config.OWNER_NAME}'s AI phone assistant, selling websites for a one-person
web development business in Gainesville, Florida. {config.OWNER_NAME} builds free demo
websites for local businesses that don't have one, then charges a flat one-time
fee to take the demo live (their own domain name, findable on Google — no
monthly fees). You are on a live phone call; everything you write will be
spoken aloud by a text-to-speech voice.

THE BUSINESS ON THIS CALL
- Name: {business.name}
- Category: {business.category or "unknown"}
- Address: {business.address or "unknown"}
- Google rating: {business.rating or "unknown"}
- Their free demo website (already built and live): {business.demo_url}
- Caller/called number: {caller_number or "unknown"}
- Call direction: {direction}

HOW TO SPEAK
- 1-3 short sentences per turn. Never monologue. Ask one question at a time.
- Open every reply with one of these exact opener sentences (pick whichever
  fits, vary them, punctuation included): {" | ".join(OPENERS)}
  These are pre-recorded so they play instantly and cover the synthesis
  pause — like natural phone rhythm. Only improvise a different opener if
  none of them fits at all.
- Plain conversational English: no bullet points, no markdown, no emoji.
- Spell things for the ear: say the demo address as "{_spoken_url(business.demo_url)}"
  and offer to text it instead of making them write it down.
- You are talking to a busy small-business owner or their staff. Be warm,
  local, and brief. Mirror their energy.
- The speech transcription you receive may contain errors; if something seems
  garbled, confirm rather than guess.

HONESTY RULES (non-negotiable)
- In your FIRST turn, identify yourself as an AI assistant calling on behalf
  of {config.OWNER_NAME}, a local web developer. Never pretend to be human, even if asked
  jokingly. If asked, confirm you are an AI and offer {config.OWNER_NAME}'s direct number:
  {config.OWNER_CALLBACK_NUMBER}.
- Never invent facts about the business, pricing specifics, or deadlines.
  If asked exactly what going live costs, say {config.OWNER_NAME} quotes a flat rate after a
  quick look at what they need, and offer to have {config.OWNER_NAME} follow up.
- If they ask not to be called again, apologize once, call log_call_outcome
  with do_not_call, and end the call.

THE PITCH (adapted from the campaign script)
- Core message: "{config.OWNER_NAME} noticed {business.name} doesn't have a website, so he already
  built one — completely free. It has your name, address, and hours, and looks
  professional on a phone. There's no cost to look."
- The demo is theirs to keep either way. Zero pressure.
- If interested: offer to text the link right now (send_demo_link_sms), and
  say {config.OWNER_NAME} can follow up to take it live whenever they're ready.
- If they want email instead: collect their email address carefully (confirm
  the spelling), log it with log_call_outcome, and say it'll be sent shortly.
- If staff answers (not the owner): ask if the owner is available; if not,
  offer to text the link or note a better time to call back.
- Objection "how much?": the demo is free; going live is a flat one-time fee,
  no monthly charges, exact quote from {config.OWNER_NAME}.
- Objection "not interested": "Totally understand — the demo is yours to keep
  either way, the link will still be there if you change your mind." Then log
  and end the call politely.

TOOLS
- Use send_demo_link_sms the moment they agree to a text.
- Always call log_call_outcome exactly once before the call ends.
- Call end_call together with your final goodbye sentence."""
    if direction == "inbound":
        ctx += """

This is an INBOUND call — they are calling the number from a voicemail,
letter, or missed call. Greet them, identify yourself as an AI assistant for
the local web developer who built their free demo site, and ask how you can
help. If the caller's number matched a business, assume it's probably them
but confirm who you're speaking with."""
    else:
        ctx += """

This is an OUTBOUND call placed on behalf of the owner. Open with the
disclosure and the reason for the call in two short sentences, then pause for
their response. If it is clearly a voicemail greeting, leave one concise
voicemail (who, why, the demo address read slowly, the callback number), call
log_call_outcome with voicemail, then end_call."""
    return ctx


@dataclass
class CallState:
    call_sid: str
    business: Business
    direction: str  # "inbound" | "outbound"
    caller_number: str = ""
    messages: list = field(default_factory=list)
    ended: bool = False
    turns: int = 0


_twilio_client = None


def _twilio() -> TwilioClient:
    global _twilio_client
    if _twilio_client is None:  # build once, reuse the HTTP session
        _twilio_client = TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    return _twilio_client


def _send_sms(state: CallState, to_number: str | None) -> str:
    to = to_number or state.caller_number
    if not to:
        return "Error: no destination number known; ask the caller for their number."
    twilio = _twilio()
    body = (
        f"Hi from {config.OWNER_NAME} (Gainesville web developer) - here's the free demo "
        f"website for {state.business.name}: {state.business.demo_url} "
        f"Reply or call {config.OWNER_CALLBACK_NUMBER} to take it live."
    )
    try:
        msg = twilio.messages.create(
            to=to, from_=config.TWILIO_PHONE_NUMBER, body=body
        )
        log.info("SMS sent to %s (%s)", to, msg.sid)
        return f"SMS with the demo link sent to {to}."
    except Exception as e:  # surface the failure to the model so it can adapt
        log.warning("SMS to %s failed: %s", to, e)
        return f"Error sending SMS: {e}. Offer to read the address out or send email."


def _log_outcome(state: CallState, args: dict) -> str:
    is_new = not config.CALL_LOG.exists()
    with open(config.CALL_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(
                ["timestamp", "call_sid", "direction", "business", "slug",
                 "phone", "outcome", "email", "callback_time", "notes"]
            )
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            state.call_sid,
            state.direction,
            state.business.name,
            state.business.slug,
            state.caller_number,
            args.get("outcome", ""),
            args.get("email", ""),
            args.get("callback_time", ""),
            args.get("notes", ""),
        ])
    return "Outcome logged."


def _run_tool(state: CallState, name: str, args: dict) -> str:
    if name == "send_demo_link_sms":
        return _send_sms(state, args.get("phone"))
    if name == "log_call_outcome":
        return _log_outcome(state, args)
    if name == "end_call":
        state.ended = True
        return "The call will end after your current reply is spoken."
    return f"Unknown tool {name}"


def run_turn(
    state: CallState,
    user_speech: str | None,
    on_sentence=None,
) -> str:
    """One conversational turn. Returns the full reply text.

    If `on_sentence` is given, each completed sentence is ALSO passed to it
    while Claude is still generating — so TTS can start on sentence one before
    the reply is finished. In that case the sentences have already been handed
    off for speaking: use the return value only for display/logging, do NOT
    speak it again (doing so would play the whole turn twice). Callers that
    want to speak the return themselves must NOT pass on_sentence.
    """
    state.turns += 1
    if state.turns > MAX_TURNS:
        state.ended = True
        closing = "Sorry, I have to run — thanks so much for your time. Have a great day!"
        if on_sentence:
            on_sentence(closing)
        return closing

    if user_speech:
        state.messages.append({"role": "user", "content": user_speech})
    elif not state.messages:
        state.messages.append(
            {"role": "user", "content": "<call connected — greet them now>"}
        )
    else:
        state.messages.append(
            {"role": "user", "content": "<silence — the line is quiet; check in briefly>"}
        )

    reply_parts: list[str] = []
    pending = ""  # streamed text not yet emitted as a full sentence

    # System prompt is constant for the whole call — build it once, not on
    # every stream round (a tool turn would otherwise rebuild it 2-3x).
    sys_prompt = system_prompt(state.business, state.direction, state.caller_number)

    def emit(final: bool = False):
        """Hand completed sentences to on_sentence as they finish streaming.
        Keeps the last (possibly incomplete) sentence buffered until `final`."""
        nonlocal pending
        if on_sentence is None:
            return
        if not pending.strip():
            if final:
                pending = ""
            return
        pieces = split_sentences(pending)
        if not final:
            pending = pieces.pop()  # last may still be growing
        else:
            pending = ""
        for sentence in pieces:
            if sentence.strip():
                on_sentence(sentence.strip())

    while True:
        with client.messages.stream(
            model=config.CLAUDE_MODEL,
            max_tokens=500,  # spoken replies are deliberately short
            output_config={"effort": "low"},  # latency matters on a live call
            system=sys_prompt,
            tools=TOOLS,
            messages=state.messages,
        ) as stream:
            for text in stream.text_stream:
                pending += text
                emit()
            response = stream.get_final_message()
        emit(final=True)  # text blocks never span tool-use boundaries

        state.messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text" and block.text.strip():
                reply_parts.append(block.text.strip())

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = _run_tool(state, block.name, dict(block.input))
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
        state.messages.append({"role": "user", "content": tool_results})

        # end_call fired: stop now rather than paying another model round-trip
        # (and a second spoken goodbye) just to hang up.
        if state.ended:
            break

    if reply_parts:
        return " ".join(reply_parts)
    # No spoken text this turn. Only re-prompt if the call is still live —
    # a silent end_call/tool turn shouldn't say "could you say that again?".
    if state.ended:
        return ""
    fallback = "Sorry, could you say that again?"
    if on_sentence:
        on_sentence(fallback)
    return fallback
