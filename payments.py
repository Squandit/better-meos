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

log = logging.getLogger("payments")


def is_enabled() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


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
                "currency": os.environ.get("BMEOS_CURRENCY", "aud"),
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
