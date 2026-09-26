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


def send_order_receipt(order: dict, results: list[dict], event: dict) -> bool:
    """
    Email the receipt for a verified online payment. Built only from what the
    server recorded (the order row + who actually got entered), so it can't be
    used to send arbitrary text. Returns True if actually sent.
    """
    to = (order.get("email") or "").strip()
    if not _valid_email(to):
        return False
    lines = []
    for r in results:
        status = "Entered" if r.get("ok") else \
            f"NOT entered ({r.get('detail', '')}); the organiser will refund this entry"
        lines.append(f"  {r.get('name', '')} ({r.get('className', '')}): {status}")
    body = (
        f"Your entries for {event.get('name', '')} ({event.get('date', '')}):\n\n"
        + "\n".join(lines)
        + f"\n\nTotal paid: {order.get('amount')} {order.get('currency')}\n"
        f"PayPal reference: {order.get('capture_id') or order.get('paypal_order_id')}\n\n"
        "If anything looks wrong, contact the organiser and quote the PayPal "
        "reference above.\n"
    )
    subject = f"Entry receipt: {event.get('name', '')}"
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
