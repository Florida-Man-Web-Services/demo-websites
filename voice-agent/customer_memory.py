"""Per-user memory for voice customers (Honcho when configured, else local).

The product requirement is long-lived memory across users/calls. When
HONCHO_API_KEY (or HONCHO_BASE_URL) is set we talk to Honcho; otherwise we
persist small per-phone notes under MEMORY_DIR / data/customer-memory/.

"hombre" in product notes → Honcho.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("voice-agent.memory")

_REPO = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = (
    Path("/data/customer-memory")
    if Path("/data").is_dir()
    else _REPO / "data" / "customer-memory"
)


def _dir() -> Path:
    return Path(os.getenv("MEMORY_DIR", str(_DEFAULT_DIR)))


def honcho_enabled() -> bool:
    return bool(
        (os.getenv("HONCHO_API_KEY") or "").strip()
        or (os.getenv("HONCHO_BASE_URL") or "").strip()
    )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _local_path(phone: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "+-_" else "_" for c in phone)[:40]
    return _dir() / f"{safe}.json"


def load_local(phone: str) -> dict[str, Any]:
    path = _local_path(phone)
    if not path.is_file():
        return {"phone": phone, "notes": [], "facts": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"phone": phone, "notes": [], "facts": {}}


def append_note(phone: str, note: str, *, kind: str = "general") -> dict[str, Any]:
    phone = (phone or "").strip()
    if not phone or not (note or "").strip():
        return {"ok": False, "error": "phone and note required"}

    if honcho_enabled():
        try:
            return _honcho_append(phone, note, kind=kind)
        except Exception as e:  # noqa: BLE001
            log.warning("Honcho append failed, falling back to local: %s", e)

    data = load_local(phone)
    data.setdefault("notes", []).append(
        {"at": _now(), "kind": kind, "text": note.strip()}
    )
    data["notes"] = data["notes"][-100:]  # cap
    data["updated_at"] = _now()
    path = _local_path(phone)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, "backend": "local", "phone": phone}


def recall(phone: str, *, limit: int = 10) -> dict[str, Any]:
    phone = (phone or "").strip()
    if not phone:
        return {"ok": False, "error": "phone required", "notes": []}

    if honcho_enabled():
        try:
            return _honcho_recall(phone, limit=limit)
        except Exception as e:  # noqa: BLE001
            log.warning("Honcho recall failed, falling back to local: %s", e)

    data = load_local(phone)
    notes = list(reversed(data.get("notes") or []))[: max(1, min(limit, 50))]
    return {
        "ok": True,
        "backend": "local",
        "phone": phone,
        "notes": notes,
        "facts": data.get("facts") or {},
    }


def _honcho_append(phone: str, note: str, *, kind: str) -> dict[str, Any]:
    """Best-effort Honcho write via HTTP if the SDK is not installed."""
    import httpx

    base = (os.getenv("HONCHO_BASE_URL") or "https://api.honcho.dev").rstrip("/")
    key = (os.getenv("HONCHO_API_KEY") or "").strip()
    app = (os.getenv("HONCHO_APP_ID") or "fmws-voice").strip()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    # Session key = phone so each customer is isolated.
    payload = {
        "app_id": app,
        "user_id": phone,
        "session_id": phone,
        "content": f"[{kind}] {note}",
        "metadata": {"source": "voice-agent", "kind": kind},
    }
    # Endpoint path may vary by Honcho version — document for ops.
    url = os.getenv("HONCHO_MESSAGE_URL") or f"{base}/v1/apps/{app}/users/{phone}/sessions/{phone}/messages"
    r = httpx.post(url, headers=headers, json=payload, timeout=15.0)
    if r.status_code >= 400:
        # Always dual-write local so we never lose memory on API mismatch.
        append_note.__wrapped__ if False else None  # placate linters
        data = load_local(phone)
        data.setdefault("notes", []).append(
            {"at": _now(), "kind": kind, "text": note.strip(), "honcho_error": r.text[:200]}
        )
        path = _local_path(phone)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "backend": "local+honcho_error",
            "status_code": r.status_code,
            "phone": phone,
        }
    return {"ok": True, "backend": "honcho", "phone": phone}


def _honcho_recall(phone: str, *, limit: int) -> dict[str, Any]:
    import httpx

    base = (os.getenv("HONCHO_BASE_URL") or "https://api.honcho.dev").rstrip("/")
    key = (os.getenv("HONCHO_API_KEY") or "").strip()
    app = (os.getenv("HONCHO_APP_ID") or "fmws-voice").strip()
    headers = {"Authorization": f"Bearer {key}"}
    url = os.getenv("HONCHO_RECALL_URL") or (
        f"{base}/v1/apps/{app}/users/{phone}/sessions/{phone}/messages"
    )
    r = httpx.get(url, headers=headers, params={"limit": limit}, timeout=15.0)
    if r.status_code >= 400:
        return load_fallback(phone, limit)
    data = r.json()
    return {"ok": True, "backend": "honcho", "phone": phone, "raw": data}


def load_fallback(phone: str, limit: int) -> dict[str, Any]:
    data = load_local(phone)
    notes = list(reversed(data.get("notes") or []))[:limit]
    return {"ok": True, "backend": "local", "phone": phone, "notes": notes}
