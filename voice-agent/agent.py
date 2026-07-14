"""The conversation brain: an LLM runs the sales call, one spoken turn at a time.

Each call holds a CallState (message history + business context). run_turn()
feeds the caller's transcribed speech to the model (Claude by default, Grok
with LLM_PROVIDER=grok), executes any tool calls (text the demo link, log the
outcome, hang up), and returns the sentence(s) the agent should speak next.
"""

import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

import anthropic
from twilio.rest import Client as TwilioClient

import config
import ai411
from businesses import Business
from tts import split_sentences

log = logging.getLogger("voice-agent.agent")

MAX_TURNS = 40  # hard stop so a stuck call can't loop forever

# Sales-mode tools (default). AI 411 tools live in ai411.TOOLS — use get_tools().
SALES_TOOLS = [
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
SALES_OPENERS = [
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


def get_tools() -> list:
    """Tool schemas for the active AGENT_MODE (sales default, or ai411)."""
    return ai411.TOOLS if config.is_ai411() else SALES_TOOLS


def get_openers() -> list:
    """Stock opener phrases for the active AGENT_MODE."""
    return ai411.OPENERS if config.is_ai411() else SALES_OPENERS


# Back-compat names: resolve to sales lists when AGENT_MODE is default.
# Prefer get_tools() / get_openers() so mode switches are live after import.
TOOLS = SALES_TOOLS
OPENERS = SALES_OPENERS


def _spoken_url(demo_url: str) -> str:
    return demo_url.removeprefix("https://")


def _opener_rule(openers_list: list | None = None) -> str:
    phrases = openers_list if openers_list is not None else get_openers()
    return f"""- Open every reply with one of these exact opener sentences (pick whichever
  fits, vary them, punctuation included): {" | ".join(phrases)}
  These are pre-recorded so they play instantly and cover the synthesis
  pause — like natural phone rhythm. Only improvise a different opener if
  none of them fits at all.
"""


def system_prompt(
    business: Business, direction: str, caller_number: str, openers: bool = True
) -> str:
    """Build the per-call system prompt for the active AGENT_MODE.

    openers=False drops the pre-recorded-opener instructions — the realtime
    speech backend speaks natively and has no synthesis pause to cover.
    """
    if config.is_ai411():
        return ai411.system_prompt(
            direction=direction,
            caller_number=caller_number,
            openers=openers,
        )
    return _sales_system_prompt(business, direction, caller_number, openers=openers)


def _sales_system_prompt(
    business: Business, direction: str, caller_number: str, openers: bool = True
) -> str:
    """Florida Man Web Services pitch agent (AGENT_MODE=sales, default)."""
    ctx = f"""You are {config.OWNER_NAME}'s AI phone assistant, selling websites for a one-person
web development business in Gainesville, Florida. {config.OWNER_NAME} builds free demo
websites for local businesses that don't have one, then charges $999 a month
to take the demo live and keep it running (their own domain name, hosting,
ongoing updates, findable on Google). You are on a live phone call; everything
you write will be spoken aloud by a text-to-speech voice.

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
{_opener_rule(SALES_OPENERS) if openers else ""}- Plain conversational English: no bullet points, no markdown, no emoji.
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
  If asked exactly what going live costs: it is $999 a month, which covers the
  domain, hosting, and ongoing updates. Quote only that number — do not
  discount or negotiate; offer to have {config.OWNER_NAME} follow up on specifics.
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
- Objection "how much?": looking at the demo is free; going live is $999 a
  month (domain, hosting, ongoing updates). If they balk at the price, don't
  negotiate — offer a follow-up from {config.OWNER_NAME}.
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


# --- LLM backends ------------------------------------------------------------
# One conversation = one backend instance owning the message history in its
# provider's native format. run_turn() only sees the normalized _TurnResult.

MAX_REPLY_TOKENS = 500  # spoken replies are deliberately short


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class _TurnResult:
    text_parts: list
    tool_calls: list


_anthropic_client = None
_xai_client = None


def _anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:  # build once, reuse the HTTP session
        _anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)
    return _anthropic_client


def _xai():
    global _xai_client
    if _xai_client is None:
        if not config.XAI_API_KEY:
            raise SystemExit(
                "LLM_PROVIDER=grok needs XAI_API_KEY in voice-agent/.env "
                "(create one at console.x.ai)."
            )
        # Imported lazily: openai is only needed when the grok backend is used.
        from openai import OpenAI

        _xai_client = OpenAI(api_key=config.XAI_API_KEY, base_url=config.XAI_BASE_URL)
    return _xai_client


class _ClaudeBackend:
    def __init__(self):
        self.messages: list = []

    def has_history(self) -> bool:
        return bool(self.messages)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list) -> None:
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call_id, "content": text}
                    for call_id, text in results
                ],
            }
        )

    def stream(self, sys_prompt: str, on_delta) -> _TurnResult:
        with _anthropic().messages.stream(
            model=config.CLAUDE_MODEL,
            max_tokens=MAX_REPLY_TOKENS,
            output_config={"effort": "low"},  # latency matters on a live call
            system=sys_prompt,
            tools=get_tools(),
            messages=self.messages,
        ) as stream:
            for text in stream.text_stream:
                on_delta(text)
            response = stream.get_final_message()
        self.messages.append({"role": "assistant", "content": response.content})
        return _TurnResult(
            text_parts=[
                b.text.strip()
                for b in response.content
                if b.type == "text" and b.text.strip()
            ],
            tool_calls=[
                ToolCall(b.id, b.name, dict(b.input))
                for b in response.content
                if b.type == "tool_use"
            ],
        )


def _openai_tools() -> list:
    """Tools stay in Anthropic schema; convert for the OpenAI-format API."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in get_tools()
    ]


class _GrokBackend:
    def __init__(self):
        self.messages: list = []

    def has_history(self) -> bool:
        return bool(self.messages)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list) -> None:
        for call_id, text in results:
            self.messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": text}
            )

    def stream(self, sys_prompt: str, on_delta) -> _TurnResult:
        stream = _xai().chat.completions.create(
            model=config.GROK_MODEL,
            max_tokens=MAX_REPLY_TOKENS,
            stream=True,
            messages=[{"role": "system", "content": sys_prompt}, *self.messages],
            tools=_openai_tools(),
        )
        text = ""
        by_index: dict[int, dict] = {}  # streamed tool calls arrive as deltas
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                text += delta.content
                on_delta(delta.content)
            for tc in delta.tool_calls or []:
                acc = by_index.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                if tc.id:
                    acc["id"] = tc.id
                if tc.function and tc.function.name:
                    acc["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    acc["args"] += tc.function.arguments

        assistant: dict = {"role": "assistant", "content": text or None}
        if by_index:
            assistant["tool_calls"] = [
                {
                    "id": acc["id"],
                    "type": "function",
                    "function": {"name": acc["name"], "arguments": acc["args"] or "{}"},
                }
                for _, acc in sorted(by_index.items())
            ]
        self.messages.append(assistant)

        tool_calls = []
        for _, acc in sorted(by_index.items()):
            try:
                args = json.loads(acc["args"]) if acc["args"].strip() else {}
            except ValueError:
                log.warning("Grok sent unparseable tool args: %r", acc["args"])
                args = {}  # _run_tool copes with missing keys
            tool_calls.append(ToolCall(acc["id"], acc["name"], args))
        return _TurnResult(
            text_parts=[text.strip()] if text.strip() else [],
            tool_calls=tool_calls,
        )


def make_backend():
    if config.LLM_PROVIDER == "anthropic":
        return _ClaudeBackend()
    if config.LLM_PROVIDER == "grok":
        return _GrokBackend()
    raise SystemExit(
        f"Unknown LLM_PROVIDER {config.LLM_PROVIDER!r}; use 'anthropic' or 'grok'."
    )


@dataclass
class CallState:
    call_sid: str
    business: Business
    direction: str  # "inbound" | "outbound"
    caller_number: str = ""
    llm: object = field(default_factory=make_backend)
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


def _send_sms_links(state: CallState, args: dict) -> str:
    """AI 411: text result links (not the sales demo pitch)."""
    to = args.get("phone") or state.caller_number
    if not to:
        return "Error: no destination number known; ask the caller for their number."
    links = args.get("links") or []
    if isinstance(links, str):
        links = [links]
    if not links:
        return "Error: no links provided to text."
    note = (args.get("note") or "Gainesville AI 411 — links you asked for:").strip()
    body = note + "\n" + "\n".join(str(u) for u in links)
    if len(body) > 1500:
        body = body[:1490] + "…"
    try:
        msg = _twilio().messages.create(
            to=to, from_=config.TWILIO_PHONE_NUMBER, body=body
        )
        log.info("AI 411 SMS links sent to %s (%s)", to, msg.sid)
        return f"SMS with {len(links)} link(s) sent to {to}."
    except Exception as e:
        log.warning("AI 411 SMS to %s failed: %s", to, e)
        return f"Error sending SMS: {e}. Offer to read the links slowly instead."


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
    if name == "end_call":
        state.ended = True
        return "The call will end after your current reply is spoken."

    if config.is_ai411():
        if name == "send_sms_links":
            return _send_sms_links(state, args)
        # In-process mcp-server stores (knowledge/events/callers/broadcasts/lookup).
        import mcp_bridge

        return mcp_bridge.run_ai411_tool(
            name, args, caller_number=state.caller_number or ""
        )

    if name == "send_demo_link_sms":
        return _send_sms(state, args.get("phone"))
    if name == "log_call_outcome":
        return _log_outcome(state, args)
    return f"Unknown tool {name}"


def run_turn(
    state: CallState,
    user_speech: str | None,
    on_sentence=None,
) -> str:
    """One conversational turn. Returns the full reply text.

    If `on_sentence` is given, each completed sentence is ALSO passed to it
    while the model is still generating — so TTS can start on sentence one before
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
        state.llm.add_user(user_speech)
    elif not state.llm.has_history():
        state.llm.add_user("<call connected — greet them now>")
    else:
        state.llm.add_user("<silence — the line is quiet; check in briefly>")

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
            tail = pieces.pop()  # last may still be growing
            # Keep the raw tail, not the stripped piece: a delta ending in
            # whitespace ("Hi! ") must not fuse with the next chunk ("Hi!This").
            cut = pending.rfind(tail)
            pending = pending[cut:] if cut != -1 else tail
        else:
            pending = ""
        for sentence in pieces:
            if sentence.strip():
                on_sentence(sentence.strip())

    def on_delta(text: str):
        nonlocal pending
        pending += text
        emit()

    while True:
        result = state.llm.stream(sys_prompt, on_delta)
        emit(final=True)  # text never spans tool-use boundaries

        reply_parts.extend(result.text_parts)

        if not result.tool_calls:
            break

        state.llm.add_tool_results(
            [(tc.id, _run_tool(state, tc.name, tc.args)) for tc in result.tool_calls]
        )

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
