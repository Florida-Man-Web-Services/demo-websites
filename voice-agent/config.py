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

# --- Models -----------------------------------------------------------------
# Claude drives the conversation. Opus 4.8 with effort=low keeps per-turn
# latency reasonable; override with VOICE_AGENT_MODEL if you want to trade
# quality for speed.
CLAUDE_MODEL = os.getenv("VOICE_AGENT_MODEL", "claude-opus-4-8")

# Sesame CSM-1B served by DeepInfra ($7 per 1M characters). Point this at any
# endpoint that accepts {"text": ...} and returns {"audio": <wav>} — e.g. a
# self-hosted csm-1b — without touching the rest of the code.
SESAME_TTS_URL = os.getenv(
    "SESAME_TTS_URL", "https://api.deepinfra.com/v1/inference/sesame/csm-1b"
)

# --- Identity / pitch -------------------------------------------------------
OWNER_NAME = os.getenv("OWNER_NAME", "Noah")
OWNER_CALLBACK_NUMBER = os.getenv("OWNER_CALLBACK_NUMBER", TWILIO_PHONE_NUMBER)

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

DEMO_BASE_URL = "https://florida-man-bioscience.github.io/demo-websites/generated-sites"


def require(*names: str) -> None:
    """Fail fast with a readable message when required env vars are missing."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + "\nCopy .env.example to .env and fill them in."
        )
