"""The conversation brain: an LLM runs the sales call, one spoken turn at a time.

Each call holds a CallState (message history + business context). run_turn()
feeds the caller's transcribed speech to the model (Claude by default, Grok
with LLM_PROVIDER=grok), executes any tool calls (text the demo link, log the
outcome, hang up), and returns the sentence(s) the agent should speak next.
"""

import json
import logging
import os
from dataclasses import dataclass, field

import anthropic
from twilio.rest import Client as TwilioClient

import config
import ai411
import owner_updates
import onboarding
import site_content
import unified
from businesses import Business
from tts import split_sentences

log = logging.getLogger("voice-agent.agent")

MAX_TURNS = 40  # hard stop so a stuck call can't loop forever

# Sales-mode tools (default). AI 411 tools live in ai411.TOOLS — use get_tools().
# Shared with mcp-server/calllog.VALID_OUTCOMES — keep in sync.
SALES_OUTCOMES = [
    "interested",
    "wants_email",
    "callback_requested",
    "sent_sms",
    "not_interested",
    "do_not_call",
    "wrong_number",
    "voicemail",
    "other",
]
# Owner-updates / unified owner filings also land in call-log.csv.
OWNER_OUTCOMES = [
    "owner_update_filed",
    "owner_update_cancelled",
    "owner_update_applied",
    "no_change",
]
ALL_CALL_OUTCOMES = SALES_OUTCOMES + OWNER_OUTCOMES


def _log_call_outcome_tool(
    *,
    description: str,
    outcomes: list[str],
) -> dict:
    return {
        "name": "log_call_outcome",
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": list(outcomes),
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
    }


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
        "name": "send_demo_link_email",
        "description": (
            "Email the business's live demo website link. Use when they prefer "
            "email over text, or when SMS fails. Confirm the spelling of the "
            "address before calling. Also log the email via log_call_outcome "
            "(outcome wants_email or sent_sms if they also got a text)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Destination email address (required).",
                },
            },
            "required": ["email"],
            "additionalProperties": False,
        },
    },
    _log_call_outcome_tool(
        description=(
            "Record how the call went. Call this once, near the end of every call, "
            "before ending it. Use do_not_call whenever the person asks not to be "
            "contacted again."
        ),
        outcomes=SALES_OUTCOMES,
    ),
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


def get_tools(mode: str | None = None) -> list:
    """Tool schemas for the active mode (per-call override when AGENT_MODE=auto)."""
    m = (mode or config.AGENT_MODE or "sales").strip().lower()
    if m == "ai411":
        return ai411.TOOLS
    if m == "owner_updates":
        return owner_updates.TOOLS
    if m == "unified":
        return unified.TOOLS
    if m == "onboarding":
        return onboarding.TOOLS
    return SALES_TOOLS


def get_openers(mode: str | None = None) -> list:
    """Stock opener phrases for the active mode."""
    m = (mode or config.AGENT_MODE or "sales").strip().lower()
    if m == "ai411":
        return ai411.OPENERS
    if m == "owner_updates":
        return owner_updates.OPENERS
    if m == "unified":
        return unified.OPENERS
    if m == "onboarding":
        return onboarding.OPENERS
    return SALES_OPENERS


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


def _load_ai411_caller_profile(caller_number: str) -> dict | None:
    """Load phone-keyed AI 411 profile for system-prompt memory injection."""
    phone = (caller_number or "").strip()
    if not phone:
        return None
    try:
        import sys
        from pathlib import Path

        mcp = Path(__file__).resolve().parent.parent / "mcp-server"
        if str(mcp) not in sys.path:
            sys.path.insert(0, str(mcp))
        import callers

        return callers.get_profile(phone)
    except Exception as e:  # noqa: BLE001 — never block a call on memory
        log = __import__("logging").getLogger("voice-agent.agent")
        log.warning("AI 411 caller profile load failed for %s: %s", phone, e)
        return None


def system_prompt(
    business: Business,
    direction: str,
    caller_number: str,
    openers: bool = True,
    *,
    mode: str | None = None,
    customer: dict | None = None,
) -> str:
    """Build the per-call system prompt for the active / resolved mode.

    openers=False drops the pre-recorded-opener instructions — the realtime
    speech backend speaks natively and has no synthesis pause to cover.
    Inbound SMS (direction="sms") never uses spoken openers.
    """
    if direction == "sms":
        openers = False
    m = (mode or config.AGENT_MODE or "sales").strip().lower()
    if m == "auto":
        m = "ai411"
    cust = customer or {}
    if m == "ai411":
        profile = _load_ai411_caller_profile(caller_number)
        return ai411.system_prompt(
            direction=direction,
            caller_number=caller_number,
            openers=openers,
            caller_profile=profile,
        )
    if m == "owner_updates":
        return owner_updates.system_prompt(
            direction=direction,
            caller_number=caller_number,
            openers=openers,
        )
    if m == "unified":
        return unified.system_prompt(
            business,
            direction=direction,
            caller_number=caller_number,
            openers=openers,
        )
    if m == "onboarding":
        return onboarding.system_prompt(
            direction=direction,
            caller_number=caller_number,
            openers=openers,
            customer=cust,
        )
    return _sales_system_prompt(
        business, direction, caller_number, openers=openers, customer=cust
    )


def _sales_system_prompt(
    business: Business,
    direction: str,
    caller_number: str,
    openers: bool = True,
    customer: dict | None = None,
) -> str:
    """Florida Man Web Services pitch agent (AGENT_MODE=sales, default)."""
    cust = customer or {}
    # Prefer customer demo URL / payment link when this is a post-onboarding sale.
    demo_url = cust.get("demo_url") or business.demo_url
    pay_link = (
        cust.get("stripe_payment_link")
        or getattr(config, "STRIPE_PAYMENT_LINK_DEFAULT", "")
        or ""
    )
    if cust.get("business_name") and (
        not business.name or business.name == "your business"
    ):
        business = Business(
            name=cust.get("business_name") or business.name,
            category=cust.get("category") or business.category,
            phone=cust.get("phone") or getattr(business, "phone", ""),
            address=getattr(business, "address", "") or "",
            rating=getattr(business, "rating", "") or "",
            demo_url=demo_url,
            slug=cust.get("slug") or business.slug,
        )
    elif demo_url:
        business.demo_url = demo_url
    site_text = site_content.site_text(business.slug)
    site_section = (
        f"""

WHAT'S ON THEIR DEMO SITE
The full text of their demo website, so you can answer questions about what
it says and shows:
--- SITE TEXT START ---
{site_text}
--- SITE TEXT END ---
- When asked what's on the site (services, hours, deals, events, wording),
  answer from the site text above only — never invent content that isn't
  there. If it's not in the text, say you're not sure and offer the link.
- If they say something on it is wrong or outdated, don't argue: agree that
  it's an easy fix, mention that updates are included when the site goes
  live, and note the correction when you log the call outcome."""
        if site_text
        else ""
    )
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
- Call direction: {direction}{site_section}

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
- If they want email instead: carefully confirm the spelling of their address,
  call send_demo_link_email with that address, then log_call_outcome with
  wants_email (include the email field) once the send succeeds — or if email
  send fails, apologize and offer SMS or have {config.OWNER_NAME} follow up.
- If staff answers (not the owner): ask if the owner is available; if not,
  offer to text or email the link or note a better time to call back.
- Objection "how much?": looking at the demo is free; going live is $999 a
  month (domain, hosting, ongoing updates). If they balk at the price, don't
  negotiate — offer a follow-up from {config.OWNER_NAME}.
- Objection "not interested": "Totally understand — the demo is yours to keep
  either way, the link will still be there if you change your mind." Then log
  and end the call politely.

TOOLS
- Use send_demo_link_sms the moment they agree to a text.
- Use send_demo_link_email when they give an email address for the demo link.
- Always call log_call_outcome exactly once before the call ends.
- Call end_call together with your final goodbye sentence (on SMS, end_call
  closes this text thread).

POST-ONBOARDING / DEMO-READY CALLS
- If this caller completed onboarding, their demo may already be built. Lead with
  the demo link (SMS or email) and invite them to look.
- If a payment link is configured for them, after they like the demo offer the
  Stripe checkout link so they can start the monthly service. Prefer SMS for
  the Stripe URL (send_sms_links or send_demo_link_sms style brevity).
- Payment link (may be empty): {pay_link or "none configured — collect interest and log"}
- Never claim payment succeeded until ops marks them paid; after payment they
  reach the owner updates desk on future calls.
"""
    if direction == "sms":
        ctx += """

This is an INBOUND SMS text conversation (not a phone call). Reply in short
plain-text messages suitable for SMS — 1-3 short sentences, no markdown.
Identify as an AI on the first reply. Prefer send_demo_link_sms back to their
number, or send_demo_link_email if they give an email. Use end_call when the
thread is done (no more replies needed)."""
    elif direction == "inbound":
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



def resolve_call_mode(
    phone: str,
    *,
    direction: str = "inbound",
    outbound_slug: str | None = None,
) -> tuple[str, dict]:
    """Return (mode, customer_row) for this phone under AGENT_MODE=auto|pinned.

    Never raises: missing mcp-server/customers (old images) falls back to a safe
    pin so Twilio realtime streams do not die on import.
    """
    import logging
    import sys
    from pathlib import Path

    log = logging.getLogger("voice-agent.agent")
    mcp = Path(__file__).resolve().parent.parent / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    try:
        import customers
    except Exception as e:  # noqa: BLE001 — call path must stay alive
        log.error(
            "customers import failed (%s); falling back to pinned mode (env=%s)",
            e,
            config.AGENT_MODE,
        )
        fallback = config.AGENT_MODE if config.AGENT_MODE != "auto" else "ai411"
        if outbound_slug:
            fallback = "sales"
        return fallback, {}

    in_outreach = False
    try:
        from businesses import by_phone

        in_outreach = by_phone(phone) is not None
    except Exception:
        pass
    try:
        mode = customers.resolve_mode(
            phone,
            direction=direction,
            outbound_sales_slug=outbound_slug,
            env_mode=config.AGENT_MODE,
            in_sales_outreach=in_outreach,
        )
        cust = customers.get(phone) or {}
        return mode, cust
    except Exception as e:  # noqa: BLE001
        log.exception("resolve_mode failed for %s: %s", phone, e)
        fallback = config.AGENT_MODE if config.AGENT_MODE != "auto" else "ai411"
        if outbound_slug:
            fallback = "sales"
        return fallback, {}


def effective_mode(state: "CallState") -> str:
    if state.mode:
        return state.mode
    if config.AGENT_MODE == "auto":
        return "ai411"
    return config.AGENT_MODE


@dataclass
class CallState:
    call_sid: str
    business: Business
    direction: str  # "inbound" | "outbound" | "sms"
    caller_number: str = ""
    llm: object = field(default_factory=make_backend)
    ended: bool = False
    turns: int = 0
    # Live transcript buffer (realtime fills this; pipeline rebuilds from llm.messages).
    transcript_turns: list = field(default_factory=list)
    transcript_flushed: bool = False
    outcome_logged: bool = False
    # Per-call product mode when AGENT_MODE=auto (ai411|onboarding|sales|owner_updates).
    mode: str = ""
    customer: dict = field(default_factory=dict)
    # Owner F1/F2 auth (see voice_auth.py). Defaults safe for non-owner modes.
    auth_level: str = "anonymous"
    voice_enrolled: bool = False
    voice_score_ema: float | None = None
    voice_windows: int = 0
    step_up_ok: bool = False
    # Phase 3 SMS OTP step-up (hashes only; never log the code).
    step_up_code_hash: str = ""
    step_up_salt: str = ""
    step_up_expires_at: float = 0.0
    step_up_attempts: int = 0
    step_up_sent_at: float = 0.0
    # Throttle F2 speech-window scoring in realtime media path.
    voice_auth_last_window_at: float = 0.0
    voice_auth_media_bytes: int = 0


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



def _send_demo_link_email(state: CallState, email: str | None) -> str:
    """Email the demo URL via mailer (Resend or SMTP)."""
    import mailer

    addr = (email or "").strip()
    if not addr:
        return "Error: no email address given; ask them to spell it."
    if not mailer.is_valid_email(addr):
        return (
            f"Error: {addr!r} does not look like a valid email. "
            "Confirm the spelling with them."
        )
    subject = f"Your free demo website — {state.business.name}"
    text_body = (
        f"Hi from {config.OWNER_NAME} (Gainesville web developer),\n\n"
        f"Here's the free demo website for {state.business.name}:\n"
        f"{state.business.demo_url}\n\n"
        f"Looking at the demo is free. Taking it live is $999/month "
        f"(domain, hosting, ongoing updates).\n\n"
        f"Reply to this email or call {config.OWNER_CALLBACK_NUMBER} "
        f"to take it live.\n\n"
        f"— {config.OWNER_NAME}"
    )
    html_body = (
        f"<p>Hi from {config.OWNER_NAME} (Gainesville web developer),</p>"
        f"<p>Here's the free demo website for "
        f"<strong>{state.business.name}</strong>:</p>"
        f'<p><a href="{state.business.demo_url}">{state.business.demo_url}</a></p>'
        f"<p>Looking at the demo is free. Taking it live is $999/month "
        f"(domain, hosting, ongoing updates).</p>"
        f"<p>Reply to this email or call {config.OWNER_CALLBACK_NUMBER} "
        f"to take it live.</p>"
        f"<p>— {config.OWNER_NAME}</p>"
    )
    result = mailer.send_email(
        to=addr, subject=subject, text_body=text_body, html_body=html_body
    )
    if result.get("sent"):
        log.info("demo email sent to %s (%s)", addr, result.get("provider"))
        return f"Email with the demo link sent to {addr}."
    err = result.get("error") or "unknown error"
    log.warning("demo email to %s failed: %s", addr, err)
    return (
        f"Error sending email: {err}. Offer to text the link instead "
        f"(send_demo_link_sms), or say {config.OWNER_NAME} will follow up."
    )


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


def _log_outcome(
    state: CallState,
    args: dict,
    *,
    business: Business | None = None,
) -> str:
    import calldb

    biz = business or state.business
    result = calldb.log_outcome(
        call_sid=state.call_sid,
        direction=state.direction,
        business=biz.name,
        slug=biz.slug,
        phone=state.caller_number,
        outcome=str(args.get("outcome", "") or ""),
        email=str(args.get("email", "") or ""),
        callback_time=str(args.get("callback_time", "") or ""),
        notes=str(args.get("notes", "") or ""),
        source="voice-agent",
        dual_write_csv=config.CALL_LOG_DUAL_WRITE_CSV,
    )
    state.outcome_logged = True
    ref = result.get("transcript_ref") or ""
    if ref:
        return f"Outcome logged. transcript_ref={ref}"
    return "Outcome logged."


def record_transcript_turn(
    state: CallState,
    role: str,
    text: str,
    *,
    replace_last_same_role: bool = False,
) -> None:
    """Append one spoken/transcribed turn to the in-memory buffer.

    When replace_last_same_role is True (realtime ASR partials), overwrite the
    trailing turn if it has the same role instead of stacking partials.
    """
    text = (text or "").strip()
    if not text:
        return
    from datetime import datetime as _dt

    ts = _dt.now().isoformat(timespec="seconds")
    if (
        replace_last_same_role
        and state.transcript_turns
        and state.transcript_turns[-1].get("role") == role
    ):
        state.transcript_turns[-1] = {"role": role, "content": text, "ts": ts}
        return
    state.transcript_turns.append({"role": role, "content": text, "ts": ts})


def flush_call_transcript(state: CallState, *, backend: str = "") -> dict:
    """Write buffered / pipeline turns into the relational transcript tables.

    Safe to call more than once; subsequent calls replace the transcript row
    for the same call_sid when new turns arrived.
    """
    import calldb

    if state.transcript_flushed and not state.transcript_turns:
        # Still try pipeline extract if we never buffered realtime turns.
        pass

    turns = list(state.transcript_turns)
    if not turns:
        messages = getattr(state.llm, "messages", None)
        if messages:
            turns = calldb.extract_pipeline_turns(messages)

    backend = backend or (
        "grok-realtime" if config.VOICE_BACKEND == "grok-realtime" else "pipeline"
    )
    result = calldb.finalize_call(
        state.call_sid,
        turns,
        backend=backend,
        direction=state.direction,
        business=state.business.name,
        slug=state.business.slug,
        phone=state.caller_number,
    )
    state.transcript_flushed = True
    if result.get("transcript_ref"):
        log.info(
            "call %s transcript saved (%s turns) ref=%s",
            state.call_sid,
            result.get("turn_count"),
            result.get("transcript_ref"),
        )
    return result


def _owner_log_business(state: CallState, slug: str) -> Business | None:
    """Resolve the site the owner tool touched (may differ from CallState)."""
    slug = (slug or "").strip()
    if not slug:
        return None
    try:
        from businesses import by_slug

        found = by_slug(slug)
        if found is not None:
            return found
    except Exception:  # noqa: BLE001
        pass
    if state.business and state.business.slug == slug:
        return state.business
    return Business(name=slug, slug=slug)


def _maybe_log_owner_update(state: CallState, name: str, args: dict, raw: str) -> None:
    """Append call-db/call-log when a ChangeRequest is filed/cancelled/applied.

    Owner mode previously only wrote change-requests.jsonl — outcomes never
    hit the call store. Best-effort: never raise into the tool path.
    """
    if name not in (
        "create_change_request",
        "cancel_change_request",
        "apply_change_request",
    ):
        return
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return

    outcome = ""
    notes = ""
    slug = ""
    if name == "create_change_request" and data.get("created"):
        outcome = "owner_update_filed"
        cr_id = data.get("id") or ""
        summary = data.get("summary") or args.get("summary") or ""
        n = data.get("item_count")
        notes = f"CR {cr_id}: {summary}".strip()
        if n is not None:
            notes = f"{notes} ({n} item(s))"
        req = data.get("request") if isinstance(data.get("request"), dict) else {}
        slug = str(
            data.get("business_slug")
            or req.get("business_slug")
            or args.get("business_slug")
            or args.get("slug")
            or ""
        )
    elif name == "cancel_change_request" and (
        data.get("cancelled") or data.get("already_cancelled")
    ):
        outcome = "owner_update_cancelled"
        rid = data.get("id") or args.get("request_id") or args.get("id") or ""
        notes = f"Cancelled change request {rid}".strip()
        if data.get("summary"):
            notes = f"{notes}: {data['summary']}"
        slug = str(data.get("business_slug") or "")
    elif name == "apply_change_request" and data.get("applied"):
        outcome = "owner_update_applied"
        rid = data.get("id") or args.get("request_id") or args.get("id") or ""
        notes = f"Applied change request {rid} to local demo HTML".strip()
        if data.get("summary"):
            notes = f"{notes}: {data['summary']}"
        slug = str(data.get("business_slug") or "")
    else:
        return

    biz = _owner_log_business(state, slug)
    try:
        _log_outcome(state, {"outcome": outcome, "notes": notes}, business=biz)
    except Exception as e:  # noqa: BLE001
        log.warning("owner call-log append failed after %s: %s", name, e)


def _inject_transcript_ref(state: CallState, args: dict) -> dict:
    """Attach calldb transcript_ref onto create_change_request when missing."""
    if args.get("transcript_ref"):
        return args
    try:
        import calldb

        if not calldb.enabled():
            return args
        row = calldb.get_call(state.call_sid)
        ref = (row or {}).get("transcript_ref") or ""
        if not ref and state.transcript_turns:
            flush_call_transcript(state)
            row = calldb.get_call(state.call_sid)
            ref = (row or {}).get("transcript_ref") or ""
        if ref:
            out = dict(args)
            out["transcript_ref"] = ref
            return out
    except Exception as e:  # noqa: BLE001
        log.debug("transcript_ref inject skipped: %s", e)
    return args


def _run_owner_tool(state: CallState, name: str, args: dict) -> str:
    """Owner-updates tools + local SMS / call-log outcome logging."""
    import voice_auth

    # Ensure F1 snapshot exists (tests may construct CallState without server._make_state).
    if not getattr(state, "auth_level", None) or state.auth_level == "anonymous":
        if state.caller_number or state.customer:
            voice_auth.refresh_auth(state)

    if name == "enroll_voice_auth":
        conf = args.get("consent_spoken", False)
        if isinstance(conf, str):
            conf = conf.strip().lower() in ("1", "true", "yes", "on")
        if not conf:
            return voice_auth.deny_json(
                {
                    "ok": False,
                    "error": "consent_spoken must be true after the owner agrees.",
                    "code": "consent_required",
                }
            )
        out = voice_auth.enroll_owner_on_state(
            state,
            consent_version=str(args.get("consent_version") or "2026-08-14"),
        )
        return json.dumps(out, ensure_ascii=False)

    if name == "request_step_up_code":
        def _sms(to: str, body: str) -> None:
            _twilio().messages.create(
                to=to, from_=config.TWILIO_PHONE_NUMBER, body=body
            )

        # Prefer real SMS; tests can set VOICE_STEP_UP_DEBUG_CODE=1 without Twilio.
        send_fn = _sms
        if (os.getenv("VOICE_STEP_UP_DEBUG_CODE") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            send_fn = None
        out = voice_auth.request_step_up_code(state, send_sms_fn=send_fn)
        return json.dumps(out, ensure_ascii=False)

    if name == "verify_step_up_code":
        out = voice_auth.verify_step_up_code(state, str(args.get("code") or ""))
        return json.dumps(out, ensure_ascii=False)

    deny = voice_auth.check_tool_allowed(state, name)
    if deny is not None:
        return voice_auth.deny_json(deny)

    if name == "send_sms_links":
        return _send_sms_links(state, args)
    if name == "log_call_outcome":
        return _log_outcome(state, args)
    import mcp_bridge

    tool_args = dict(args or {})
    if name == "create_change_request":
        tool_args = _inject_transcript_ref(state, tool_args)

    raw = mcp_bridge.run_owner_updates_tool(
        name,
        tool_args,
        caller_number=state.caller_number or "",
        call_sid=getattr(state, "call_sid", "") or "",
        auth_level=getattr(state, "auth_level", "") or "",
    )
    _maybe_log_owner_update(state, name, tool_args, raw)
    return raw



def _run_onboarding_tool(state: CallState, name: str, args: dict) -> str:
    """Onboarding interview tools backed by customers registry + memory."""
    import json
    import sys
    from pathlib import Path

    import customer_memory

    mcp = Path(__file__).resolve().parent.parent / "mcp-server"
    if str(mcp) not in sys.path:
        sys.path.insert(0, str(mcp))
    import customers

    phone = (args.get("phone") or state.caller_number or "").strip()
    try:
        if name == "get_customer_profile":
            cust = customers.get(phone) or {}
            mem = customer_memory.recall(phone, limit=5)
            return json.dumps({"ok": True, "customer": cust, "memory": mem}, ensure_ascii=False)

        if name == "save_onboarding_answer":
            field = str(args.get("field") or "").strip()
            value = args.get("value")
            if not field:
                return json.dumps({"ok": False, "error": "field required"})
            cust = customers.get(phone) or {}
            req = cust.get("requirements") if isinstance(cust.get("requirements"), dict) else {}
            if not isinstance(req, dict):
                req = {"_raw": req}
            req[field] = value
            extra = {}
            if field == "business_name" and isinstance(value, str):
                extra["business_name"] = value
            if field == "email" and isinstance(value, str):
                extra["email"] = value
            if field == "category" and isinstance(value, str):
                extra["category"] = value
            out = customers.upsert(
                phone,
                status=cust.get("status") or "onboarding",
                requirements=req,
                **extra,
            )
            customer_memory.append_note(phone, f"{field}={value!r}", kind="onboarding")
            if out.get("customer"):
                state.customer = out["customer"]
            return json.dumps(out, ensure_ascii=False)

        if name == "finalize_requirements":
            conf = args.get("confirmation_spoken", True)
            if isinstance(conf, str):
                conf = conf.strip().lower() in ("1", "true", "yes", "on")
            if not conf:
                return json.dumps({
                    "ok": False,
                    "error": "confirmation_spoken must be true after read-back",
                })
            req = args.get("requirements")
            if isinstance(req, str):
                try:
                    req = json.loads(req)
                except json.JSONDecodeError:
                    req = {"text": req}
            out = customers.save_requirements(
                phone,
                requirements=req or {},
                summary=str(args.get("summary") or ""),
                business_name=str(args.get("business_name") or ""),
                category=str(args.get("category") or ""),
                email=str(args.get("email") or ""),
                mark_ready=True,
            )
            customer_memory.append_note(
                phone,
                f"requirements finalized: {args.get('summary')}",
                kind="onboarding",
            )
            if out.get("customer"):
                state.customer = out["customer"]
            return json.dumps(out, ensure_ascii=False)

        if name == "queue_website_build":
            out = customers.write_builder_brief(phone)
            if out.get("ok"):
                customer_memory.append_note(
                    phone, f"builder brief: {out.get('path')}", kind="build"
                )
            return json.dumps(out, ensure_ascii=False)

        return onboarding.stub_tool_result(name, args)
    except Exception as e:
        log.warning("onboarding tool %s failed: %s", name, e, exc_info=True)
        return onboarding.stub_tool_result(name, args)


def _run_tool(state: CallState, name: str, args: dict) -> str:
    if name == "end_call":
        state.ended = True
        return "The call will end after your current reply is spoken."

    mode = effective_mode(state)

    if mode == "onboarding":
        if name == "send_sms_links":
            return _send_sms_links(state, args)
        if name == "log_call_outcome":
            return _log_outcome(state, args)
        return _run_onboarding_tool(state, name, args)

    if mode == "ai411":
        if name == "send_sms_links":
            return _send_sms_links(state, args)
        # In-process mcp-server stores (knowledge/events/callers/broadcasts/lookup).
        import mcp_bridge

        return mcp_bridge.run_ai411_tool(
            name, args, caller_number=state.caller_number or ""
        )

    if mode == "owner_updates":
        return _run_owner_tool(state, name, args)

    if mode == "unified" or config.is_unified():
        if name == "send_sms_links":
            return _send_sms_links(state, args)
        if name == "log_call_outcome":
            return _log_outcome(state, args)
        import mcp_bridge

        # Owner tools require caller-ID-verified ownership; everything else
        # is the public AI 411 surface. Enforced here, not just in the prompt.
        if name in unified.OWNER_TOOL_NAMES:
            if not unified.caller_owns(state.business, state.caller_number or ""):
                return (
                    "Owner tools are only available when calling from the "
                    "business's own phone line. Offer to note the request "
                    "for a human follow-up instead."
                )
            return _run_owner_tool(state, name, args)
        return mcp_bridge.run_ai411_tool(
            name, args, caller_number=state.caller_number or ""
        )

    if name == "send_demo_link_sms":
        return _send_sms(state, args.get("phone"))
    if name == "send_demo_link_email":
        return _send_demo_link_email(state, args.get("email"))
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

    # Default AI 411 answer: fixed short greeting (no LLM menu monologue).
    if (
        not user_speech
        and not state.llm.has_history()
        and effective_mode(state) == "ai411"
        and state.direction != "sms"
    ):
        import ai411 as _ai411

        greeting = _ai411.AI411_GREETING
        # Seed transcript so the next turn is not another "greet them now".
        if hasattr(state.llm, "messages"):
            state.llm.messages.append({"role": "assistant", "content": greeting})
        if on_sentence:
            on_sentence(greeting)
        return greeting

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
    sys_prompt = system_prompt(
        state.business, state.direction, state.caller_number,
        mode=effective_mode(state), customer=state.customer or None,
    )

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
