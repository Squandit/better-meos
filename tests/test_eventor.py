"""Eventor API: connection check, event list, entries in (and again for late
entries), members into the runner database, results out. Eventor's answers
are faked with the XML shapes from its API documentation."""

import xml.etree.ElementTree as ET

import pytest

import app as appmod
import eventor
import runners
import store

ORG = """<?xml version="1.0" encoding="utf-8"?>
<Organisation><OrganisationId>742</OrganisationId><Name>Bibbulmun Orienteers</Name>
<ShortName>BO</ShortName></Organisation>"""

EVENTS = """<EventList>
<Event eventForm="IndSingleDay"><EventId>20412</EventId><Name>Kings Park Sprint</Name>
  <Organiser><Organisation><OrganisationId>742</OrganisationId><Name>Bibbulmun Orienteers</Name></Organisation></Organiser>
  <StartDate><Date>2026-10-11</Date><Clock>09:00:00</Clock></StartDate></Event>
<Event><EventId>20399</EventId><Name>Club Night</Name>
  <StartDate><Date>2026-10-02</Date><Clock>18:00:00</Clock></StartDate></Event>
</EventList>"""

CLASSES = """<EventClassList>
<EventClass><EventClassId>501</EventClassId><Name>M21A</Name><ClassShortName>M21A</ClassShortName></EventClass>
<EventClass><EventClassId>502</EventClassId><Name>W45</Name><ClassShortName>W45</ClassShortName></EventClass>
</EventClassList>"""


def entry(n, given, family, cls, card=None, club="BO"):
    card_xml = f"<CCard><CCardId>{card}</CCardId><PunchingUnitType value='SI'/></CCard>" if card else ""
    return f"""<Entry><EntryId>{n}</EntryId>
<Competitor><CompetitorId>{n}</CompetitorId>
  <Person sex="F"><PersonName><Family>{family}</Family><Given sequence="1">{given}</Given></PersonName>
    <PersonId>{9000 + n}</PersonId></Person>
  <Organisation><OrganisationId>742</OrganisationId><Name>Bibbulmun Orienteers</Name><ShortName>{club}</ShortName></Organisation>
  {card_xml}</Competitor>
<EntryClass sequence="1"><EventClassId>{cls}</EventClassId></EntryClass>
<EventId>20412</EventId><EntryDate><Date>2026-09-20</Date><Clock>10:00:00</Clock></EntryDate></Entry>"""


MEMBERS = """<?xml version="1.0" encoding="UTF-8"?>
<CompetitorList xmlns="http://www.orienteering.org/datastandard/3.0" iofVersion="3.0">
<Competitor><Person><Name><Family>Member</Family><Given>Maisie</Given></Name></Person>
  <Organisation><Name>Bibbulmun Orienteers</Name></Organisation>
  <ControlCard punchingSystem="SI">9771001</ControlCard></Competitor>
</CompetitorList>"""


@pytest.fixture
def api(cfg, monkeypatch):
    """Eventor set up, answering from ``api.answers`` (path -> XML)."""
    cfg.save({"eventor_api_key": "k3y"})

    class Fake:
        answers = {"organisation/apiKey": ORG, "events": EVENTS, "eventclasses": CLASSES,
                   "export/competitors": MEMBERS,
                   "entries": "<EntryList>" + entry(1, "Ada", "Api", 501, 9770001)
                              + entry(2, "Bea", "Nocard", 501) + entry(3, "Cat", "Vet", 502, 9770003)
                              + "</EntryList>"}
        calls = []
        posted = []

    def fake_get(url, headers, timeout=30.0):
        Fake.calls.append((url, headers))
        path = url.split("/api/", 1)[1].split("?")[0]
        return Fake.answers[path].encode()

    def fake_post(url, headers, body, timeout=60.0):
        Fake.posted.append((url, headers, body.decode()))
        return (b"<ImportResultListResult><ResultListUrl>https://eventor.example/r/1"
                b"</ResultListUrl></ImportResultListResult>")

    monkeypatch.setattr(eventor, "_http_get", fake_get)
    monkeypatch.setattr(eventor, "_http_post", fake_post)
    yield Fake
    for comp in list(store._competitors.values()):
        if comp["card_number"] in (9770001, 9770003) or comp["name"] in ("Bea Nocard", "Cat Vet"):
            store.delete_competitor(comp["id"])
    for cls in store.class_options():
        if cls["name"] == "W45":
            store.delete_class(cls["id"])


def test_connection_check_names_the_club(api):
    r = appmod.app.test_client().post("/api/eventor/test")
    assert r.get_json()["club"]["name"] == "Bibbulmun Orienteers"
    url, headers = api.calls[-1]
    assert url == "https://eventor.orienteering.asn.au/api/organisation/apiKey"
    assert headers["ApiKey"] == "k3y"


def test_event_list_is_the_clubs_events_soonest_first(api):
    events = appmod.app.test_client().get("/api/eventor/events").get_json()["events"]
    assert [e["id"] for e in events] == ["20399", "20412"]
    assert events[1]["name"] == "Kings Park Sprint" and events[1]["date"] == "2026-10-11"
    assert "organisationIds=742" in api.calls[-1][0]


def test_parse_eventor_entries():
    rows = eventor.parse_eventor_entries(
        ET.fromstring("<EntryList>" + entry(1, "Ada", "Api", 501, 9770001) + "</EntryList>"),
        [{"id": "501", "name": "M21A", "short_name": "M21A"}])
    assert rows == [{"name": "Ada Api", "club": "BO", "class_name": "M21A",
                     "card_number": 9770001, "start": None, "eventor_class_id": "501"}]


def test_fetch_entries_and_again_for_late_entries(api):
    c = appmod.app.test_client()
    c.post("/api/eventor/link", json={"event_id": "20412"})
    d = c.post("/api/eventor/fetch", json={}).get_json()
    assert d["created"] == 2 and d["already"] == 0
    assert d["missing_classes"] == [{"name": "W45", "entries": 1}]
    assert store.find_by_card(9770001)["name"] == "Ada Api"
    # A late entry arrives; fetching again adds only that one.
    api.answers["entries"] = api.answers["entries"].replace(
        "</EntryList>", entry(4, "Dot", "Late", 501, 9770004) + "</EntryList>")
    d = c.post("/api/eventor/fetch", json={}).get_json()
    assert d["created"] == 1 and d["already"] == 2
    store.delete_competitor(store.find_by_card(9770004)["id"])


def test_fetch_can_make_the_missing_classes(api):
    course = store.course_options()[0]["id"]
    d = appmod.app.test_client().post(
        "/api/eventor/fetch", json={"event_id": "20412", "course_id": course}).get_json()
    assert d["new_classes"] == ["W45"] and d["created"] == 3 and d["missing_classes"] == []


def test_members_fill_the_runner_database(api):
    d = appmod.app.test_client().post("/api/eventor/members").get_json()
    assert d["imported"] == 1
    assert runners.lookup(9771001)["name"] == "Maisie Member"
    assert "version=3.0" in api.calls[-1][0]


def test_upload_results_uses_eventors_ids(api):
    c = appmod.app.test_client()
    assert c.post("/api/eventor/upload-results").status_code == 400     # no event linked
    c.post("/api/eventor/link", json={"event_id": "20412"})
    d = c.post("/api/eventor/upload-results").get_json()
    assert d["result_url"] == "https://eventor.example/r/1"
    url, headers, body = api.posted[-1]
    assert url.endswith("/api/import/resultlist") and headers["ApiKey"] == "k3y"
    root = ET.fromstring(body)
    ns = {"i": "http://www.orienteering.org/datastandard/3.0"}
    assert root.find("i:Event/i:Id", ns).text == "20412"
    m21a = [cr for cr in root.findall("i:ClassResult", ns)
            if cr.find("i:Class/i:Name", ns).text == "M21A"][0]
    assert m21a.find("i:Class/i:Id", ns).text == "501"


def test_errors_read_well(api, cfg):
    import urllib.error

    def refuse(url, headers, timeout=30.0):
        raise urllib.error.HTTPError(url, 401, "no", {}, None)

    eventor._http_get = refuse
    r = appmod.app.test_client().post("/api/eventor/test")
    assert r.status_code == 400 and "refused the API key" in r.get_json()["error"]
    cfg.save({"eventor_base_url": "http://eventor.example"})
    assert "https" in appmod.app.test_client().post("/api/eventor/test").get_json()["error"]


def test_page(api):
    html = appmod.app.test_client().get("/eventor").get_data(as_text=True)
    assert "Fetch entries" in html and "Upload results to Eventor" in html
