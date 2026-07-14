"""Central configuration for the voice sales agent.

Everything is driven by environment variables (see .env.example).
A .env file in this directory is loaded automatically if present.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

AGENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = AGENT_DIR.parent

load_dotenv(AGENT_DIR / ".env")

# --- Required keys ---------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

# Public HTTPS base URL Twilio uses to reach this server (ngrok/cloudflared/VPS).
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Verify X-Twilio-Signature on every webhook post (on by default). Only turn
# this off (=0) for local experiments that fake Twilio requests by hand.
VALIDATE_TWILIO_WEBHOOKS = os.getenv(
    "VALIDATE_TWILIO_WEBHOOKS", "1"
).strip().lower() not in ("0", "false", "no")

# --- Models -----------------------------------------------------------------
# Which LLM drives the conversation: "anthropic" (Claude, default) or "grok"
# (xAI's OpenAI-compatible API at https://api.x.ai/v1 — needs XAI_API_KEY).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

# Claude: Opus 4.8 with effort=low keeps per-turn latency reasonable;
# override with VOICE_AGENT_MODEL if you want to trade quality for speed.
CLAUDE_MODEL = os.getenv("VOICE_AGENT_MODEL", "claude-opus-4-8")

# Grok (LLM_PROVIDER=grok): grok-4.3 is the current flagship ($1.25/$2.50
# per 1M tokens, 1M context); xAI bills grok-4.5 as faster if per-turn
# latency matters more than cost.
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4.3")
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")

# --- Voice backend ------------------------------------------------------------
# "pipeline" (default): Twilio speech-to-text -> LLM -> Sesame TTS, one webhook
#   per spoken turn.
# "grok-realtime": bidirectional Twilio Media Stream bridged to xAI's realtime
#   speech-to-speech API (wss://api.x.ai/v1/realtime). Needs XAI_API_KEY; the
#   LLM_PROVIDER / Sesame settings are unused in this mode.
VOICE_BACKEND = os.getenv("VOICE_BACKEND", "pipeline").strip().lower()

XAI_REALTIME_URL = os.getenv("XAI_REALTIME_URL", "wss://api.x.ai/v1/realtime")
XAI_REALTIME_MODEL = os.getenv("XAI_REALTIME_MODEL", "grok-voice-latest")
# A voice agent pre-configured at console.x.ai; when set we connect with
# ?agent_id=... (its voice/config applies) but still override instructions
# and tools per call so the model knows which business it is talking to.
XAI_VOICE_AGENT_ID = os.getenv("XAI_VOICE_AGENT_ID", "")
# Built-in voices: eve, ara, rex, sal, leo — or a custom voice ID. Left empty,
# the session (or console agent) default is used.
GROK_VOICE = os.getenv("GROK_VOICE", "")
# Server-side VAD: how much silence ends the caller's turn.
GROK_VAD_SILENCE_MS = int(os.getenv("GROK_VAD_SILENCE_MS", "600"))

# Sesame CSM-1B served by DeepInfra ($7 per 1M characters). Point this at any
# endpoint that accepts {"text": ...} and returns {"audio": <wav>} — e.g. a
# self-hosted csm-1b — without touching the rest of the code.
SESAME_TTS_URL = os.getenv(
    "SESAME_TTS_URL", "https://api.deepinfra.com/v1/inference/sesame/csm-1b"
)

# --- Identity / pitch -------------------------------------------------------
OWNER_NAME = os.getenv("OWNER_NAME", "Noah")
OWNER_CALLBACK_NUMBER = os.getenv("OWNER_CALLBACK_NUMBER", TWILIO_PHONE_NUMBER)

# --- Agent mode ---------------------------------------------------------------
# "sales" (default): Florida Man Web Services demo-website pitch agent.
# "ai411": Gainesville AI 411 directory/events/broadcast operator (issue #51).
# VOICE_AGENT_MODE is accepted as an alias for AGENT_MODE.
_raw_agent_mode = (
    os.getenv("AGENT_MODE") or os.getenv("VOICE_AGENT_MODE") or "sales"
).strip().lower()
if _raw_agent_mode not in ("sales", "ai411"):
    raise SystemExit(
        f"Unknown AGENT_MODE {_raw_agent_mode!r}; use 'sales' or 'ai411'."
    )
AGENT_MODE = _raw_agent_mode


def is_ai411() -> bool:
    return AGENT_MODE == "ai411"


# --- AI 411 MCP bridge ------------------------------------------------------
# How voice-agent reaches knowledge/events/callers/broadcasts/lookup:
#   inproc (default) — import mcp-server modules from the monorepo checkout
#   http             — Streamable HTTP tools/call to MCP_URL (bearer token)
#   auto             — try inproc; if import fails and MCP_URL is set, use http
#
# Production voice containers often lack the mcp-server store filesystem; set
# MCP_MODE=http (or auto) with MCP_URL=https://mcp.flmanbiosci.net/mcp and
# MCP_AUTH_TOKEN matching the demo-mcp Deployment.
_raw_mcp_mode = (os.getenv("MCP_MODE") or "inproc").strip().lower()
if _raw_mcp_mode not in ("inproc", "http", "auto"):
    # Soft-fallback so a typo does not kill the process at import time.
    _raw_mcp_mode = "inproc"
MCP_MODE = _raw_mcp_mode
MCP_URL = os.getenv("MCP_URL", "").strip()
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "").strip()


# --- Data files -------------------------------------------------------------
OUTREACH_CSV = Path(
    os.getenv("OUTREACH_CSV", REPO_ROOT / "correspondences" / "outreach-data.csv")
)
CALL_ORDER_CSV = Path(
    os.getenv("CALL_ORDER_CSV", REPO_ROOT / "correspondences" / "call-order.csv")
)
BUSINESS_JSON = Path(
    os.getenv(
        "BUSINESS_JSON",
        REPO_ROOT / "gainesville-no-website" / "gainesville_no_website.json",
    )
)

CALL_LOG = Path(os.getenv("CALL_LOG", AGENT_DIR / "call-log.csv"))
AUDIO_CACHE_DIR = Path(os.getenv("AUDIO_CACHE_DIR", AGENT_DIR / "audio_cache"))

# --- Demo site URLs ---------------------------------------------------------
# "slug" (default): {DEMO_BASE_URL}/{slug}.html — the GitHub Pages layout.
# "hash": {DEMO_BASE_URL}/{sha256(page)[:12]}/ — the floridamanweb.online
#   layout served by hosting/Dockerfile; links are unguessable but change
#   whenever the page content changes.
DEMO_URL_STYLE = os.getenv("DEMO_URL_STYLE", "slug").strip().lower()
DEMO_BASE_URL = os.getenv(
    "DEMO_BASE_URL",
    "https://florida-man-bioscience.github.io/demo-websites/generated-sites",
).rstrip("/")
# Where the generated demo pages live; hash-style URLs are computed from
# these files, so they must be present (they're baked into the demo-mcp image).
GENERATED_SITES_DIR = Path(
    os.getenv("GENERATED_SITES_DIR", REPO_ROOT / "generated-sites")
)


def require(*names: str) -> None:
    """Fail fast with a readable message when required env vars are missing."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + "\nCopy .env.example to .env and fill them in."
        )
