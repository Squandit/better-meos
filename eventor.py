"""
Eventor (the federation's event system) integration.

With the club's API key (Settings -> Eventor) the app can:

* check the key and say which club it belongs to (``/organisation/apiKey``);
* list the club's events so one can be linked to this event (``/events``);
* fetch the event's entries (``/entries``) with their classes
  (``/eventclasses``), and fetch them again later for late entries without
  doubling anyone up;
* load the club's members and their SI cards into the runner database
  (``/export/competitors``, IOF XML 3.0) for card -> name autofill;
* upload the results (``/import/resultlist``, IOF XML 3.0).

Endpoints and XML shapes follow the Eventor API documentation (mirrored as an
OpenAPI spec at github.com/orienteering-oss/eventor-api-openapi-spec). Most
answers use Eventor's own XML (IOF 2.0 style, no namespace); the member export
and the result upload use IOF XML 3.0. The requests are sent with the
``ApiKey`` header, over https only.

Entries can also come from a file: Eventor's IOF XML 3.0 EntryList export,
read by :func:`parse_entrylist`.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import config
import iofxml

DEFAULT_BASE_URL = "https://eventor.orienteering.asn.au"


class EventorError(Exception):
    """Shown to the operator as-is."""


def base_url() -> str:
    return (config.get_str("eventor_base_url") or DEFAULT_BASE_URL).rstrip("/")


def is_enabled() -> bool:
    return bool(config.get_str("eventor_api_key"))


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_get(url: str, headers: dict, timeout: float = 30.0) -> bytes:
    """One HTTPS GET (split out so tests can replace it)."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_post(url: str, headers: dict, body: bytes, timeout: float = 60.0) -> bytes:
    """One HTTPS POST (split out so tests can replace it)."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _url(path: str, params: dict | None = None) -> str:
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items()
                                    if v is not None})
    return f"{base_url()}/api/{path.lstrip('/')}" + (f"?{query}" if query else "")


def _check_ready() -> None:
    if not is_enabled():
        raise EventorError("Add your club's Eventor API key in Settings (Eventor) first")
    if not base_url().startswith("https://"):
        raise EventorError("The Eventor address must start with https:// (the API key "
                           "would otherwise travel unencrypted)")


def _call(send) -> ET.Element:
    """Run one request and parse the XML answer, turning failures into
    messages an operator can act on."""
    _check_ready()
    try:
        body = send({"ApiKey": config.get_str("eventor_api_key"),
                     "Accept": "application/xml"})
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise EventorError("Eventor refused the API key (check it in Settings, "
                               "and that it's for this Eventor)")
        if err.code == 404:
            raise EventorError("Eventor doesn't know that (check the event id)")
        raise EventorError(f"Eventor answered HTTP {err.code}")
    except (urllib.error.URLError, OSError) as err:
        raise EventorError(f"Could not reach Eventor at {base_url()} "
                           f"({getattr(err, 'reason', err)}). Is this computer online?")
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        raise EventorError("Eventor's answer wasn't XML (is the Eventor address right?)")


def _get(path: str, params: dict | None = None) -> ET.Element:
    return _call(lambda headers: _http_get(_url(path, params), headers))


# ---------------------------------------------------------------------------
# Eventor XML helpers (no namespace, except in IOF 3.0 answers)
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(elem, name):
    if elem is None:
        return None
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


def _children(elem, name):
    return [c for c in elem if _local(c.tag) == name] if elem is not None else []


def _text(elem, *path) -> str:
    for name in path:
        elem = _child(elem, name)
    return (elem.text or "").strip() if elem is not None and elem.text else ""


def _person_name(person) -> str:
    name = _child(person, "PersonName")
    given = " ".join((g.text or "").strip() for g in _children(name, "Given") if g.text)
    return " ".join(p for p in (given, _text(name, "Family")) if p)


def _org(elem) -> dict:
    return {"id": _text(elem, "OrganisationId"), "name": _text(elem, "Name"),
            "short_name": _text(elem, "ShortName")}


# ---------------------------------------------------------------------------
# What the app asks Eventor
# ---------------------------------------------------------------------------

def whoami() -> dict:
    """The club the API key belongs to: ``{"id", "name", "short_name"}``."""
    root = _get("organisation/apiKey")
    org = root if _local(root.tag) == "Organisation" else _child(root, "Organisation")
    if org is None or not _text(org, "OrganisationId"):
        raise EventorError("Eventor didn't say which club the key belongs to")
    return _org(org)


def list_events(org_id: str | None = None, *, days_back: int = 30,
                days_ahead: int = 180, today: date | None = None) -> list[dict]:
    """The club's events around today, soonest first:
    ``[{"id", "name", "date", "organisers"}]``."""
    today = today or date.today()
    org_id = org_id or whoami()["id"]
    root = _get("events", {
        "organisationIds": org_id,
        "fromDate": f"{today - timedelta(days=days_back)} 00:00:00",
        "toDate": f"{today + timedelta(days=days_ahead)} 23:59:59",
    })
    out = []
    for ev in _children(root, "Event"):
        organisers = [_text(o, "Name") or _text(o, "OrganisationId")
                      for o in _children(_child(ev, "Organiser"), "Organisation")]
        out.append({"id": _text(ev, "EventId"), "name": _text(ev, "Name"),
                    "date": _text(ev, "StartDate", "Date"),
                    "organisers": [o for o in organisers if o]})
    out.sort(key=lambda e: (e["date"], e["name"]))
    return out


def event_classes(event_id) -> list[dict]:
    """An event's classes: ``[{"id", "name", "short_name"}]``."""
    root = _get("eventclasses", {"eventId": _event_id(event_id)})
    return [{"id": _text(c, "EventClassId"), "name": _text(c, "Name"),
             "short_name": _text(c, "ClassShortName")}
            for c in _children(root, "EventClass")]


def _event_id(event_id) -> str:
    event_id = str(event_id or "").strip()
    if not event_id.isdigit():
        raise EventorError("The Eventor event id is the number in the event's "
                           "Eventor address (…/Events/Show/12345)")
    return event_id


def parse_eventor_entries(root: ET.Element, classes: list[dict]) -> list[dict]:
    """Eventor's EntryList (IOF 2.0 style) as competitor rows:
    ``[{"name", "club", "class_name", "card_number", "start", "eventor_class_id"}]``.
    Team entries (relays) come out as one row per team member."""
    class_name = {c["id"]: c["name"] or c["short_name"] for c in classes}
    rows = []
    for entry in _children(root, "Entry"):
        class_ids = [_text(ec, "EventClassId") for ec in _children(entry, "EntryClass")]
        cls = next((class_name.get(i) for i in class_ids if class_name.get(i)), "")
        if not cls:
            ec = _child(entry, "EntryClass")
            cls = _text(ec, "ClassShortName") or _text(ec, "EventClass", "Name")
        people = _children(entry, "Competitor") or _children(entry, "TeamCompetitor")
        for comp in people:
            person = _child(comp, "Person")
            org = _child(comp, "Organisation")
            card = next((_text(c, "CCardId") for c in _children(comp, "CCard")
                         if _text(c, "CCardId").isdigit()), "")
            rows.append({
                "name": _person_name(person) if person is not None else "",
                "club": (_text(org, "ShortName") or _text(org, "Name")) if org is not None else "",
                "class_name": cls,
                "card_number": int(card) if card else None,
                "start": None,
                "eventor_class_id": class_ids[0] if class_ids else None,
            })
    return rows


def fetch_entries(event_id) -> list[dict]:
    """Pull an event's entries (with class names) from Eventor."""
    event_id = _event_id(event_id)
    _check_ready()
    classes = event_classes(event_id)
    root = _get("entries", {"eventIds": event_id, "includePersonElement": "true",
                            "includeOrganisationElement": "true"})
    if root.tag.startswith("{"):          # an IOF XML 3.0 answer
        return iofxml.parse_entrylist(ET.tostring(root))
    return parse_eventor_entries(root, classes)


def fetch_members(org_id: str | None = None) -> list[dict]:
    """The club's members and their SI cards (IOF XML 3.0 CompetitorList):
    ``[{"name", "club", "card_number"}]``."""
    org_id = org_id or whoami()["id"]
    root = _get("export/competitors", {"organisationIds": org_id, "version": "3.0"})
    try:
        return iofxml.parse_competitorlist(ET.tostring(root))
    except ValueError as err:
        raise EventorError(f"Unexpected member list from Eventor: {err}")


def upload_results(xml: str) -> dict:
    """Send an IOF XML 3.0 ResultList. Eventor answers with the links to the
    published result and split lists: ``{"result_url", "splits_url"}``."""
    root = _call(lambda headers: _http_post(
        _url("import/resultlist"), {**headers, "Content-Type": "application/xml"},
        xml.encode("utf-8")))
    return {"result_url": _text(root, "ResultListUrl"),
            "splits_url": _text(root, "SplitTimeListUrl")}


# ---------------------------------------------------------------------------
# Into the event
# ---------------------------------------------------------------------------

def import_entries(rows: list[dict], *, course_id=None) -> dict:
    """
    Add fetched entries to the event. Safe to repeat for late entries: anyone
    already entered (same SI card, or same name in the same class) is left
    alone. Entries in a class the event doesn't have are skipped, unless
    ``course_id`` is given: then the missing classes are made on that course
    (change their courses on the Classes page later).

    Returns ``{"created", "already", "new_classes", "missing_classes", "skipped"}``.
    """
    import importers
    import store

    known = {c["name"].lower() for c in store.class_options()}
    missing = sorted({r["class_name"] for r in rows
                      if r.get("class_name") and r["class_name"].lower() not in known},
                     key=str.lower)
    new_classes = []
    if missing and course_id:
        for name in missing:
            store.create_class({"name": name, "course_id": course_id})
            new_classes.append(name)
        missing = []

    by_card = {c["card_number"] for c in store._competitors.values() if c["card_number"]}
    class_names = {c["id"]: c["name"].lower() for c in store.class_options()}
    by_name = {(c["name"].strip().lower(), class_names.get(c["class_id"]))
               for c in store._competitors.values()}
    waiting = {m.lower(): 0 for m in missing}     # entries per class we don't have
    fresh, already = [], 0
    for row in rows:
        cls = (row.get("class_name") or "").lower()
        key = (row.get("name", "").strip().lower(), cls)
        if (row.get("card_number") and row["card_number"] in by_card) or key in by_name:
            already += 1
        elif cls in waiting:
            waiting[cls] += 1
        else:
            fresh.append(row)
    outcome = importers.import_competitors(fresh)
    return {"created": outcome["created"], "already": already,
            "new_classes": new_classes,
            "missing_classes": [{"name": m, "entries": waiting[m.lower()]} for m in missing],
            "skipped": outcome["skipped"]}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def parse_entrylist(xml) -> list[dict]:
    """Parse an IOF XML 3.0 EntryList (Eventor's file export) into competitor rows."""
    return iofxml.parse_entrylist(xml)
