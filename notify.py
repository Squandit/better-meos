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
import re
import smtplib
from email.message import EmailMessage

import config

log = logging.getLogger("notify")

# Basic, conservative recipient check; also rejects header-injection newlines.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(addr: str) -> bool:
    return bool(addr) and "\n" not in addr and "\r" not in addr and bool(_EMAIL_RE.match(addr))


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_enabled() -> bool:
    return bool(config.get_str("smtp_host"))


def send_entry_confirmation(entry: dict, event: dict) -> bool:
    """Send (or log) an entry-confirmation email. Returns True if actually sent."""
    to = (entry.get("email") or "").strip()
    if not _valid_email(to):
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


def send_entry_receipt(data: dict, event: dict) -> bool:
    """Send a paid-entry receipt from the entry page (entries list + total +
    PayPal reference). Returns True if actually sent; logs a no-op when SMTP
    isn't configured."""
    to = (data.get("email") or "").strip()
    if not _valid_email(to):
        return False
    lines = []
    for e in data.get("entries", []):
        if not isinstance(e, dict):
            continue
        status = "Confirmed" if e.get("ok") else f"Error: {e.get('detail', '')}"
        lines.append(f"  {e.get('name', '')} — {e.get('className', '')} "
                     f"(${_num(e.get('price')):.2f}) — {status}")
    total = _num(data.get("total"))
    body = (
        f"Your entries for {event.get('name', '')} have been received:\n\n"
        + "\n".join(lines)
        + f"\n\nTotal paid: ${total:.2f}\n"
        f"PayPal reference: {data.get('paypalId', '')}\n\n"
        "If you have any issues, contact the organiser and quote the PayPal "
        "reference above as proof of payment.\n"
    )
    subject = f"Entry confirmation — {event.get('name', '')}"
    if not is_enabled():
        log.info("[email disabled] would send receipt %r to %s", subject, to)
        return False
    return _send(to, subject, body)


def _send(to: str, subject: str, body: str) -> bool:
    host = config.get_str("smtp_host")
    port = int(config.get("smtp_port") or 587)
    user = config.get_str("smtp_user") or None
    password = config.get_str("smtp_pass") or None
    sender = config.get_str("smtp_from") or user or "no-reply@better-meos.local"

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
