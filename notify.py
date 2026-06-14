"""
Email notifications (entry confirmations).

Scaffold: a real SMTP send when the mail server is configured via environment
variables, and a logged no-op when it isn't -- so the app runs end to end
without a mail server, and starts sending the moment credentials are supplied.

Config (all optional; absence of ``BMEOS_SMTP_HOST`` disables sending):
    BMEOS_SMTP_HOST, BMEOS_SMTP_PORT (default 587),
    BMEOS_SMTP_USER, BMEOS_SMTP_PASS, BMEOS_SMTP_FROM
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger("notify")


def is_enabled() -> bool:
    return bool(os.environ.get("BMEOS_SMTP_HOST"))


def send_entry_confirmation(entry: dict, event: dict) -> bool:
    """Send (or log) an entry-confirmation email. Returns True if actually sent."""
    to = entry.get("email")
    if not to:
        return False
    subject = f"Entry confirmed — {event.get('name', '')}"
    card = entry.get("card_number") or "hired on the day"
    body = (
        f"Hi {entry['name']},\n\n"
        f"Your entry for {event.get('name', '')} ({event.get('date', '')}) is confirmed.\n"
        f"Class: {entry.get('class_name', '')}\n"
        f"SI card: {card}\n\n"
        f"See you there!\n"
    )
    if not is_enabled():
        log.info("[email disabled] would send %r to %s", subject, to)
        return False
    return _send(to, subject, body)


def _send(to: str, subject: str, body: str) -> bool:
    host = os.environ["BMEOS_SMTP_HOST"]
    port = int(os.environ.get("BMEOS_SMTP_PORT", "587"))
    user = os.environ.get("BMEOS_SMTP_USER")
    password = os.environ.get("BMEOS_SMTP_PASS")
    sender = os.environ.get("BMEOS_SMTP_FROM", user or "no-reply@better-meos.local")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if user:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as err:  # pragma: no cover - network/credentials
        log.error("email send failed: %s", err)
        return False
