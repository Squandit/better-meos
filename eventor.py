"""
Eventor (national federation) integration -- scaffold.

Two ways entries arrive from Eventor:

* **File**: Eventor exports a standard IOF XML ``EntryList``; :func:`parse_entrylist`
  reads it (fully implemented -- it's just IOF XML, handled by :mod:`iofxml`).
* **API**: a live pull from the Eventor REST API, gated behind ``EVENTOR_API_KEY``.
  That path needs the federation's API host + key, which can't be exercised here,
  so :func:`fetch_entries` is a documented stub that activates once configured.

Config:
    EVENTOR_API_KEY     enables the live API pull
    EVENTOR_BASE_URL    federation API host (e.g. https://eventor.orienteering.asn.au)
"""

from __future__ import annotations

import os

import iofxml


def is_enabled() -> bool:
    return bool(os.environ.get("EVENTOR_API_KEY"))


def parse_entrylist(xml) -> list[dict]:
    """Parse an IOF XML EntryList (Eventor's export) into competitor rows."""
    return iofxml.parse_entrylist(xml)


def fetch_entries(event_id: str) -> list[dict]:  # pragma: no cover - needs API key
    """
    Pull entries for an Eventor event via the REST API (scaffold).

    Activates when ``EVENTOR_API_KEY`` is set; until then, export the EntryList
    XML from Eventor and import that instead.
    """
    if not is_enabled():
        raise RuntimeError(
            "Eventor API not configured (set EVENTOR_API_KEY); "
            "export the EntryList XML from Eventor and import the file instead")
    # A real implementation would:
    #   GET {EVENTOR_BASE_URL}/api/entries?eventId={event_id}
    #   headers {"ApiKey": EVENTOR_API_KEY}
    # then return parse_entrylist(response_body).
    raise NotImplementedError("Eventor live API fetch is scaffolded")
