"""
Entry payments: PayPal (the online entry page) and a Stripe scaffold.

**PayPal** runs server-side through the Orders v2 REST API. The browser never
decides what anyone pays: the server prices the cart, creates the PayPal order
for that amount, and after the buyer approves it the server captures the order
and checks PayPal's answer (status, amount, currency, our reference) before any
entry is created. See ``online_entry.py`` for the flow; this module is only the
PayPal client plus pricing.

Needs the PayPal app's client id *and* secret (Settings -> Payments). Sandbox
is the default so a test setup can never take real money.

**Stripe** (``/api/entries``, operator-only) is still a scaffold: with
``STRIPE_SECRET_KEY`` set it creates a Checkout session, and entries are marked
paid by the operator.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import config

log = logging.getLogger("payments")


def is_enabled() -> bool:
    """Stripe scaffold on/off (unrelated to the PayPal entry page)."""
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


# ---------------------------------------------------------------------------
# PayPal (used by the ported entry page; capture happens client-side in the
# browser SDK, so the server only needs to expose the public config + pricing).
# All secrets/config come from env -- nothing is committed.
# ---------------------------------------------------------------------------

def currency() -> str:
    return (config.get_str("currency") or "AUD").strip().upper()


def paypal_config() -> dict:
    """Public PayPal config for the entry page (safe to embed in the page: the
    client id is public by design; the secret never leaves the server)."""
    return {
        "clientId": config.get_str("paypal_client_id"),
        "currency": currency(),
        "enabled": paypal_enabled(),
    }


def paypal_enabled() -> bool:
    """Online payment works only with both halves of the PayPal app's
    credentials: the id for the buyer's button, the secret for the server."""
    return bool(config.get_str("paypal_client_id") and config.get_str("paypal_client_secret"))


def prices() -> dict:
    """Entry fees by membership type + family cap (from the Settings dashboard /
    env fallback). Display only; the server prices carts with :func:`quote`."""
    return {
        "senior": float(config.get("fee_senior") or 0.0),
        "junior": float(config.get("fee_junior") or 0.0),
        "concession": float(config.get("fee_concession") or 0.0),
        "familyCap": float(config.get("family_cap") or 0.0),
    }


# --- Money ------------------------------------------------------------------
# Decimal throughout: float cents drift (0.1 + 0.2), and a payment check has to
# compare exactly. PayPal wants whole units for these currencies.
_ZERO_DECIMAL = {"HUF", "JPY", "TWD"}


def _exponent(code: str) -> Decimal:
    return Decimal("1") if code in _ZERO_DECIMAL else Decimal("0.01")


def to_money(value, code: str | None = None) -> Decimal:
    """A fee/amount as an exact Decimal rounded to the currency's minor unit.
    Negative or unparseable values count as 0."""
    code = code or currency()
    try:
        amount = Decimal(str(value if value is not None else 0))
    except InvalidOperation:
        return Decimal(0).quantize(_exponent(code))
    if not amount.is_finite() or amount < 0:
        amount = Decimal(0)
    return amount.quantize(_exponent(code), rounding=ROUND_HALF_UP)


def money_str(amount: Decimal, code: str | None = None) -> str:
    """Format for the PayPal API: '25.00' (or '25' for zero-decimal currencies)."""
    return str(to_money(amount, code))


def fee_for(member_type: str) -> Decimal:
    key = {"junior": "fee_junior", "concession": "fee_concession"}.get(
        member_type, "fee_senior")
    return to_money(config.get(key))


def quote(member_types: list[str]) -> dict:
    """
    Price a cart server-side: one fee per person by membership type, then the
    family cap. Returns Decimals: ``{"lines", "subtotal", "total", "capped"}``.
    """
    lines = [fee_for(t) for t in member_types]
    subtotal = sum(lines, Decimal(0))
    cap = to_money(config.get("family_cap"))
    total = min(subtotal, cap) if cap > 0 else subtotal
    return {"lines": lines, "subtotal": to_money(subtotal),
            "total": to_money(total), "capped": total < subtotal}


# --- PayPal REST client -----------------------------------------------------

class PayPalError(Exception):
    """PayPal could not be reached or refused the call. ``status`` is the HTTP
    status (None for a network failure); ``body`` the parsed error, if any."""

    def __init__(self, message: str, status: int | None = None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body or {}

    @property
    def definite_refusal(self) -> bool:
        """PayPal answered with a 4xx: the call was refused and nothing was
        captured. A network failure or 5xx leaves the outcome unknown."""
        return self.status is not None and 400 <= self.status < 500

    @property
    def issue(self) -> str:
        details = self.body.get("details") if isinstance(self.body, dict) else None
        if isinstance(details, list) and details and isinstance(details[0], dict):
            return str(details[0].get("issue") or "")
        return ""


def api_base() -> str:
    # Sandbox unless explicitly turned off, so a missing/test config never
    # charges real money.
    if config.get("paypal_sandbox") is False:
        return "https://api-m.paypal.com"
    return "https://api-m.sandbox.paypal.com"


_token_lock = threading.Lock()
_token: dict = {"value": None, "expires": 0.0, "key": None}


def _http(method: str, url: str, *, body: bytes | None, headers: dict,
          timeout: float = 20.0) -> tuple[int, dict]:
    """One HTTPS call; returns (status, parsed JSON). Raises PayPalError on a
    network failure. Split out so tests can replace it."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as err:
        try:
            parsed = json.loads(err.read().decode("utf-8") or "{}")
        except ValueError:
            parsed = {}
        return err.code, parsed
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        raise PayPalError(f"could not reach PayPal: {err}")


def _access_token() -> str:
    client_id = config.get_str("paypal_client_id")
    secret = config.get_str("paypal_client_secret")
    if not (client_id and secret):
        raise PayPalError("PayPal client id/secret are not configured")
    key = (client_id, api_base())
    with _token_lock:
        if _token["value"] and _token["key"] == key and time.time() < _token["expires"]:
            return _token["value"]
        basic = base64.b64encode(f"{client_id}:{secret}".encode("utf-8")).decode("ascii")
        status, data = _http(
            "POST", api_base() + "/v1/oauth2/token",
            body=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"})
        if status != 200 or not data.get("access_token"):
            raise PayPalError("PayPal rejected the client id/secret "
                              "(check them, and the sandbox setting)", status, data)
        _token.update(value=data["access_token"], key=key,
                      expires=time.time() + max(60, int(data.get("expires_in", 300)) - 120))
        return _token["value"]


def _api(method: str, path: str, payload: dict | None = None, *,
         request_id: str | None = None) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {_access_token()}",
               "Content-Type": "application/json", "Accept": "application/json",
               "Prefer": "return=representation"}
    if request_id:
        # Idempotency key: a retried create/capture with the same id returns the
        # original result instead of charging twice.
        headers["PayPal-Request-Id"] = request_id
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    return _http(method, api_base() + path, body=body, headers=headers)


def paypal_create_order(*, amount: Decimal, reference: str, description: str,
                        request_id: str) -> str:
    """Create a CAPTURE-intent order for exactly ``amount``; returns its id."""
    code = currency()
    status, data = _api("POST", "/v2/checkout/orders", {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": reference,
            "custom_id": reference,
            "description": description[:127],
            "amount": {"currency_code": code, "value": money_str(amount, code)},
        }],
        "application_context": {"shipping_preference": "NO_SHIPPING",
                                "user_action": "PAY_NOW"},
    }, request_id=request_id)
    if status not in (200, 201) or not data.get("id"):
        raise PayPalError("PayPal would not create the order", status, data)
    return data["id"]


def paypal_capture_order(order_id: str, *, request_id: str) -> dict:
    """Capture an approved order and return PayPal's order representation.
    An order that was already captured is fetched and returned as-is."""
    status, data = _api("POST", f"/v2/checkout/orders/{_safe_id(order_id)}/capture",
                        {}, request_id=request_id)
    if status in (200, 201):
        return data
    err = PayPalError("PayPal would not capture the payment", status, data)
    if err.issue == "ORDER_ALREADY_CAPTURED":
        try:
            return paypal_get_order(order_id)
        except PayPalError as get_err:
            # The money *was* captured; we just can't see it yet. Report an
            # unknown outcome (status None), never a refusal.
            raise PayPalError(f"captured, but could not read it back: {get_err}")
    raise err


def paypal_get_order(order_id: str) -> dict:
    status, data = _api("GET", f"/v2/checkout/orders/{_safe_id(order_id)}")
    if status != 200:
        raise PayPalError("PayPal could not find the order", status, data)
    return data


_ORDER_ID_RE = re.compile(r"[A-Za-z0-9]{1,64}")


def _safe_id(order_id: str) -> str:
    """PayPal order ids are short upper-case alphanumerics; refuse anything else
    rather than splice it into a URL path."""
    text = str(order_id or "")
    if not _ORDER_ID_RE.fullmatch(text):
        raise PayPalError("malformed PayPal order id")
    return text



def verify_capture(order: dict, *, amount: Decimal, reference: str,
                   order_id: str) -> dict:
    """
    Check a captured PayPal order really paid what we asked, for our order.

    Returns ``{"state": "paid"|"pending"|"failed", "capture_id", "reason"}``.
    ``paid`` only when it is the PayPal order we created (``order_id``), the
    order and its single capture are COMPLETED, for exactly ``amount`` in our
    currency, tagged with our ``reference``. ``pending`` means PayPal is
    holding the money for review (not yet ours).
    """
    code = currency()
    if not isinstance(order, dict) or order.get("id") != order_id:
        return {"state": "failed", "capture_id": None,
                "reason": "PayPal answered for a different order"}
    units = order.get("purchase_units")
    if not isinstance(units, list) or len(units) != 1:
        return {"state": "failed", "capture_id": None, "reason": "unexpected order shape"}
    unit = units[0] if isinstance(units[0], dict) else {}
    captures = ((unit.get("payments") or {}).get("captures")) or []
    if len(captures) != 1 or not isinstance(captures[0], dict):
        return {"state": "failed", "capture_id": None,
                "reason": f"expected one capture, got {len(captures)}"}
    cap = captures[0]
    capture_id = cap.get("id")
    tag = cap.get("custom_id") or unit.get("custom_id") or unit.get("reference_id")
    if tag != reference:
        return {"state": "failed", "capture_id": capture_id,
                "reason": "payment is for a different order"}
    paid = cap.get("amount") or {}
    if paid.get("currency_code") != code:
        return {"state": "failed", "capture_id": capture_id,
                "reason": f"paid in {paid.get('currency_code')}, expected {code}"}
    try:
        value = Decimal(str(paid.get("value")))
    except InvalidOperation:
        return {"state": "failed", "capture_id": capture_id, "reason": "unreadable amount"}
    if value != to_money(amount, code):
        return {"state": "failed", "capture_id": capture_id,
                "reason": f"paid {paid.get('value')}, expected {money_str(amount, code)}"}
    cap_status = cap.get("status")
    if cap_status == "COMPLETED" and order.get("status") == "COMPLETED":
        return {"state": "paid", "capture_id": capture_id, "reason": ""}
    if cap_status == "PENDING":
        reason = (cap.get("status_details") or {}).get("reason") or "held by PayPal"
        return {"state": "pending", "capture_id": capture_id, "reason": str(reason)}
    return {"state": "failed", "capture_id": capture_id,
            "reason": f"capture status {cap_status}"}


def fee_cents() -> int:
    return int(os.environ.get("BMEOS_ENTRY_FEE_CENTS", "0"))


def create_checkout(entry: dict, *, success_url: str, cancel_url: str) -> dict:
    """
    Start a payment for an entry.

    Returns ``{"enabled": bool, "url": str|None}``. With Stripe unconfigured (or
    a zero fee) it's a no-op returning ``enabled=False`` -- the entry stands
    without payment. With Stripe configured it returns the hosted Checkout URL to
    redirect the entrant to.
    """
    if not is_enabled() or fee_cents() <= 0:
        log.info("[payments disabled] entry %r accepted without payment",
                 entry.get("name"))
        return {"enabled": False, "url": None}

    import stripe  # imported lazily so the app runs without the dependency
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": (config.get_str("currency") or "aud").lower(),
                "unit_amount": fee_cents(),
                "product_data": {"name": "Event entry"},
            },
            "quantity": 1,
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"entry_name": entry.get("name", "")},
    )
    return {"enabled": True, "url": session.url}
