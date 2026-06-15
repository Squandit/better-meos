"""
Entry payments (Stripe Checkout).

Scaffold: when ``STRIPE_SECRET_KEY`` is set a real Checkout session is created;
otherwise registration proceeds with a logged "payments disabled" no-op so the
flow works without a Stripe account. The entry fee is configurable.

Config:
    STRIPE_SECRET_KEY              enables real payments
    BMEOS_ENTRY_FEE_CENTS          fee in minor units (default 0 = free)
    BMEOS_CURRENCY                 ISO currency (default "aud")
"""

from __future__ import annotations

import logging
import os

import config

log = logging.getLogger("payments")


def is_enabled() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


# ---------------------------------------------------------------------------
# PayPal (used by the ported entry page; capture happens client-side in the
# browser SDK, so the server only needs to expose the public config + pricing).
# All secrets/config come from env -- nothing is committed.
# ---------------------------------------------------------------------------

def paypal_config() -> dict:
    """Public PayPal config for the entry page (safe to embed in the page).

    Values come from the Settings dashboard (config.json) with env fallback."""
    return {
        "clientId": config.get_str("paypal_client_id"),
        "currency": config.get_str("currency") or "AUD",
        # Sandbox unless explicitly turned off, so a missing/test config never
        # charges real money.
        "sandbox": bool(config.get("paypal_sandbox")),
    }


def prices() -> dict:
    """Entry fees by membership type + family cap (from the Settings dashboard /
    env fallback)."""
    return {
        "senior": float(config.get("fee_senior") or 0.0),
        "junior": float(config.get("fee_junior") or 0.0),
        "concession": float(config.get("fee_concession") or 0.0),
        "familyCap": float(config.get("family_cap") or 0.0),
    }


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
