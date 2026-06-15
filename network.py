"""
Multi-computer operation: secondary download stations push to a primary.

MeOS shares a live event across many PCs by storing it in a MySQL server every
computer connects to. Punchcard keeps the file-per-event SQLite model -- the
primary instance holds the authoritative event file and computes every result --
and lets extra download stations *forward* the cards they read to that primary
over HTTP. A secondary therefore needs no event file of its own: it reads a card
and POSTs it to ``/api/station/push`` on the primary, which records it exactly as
it does for its own reader and broadcasts the live update to every screen.

Configure a secondary with ``BMEOS_PRIMARY=http://primary-host:8765``; leave it
unset on the primary. Pair it with ``BMEOS_READER`` so the secondary's SI station
runs, and the cards flow straight through to the primary.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import store

PUSH_PATH = "/api/station/push"


def primary_url() -> str | None:
    """The primary instance's base URL (env ``BMEOS_PRIMARY``), or None if this
    instance is itself the primary."""
    url = (os.environ.get("BMEOS_PRIMARY") or "").strip().rstrip("/")
    return url or None


def is_secondary() -> bool:
    """True when this instance should forward its reads to a primary."""
    return primary_url() is not None


def card_payload(card: dict) -> dict:
    """
    Serialise an internal card dict (datetimes, ``[(code, time), ...]`` punches)
    into the JSON shape ``store.coerce_card`` / ``/api/station/push`` accept.

    Times become ``HH:MM:SS`` clock strings -- the primary re-pins them to its own
    event date, the same as a typed or imported time.
    """
    return {
        "card_number": card.get("card_number"),
        "start": store.format_clock(card.get("start")),
        "finish": store.format_clock(card.get("finish")),
        "punches": [{"code": code, "time": store.format_clock(time)}
                    for code, time in card.get("punches", [])],
        "station_id": card.get("station_id"),
    }


def push_card(card: dict, *, url: str | None = None, timeout: float = 5.0) -> dict:
    """
    Forward one read card to the primary and return its JSON outcome.

    ``card`` is the internal card dict (as the reader/simulator build it). Raises
    ``RuntimeError`` if no primary is configured, or ``urllib`` errors on a
    network/HTTP failure so the caller can fall back to a local queue or retry.
    """
    base = (url or primary_url())
    if not base:
        raise RuntimeError("no primary configured (set BMEOS_PRIMARY)")
    data = json.dumps(card_payload(card)).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + PUSH_PATH, data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
