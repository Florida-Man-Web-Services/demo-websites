"""Outbound email for demo-link delivery.

Backends (first configured wins):
  1. Resend HTTP API — set RESEND_API_KEY (+ EMAIL_FROM)
  2. SMTP — set SMTP_HOST, EMAIL_FROM; optional SMTP_USER/PASSWORD/PORT/TLS

If nothing is configured, send_* returns a clear error string for the model
to speak (log the address with log_call_outcome / wants_email as fallback).
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import config

log = logging.getLogger("voice-agent.mailer")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(addr: str) -> bool:
    addr = (addr or "").strip()
    return bool(addr) and bool(_EMAIL_RE.match(addr)) and len(addr) < 254


def email_configured() -> bool:
    if not (getattr(config, "EMAIL_FROM", "") or "").strip():
        return False
    if (getattr(config, "RESEND_API_KEY", "") or "").strip():
        return True
    if (getattr(config, "SMTP_HOST", "") or "").strip():
        return True
    return False


def send_email(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> dict[str, Any]:
    """Send one email. Returns {\"sent\": True, ...} or {\"sent\": False, \"error\": ...}."""
    to = (to or "").strip()
    if not is_valid_email(to):
        return {"sent": False, "error": f"invalid email address {to!r}"}
    if not email_configured():
        return {
            "sent": False,
            "error": (
                "email is not configured on this server "
                "(set EMAIL_FROM + RESEND_API_KEY or SMTP_HOST)"
            ),
        }

    from_addr = config.EMAIL_FROM.strip()
    if config.RESEND_API_KEY.strip():
        return _send_resend(
            from_addr=from_addr,
            to=to,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
        )
    return _send_smtp(
        from_addr=from_addr,
        to=to,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


def _send_resend(
    *,
    from_addr: str,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None,
) -> dict[str, Any]:
    import httpx

    payload: dict[str, Any] = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {config.RESEND_API_KEY.strip()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30.0,
        )
        if r.status_code >= 400:
            log.warning("Resend error %s: %s", r.status_code, r.text[:500])
            return {
                "sent": False,
                "error": f"Resend HTTP {r.status_code}: {r.text[:200]}",
            }
        data = r.json() if r.content else {}
        msg_id = data.get("id") or ""
        log.info("email sent via Resend to %s id=%s", to, msg_id)
        return {"sent": True, "provider": "resend", "id": msg_id, "to": to}
    except Exception as e:  # noqa: BLE001
        log.warning("Resend send failed: %s", e)
        return {"sent": False, "error": str(e)}


def _send_smtp(
    *,
    from_addr: str,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None,
) -> dict[str, Any]:
    host = config.SMTP_HOST.strip()
    port = int(getattr(config, "SMTP_PORT", 587) or 587)
    user = (getattr(config, "SMTP_USER", "") or "").strip()
    password = getattr(config, "SMTP_PASSWORD", "") or ""
    use_tls = bool(getattr(config, "SMTP_TLS", True))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        log.info("email sent via SMTP to %s host=%s", to, host)
        return {"sent": True, "provider": "smtp", "to": to}
    except Exception as e:  # noqa: BLE001
        log.warning("SMTP send failed: %s", e)
        return {"sent": False, "error": str(e)}
