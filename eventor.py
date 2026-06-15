"""
Eventor (national federation) integration -- scaffold.

Two ways entries arrive from Eventor:

* **File**: Eventor exports a standard IOF XML ``EntryList``; :func:`parse_entrylist`
  reads it (fully implemented -- it's just IOF XML, handled by :mod:`iofxml`).
* **API**: a live pull from the Eventor REST API, gated behind ``EVENTOR_API_KEY``.
  That path needs the federation's API host + key, which can't be exercised here,
  so :func:`fetch_entries` is a documented stub that activates once configured.

Config (Settings dashboard / config.json, env as fallback):
    eventor_api_key     enables the live API (EVENTOR_API_KEY)
    eventor_base_url    federation API host (EVENTOR_BASE_URL),
                        e.g. https://eventor.orienteering.asn.au

Uploading results is fully implemented (a real POST), but can only run against a
live Eventor with valid credentials, so it isn't exercised by the test suite.
"""

from __future__ import annotations

import urllib.parse
import urllib.request

import config
import iofxml


def is_enabled() -> bool:
    return bool(config.get_str("eventor_api_key"))


def _credentials() -> tuple[str, str]:
    key = config.get_str("eventor_api_key")
    base = config.get_str("eventor_base_url").rstrip("/")
    if not key or not base:
        raise RuntimeError(
            "Eventor isn't configured -- set the Eventor API key and base URL in "
            "Settings before uploading.")
    return key, base


def parse_entrylist(xml) -> list[dict]:
    """Parse an IOF XML EntryList (Eventor's export) into competitor rows."""
    return iofxml.parse_entrylist(xml)


def _get(path: str, params: dict, timeout: float) -> str:
    key, base = _credentials()
    url = base + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"ApiKey": key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def fetch_entries(event_id, *, timeout: float = 20.0) -> list[dict]:
    """
    Pull an event's entries straight from the Eventor API (the way MeOS does it).

    Calls ``GET {base}/api/entries?eventId={event_id}`` with the ApiKey header and
    returns the same normalised rows as :func:`parse_entrylist`, so the importer
    can create competitors (and, on the Eventor path, auto-create their classes).
    Gated behind the Eventor API key + base URL; raises ``RuntimeError`` when not
    configured or no event id is given.
    """
    if not event_id:
        raise RuntimeError("No Eventor event id set -- add it in Settings.")
    xml = _get("/api/entries", {"eventId": str(event_id)}, timeout)
    return parse_entrylist(xml)


def upload_results(results_xml: str, *, timeout: float = 15.0) -> dict:
    """
    POST an IOF XML ResultList to Eventor's results endpoint.

    Gated behind the Eventor API key + base URL (Settings). Returns
    ``{"status", "body"}`` from the response, or raises ``RuntimeError`` when not
    configured / ``urllib`` errors on a network or HTTP failure.
    """
    key, base = _credentials()
    req = urllib.request.Request(
        base + "/api/results", data=results_xml.encode("utf-8"),
        headers={"ApiKey": key, "Content-Type": "application/xml"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return {"status": resp.getcode(),
                "body": resp.read().decode("utf-8", "replace")[:500]}


def fetch_entries(event_id: str) -> list[dict]:  # pragma: no cover - needs API key
    """
    Pull entries for an Eventor event via the REST API (scaffold).

    Activates when an Eventor API key is set; until then, export the EntryList
    XML from Eventor and import that instead.
    """
    _credentials()  # raises a clear message when unconfigured
    # A real implementation would GET {base}/api/entries?eventId={event_id}
    # with the ApiKey header, then return parse_entrylist(response_body).
    raise NotImplementedError("Eventor live API fetch is scaffolded")
