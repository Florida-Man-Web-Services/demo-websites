"""Pluggable speaker-verification adapters for owner F2 (Phase 4).

Vendors (VOICE_AUTH_VENDOR):
  none|off|stub     — disabled
  mock|local_stub   — deterministic local scores (tests / dev)
  http              — POST JSON to VOICE_AUTH_HTTP_URL (Bearer optional)

HTTP contract (verify):
  POST {template_id, sample_rate, audio_b64?, meta?}
  → {ok, score: float 0-1, liveness_ok?: bool, error?}

HTTP contract (enroll) optional:
  POST {phone, sample_rate, audio_b64?, consent_version?}
  → {ok, template_id, quality?, error?}
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger("voice-agent.voice_auth_vendors")


@dataclass
class VerifyResult:
    ok: bool
    score: float | None = None
    liveness_ok: bool = True
    error: str = ""
    vendor: str = ""
    raw: dict | None = None


@dataclass
class EnrollResult:
    ok: bool
    template_id: str = ""
    quality: float | None = None
    error: str = ""
    vendor: str = ""
    raw: dict | None = None


class SpeakerVerifyVendor(Protocol):
    name: str

    def verify(
        self,
        *,
        template_id: str,
        pcm: bytes | None,
        sample_rate: int,
        meta: dict[str, Any] | None = None,
    ) -> VerifyResult: ...

    def enroll(
        self,
        *,
        phone: str,
        pcm: bytes | None,
        sample_rate: int,
        meta: dict[str, Any] | None = None,
    ) -> EnrollResult: ...


class NoneVendor:
    name = "none"

    def verify(self, **kwargs: Any) -> VerifyResult:
        return VerifyResult(ok=False, error="vendor_none", vendor=self.name)

    def enroll(self, **kwargs: Any) -> EnrollResult:
        return EnrollResult(ok=False, error="vendor_none", vendor=self.name)


class MockVendor:
    """Deterministic scores for tests — NOT a real biometric."""

    name = "mock"

    def __init__(self, score: float = 0.92, liveness_ok: bool = True) -> None:
        self.score = score
        self.liveness_ok = liveness_ok

    def verify(
        self,
        *,
        template_id: str,
        pcm: bytes | None,
        sample_rate: int,
        meta: dict[str, Any] | None = None,
    ) -> VerifyResult:
        if not template_id:
            return VerifyResult(ok=False, error="missing template_id", vendor=self.name)
        # Anti-replay signal: empty pcm still scores in mock, but callers may reject hash dups.
        return VerifyResult(
            ok=True,
            score=float(self.score),
            liveness_ok=bool(self.liveness_ok),
            vendor=self.name,
        )

    def enroll(
        self,
        *,
        phone: str,
        pcm: bytes | None,
        sample_rate: int,
        meta: dict[str, Any] | None = None,
    ) -> EnrollResult:
        tid = f"mock-{hashlib.sha256((phone or 'x').encode()).hexdigest()[:12]}"
        return EnrollResult(ok=True, template_id=tid, quality=1.0, vendor=self.name)


class LocalStubVendor(MockVendor):
    name = "local_stub"


class HttpVendor:
    """Generic HTTP speaker-verify backend."""

    name = "http"

    def __init__(self) -> None:
        self.url = (os.getenv("VOICE_AUTH_HTTP_URL") or "").rstrip("/")
        self.enroll_url = (
            os.getenv("VOICE_AUTH_HTTP_ENROLL_URL") or f"{self.url}/enroll"
        ).rstrip("/")
        self.verify_path = os.getenv("VOICE_AUTH_HTTP_VERIFY_PATH") or "/verify"
        self.token = (os.getenv("VOICE_AUTH_HTTP_TOKEN") or "").strip()
        self.timeout = float(os.getenv("VOICE_AUTH_HTTP_TIMEOUT_S", "8") or "8")

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def verify(
        self,
        *,
        template_id: str,
        pcm: bytes | None,
        sample_rate: int,
        meta: dict[str, Any] | None = None,
    ) -> VerifyResult:
        if not self.url:
            return VerifyResult(
                ok=False, error="VOICE_AUTH_HTTP_URL not set", vendor=self.name
            )
        import httpx

        body: dict[str, Any] = {
            "template_id": template_id,
            "sample_rate": sample_rate,
            "meta": meta or {},
        }
        if pcm:
            body["audio_b64"] = base64.b64encode(pcm).decode("ascii")
        url = self.url + (
            self.verify_path if self.verify_path.startswith("/") else f"/{self.verify_path}"
        )
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(url, json=body, headers=self._headers())
            data = r.json() if r.content else {}
            if r.status_code >= 400:
                return VerifyResult(
                    ok=False,
                    error=str(data.get("error") or r.text or r.status_code),
                    vendor=self.name,
                    raw=data if isinstance(data, dict) else None,
                )
            score = data.get("score")
            return VerifyResult(
                ok=bool(data.get("ok", True)) and score is not None,
                score=float(score) if score is not None else None,
                liveness_ok=bool(data.get("liveness_ok", True)),
                vendor=self.name,
                raw=data if isinstance(data, dict) else None,
                error=str(data.get("error") or ""),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("http voice verify failed: %s", e)
            return VerifyResult(ok=False, error=str(e), vendor=self.name)

    def enroll(
        self,
        *,
        phone: str,
        pcm: bytes | None,
        sample_rate: int,
        meta: dict[str, Any] | None = None,
    ) -> EnrollResult:
        if not self.enroll_url:
            return EnrollResult(
                ok=False, error="VOICE_AUTH_HTTP_ENROLL_URL not set", vendor=self.name
            )
        import httpx

        body: dict[str, Any] = {
            "phone": phone,
            "sample_rate": sample_rate,
            "meta": meta or {},
        }
        if pcm:
            body["audio_b64"] = base64.b64encode(pcm).decode("ascii")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(self.enroll_url, json=body, headers=self._headers())
            data = r.json() if r.content else {}
            if r.status_code >= 400 or not data.get("ok", True):
                return EnrollResult(
                    ok=False,
                    error=str(data.get("error") or r.text or r.status_code),
                    vendor=self.name,
                    raw=data if isinstance(data, dict) else None,
                )
            return EnrollResult(
                ok=True,
                template_id=str(data.get("template_id") or ""),
                quality=float(data["quality"]) if data.get("quality") is not None else None,
                vendor=self.name,
                raw=data if isinstance(data, dict) else None,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("http voice enroll failed: %s", e)
            return EnrollResult(ok=False, error=str(e), vendor=self.name)


_vendor_cache: SpeakerVerifyVendor | None = None
_vendor_name: str | None = None


def get_vendor(name: str | None = None) -> SpeakerVerifyVendor:
    """Resolve vendor (cached per process name)."""
    global _vendor_cache, _vendor_name
    raw = (name or os.getenv("VOICE_AUTH_VENDOR") or "none").strip().lower()
    if _vendor_cache is not None and _vendor_name == raw:
        return _vendor_cache
    if raw in ("", "none", "off", "stub"):
        v: SpeakerVerifyVendor = NoneVendor()
    elif raw == "mock":
        v = MockVendor(
            score=float(os.getenv("VOICE_AUTH_MOCK_SCORE", "0.92") or "0.92"),
            liveness_ok=(os.getenv("VOICE_AUTH_MOCK_LIVENESS", "true").strip().lower()
                         not in ("0", "false", "no")),
        )
    elif raw == "local_stub":
        v = LocalStubVendor(
            score=float(os.getenv("VOICE_AUTH_MOCK_SCORE", "0.92") or "0.92"),
        )
    elif raw == "http":
        v = HttpVendor()
    else:
        log.warning("unknown VOICE_AUTH_VENDOR %r — using none", raw)
        v = NoneVendor()
    _vendor_cache = v
    _vendor_name = raw
    return v


def reset_vendor_cache() -> None:
    global _vendor_cache, _vendor_name
    _vendor_cache = None
    _vendor_name = None


def pcm_fingerprint(pcm: bytes | None) -> str:
    if not pcm:
        return ""
    return hashlib.sha256(pcm).hexdigest()
