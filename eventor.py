"""
Eventor (national federation) integration.

Two ways entries arrive from Eventor:

* **File**: Eventor exports a standard IOF XML ``EntryList``; :func:`parse_entrylist`
  reads it (it's just IOF XML, handled by :mod:`iofxml`).
* **API**: :func:`fetch_entries` pulls the entry list straight from the
  federation's Eventor with the club's API key (Settings -> Eventor).

The API call follows Eventor's documented REST interface (``GET /api/entries``
with an ``ApiKey`` header). It hasn't been exercised against a live Eventor
here; if your Eventor answers in the older IOF XML 2.0 format, the error says
so and the file export still works.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import config
import iofxml


class EventorError(Exception):
    """Shown to the operator as-is."""


def is_enabled() -> bool:
    return bool(config.get_str("eventor_api_key") and config.get_str("eventor_base_url"))


def parse_entrylist(xml) -> list[dict]:
    """Parse an IOF XML EntryList (Eventor's export) into competitor rows."""
    return iofxml.parse_entrylist(xml)


def _http_get(url: str, headers: dict, timeout: float = 30.0) -> bytes:
    """One HTTPS GET (split out so tests can replace it)."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def entries_url(event_id: str) -> str:
    base = config.get_str("eventor_base_url").rstrip("/")
    query = urllib.parse.urlencode({
        "eventIds": event_id, "includePersonElement": "true",
        "includeOrganisationElement": "true", "includeEventElement": "false",
    })
    return f"{base}/api/entries?{query}"


def fetch_entries(event_id) -> list[dict]:
    """Pull an event's entries from Eventor and parse them into competitor rows."""
    if not is_enabled():
        raise EventorError("Set the Eventor address and API key in Settings first "
                           "(or export the EntryList XML from Eventor and import the file)")
    event_id = str(event_id or "").strip()
    if not event_id.isdigit():
        raise EventorError("The Eventor event id is the number in the event's Eventor URL")
    base = config.get_str("eventor_base_url")
    if not base.startswith("https://"):
        raise EventorError("The Eventor address must start with https:// (the API key "
                           "would otherwise travel unencrypted)")
    try:
        body = _http_get(entries_url(event_id),
                         {"ApiKey": config.get_str("eventor_api_key"),
                          "Accept": "application/xml"})
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise EventorError("Eventor refused the API key")
        raise EventorError(f"Eventor answered HTTP {err.code}")
    except (urllib.error.URLError, OSError) as err:
        raise EventorError(f"Could not reach Eventor: {err}")
    try:
        return iofxml.parse_entrylist(body)
    except ValueError as err:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise EventorError("Eventor's answer wasn't XML")
        if iofxml._tag(root) == "EntryList":
            raise EventorError("Eventor sent IOF XML 2.0, which isn't supported: export the "
                               "entry list as IOF XML 3.0 from Eventor and import the file")
        raise EventorError(f"Unexpected answer from Eventor: {err}")
