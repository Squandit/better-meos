"""Phase D scaffolds: Emit config, live radio punches, Eventor entry import."""

from datetime import datetime

import app as appmod
import eventor
import iofxml
import si_reader
import store


ENTRYLIST_XML = """<?xml version="1.0"?>
<EntryList xmlns="http://www.orienteering.org/datastandard/3.0" iofVersion="3.0">
  <PersonEntry>
    <Person><Name><Given>Evan</Given><Family>Tor</Family></Name></Person>
    <Organisation><Name>EVOC</Name></Organisation>
    <ControlCard>9300001</ControlCard>
    <Class><Name>M21A</Name></Class>
  </PersonEntry>
</EntryList>
"""


def test_emit_system_flag_default():
    # SportIdent by default; the simulator works regardless of system.
    assert si_reader.PUNCH_SYSTEM in ("sportident", "emit")
    out = si_reader.simulate({"card_number": 8500003, "punches": []})
    assert "ok" in out


def test_parse_eventor_entrylist():
    rows = eventor.parse_entrylist(ENTRYLIST_XML)
    assert rows[0]["name"] == "Evan Tor"
    assert rows[0]["card_number"] == 9300001
    assert rows[0]["class_name"] == "M21A"


def test_eventor_fetch_requires_key(monkeypatch):
    monkeypatch.delenv("EVENTOR_API_KEY", raising=False)
    assert eventor.is_enabled() is False
    try:
        eventor.fetch_entries("123")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_eventor_import_route():
    import io
    c = appmod.app.test_client()
    r = c.post("/api/import/eventor", data={
        "file": (io.BytesIO(ENTRYLIST_XML.encode()), "entries.xml")},
        content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["created"] == 1
    assert store.find_by_card(9300001)["name"] == "Evan Tor"


def test_radio_punch_adds_intermediate_split():
    # Register a competitor, then stream a radio-control punch into their card.
    cls_id = next(iter(store._classes))
    comp = store.create_competitor({"name": "Radio Ray", "class_id": cls_id,
                                    "card_number": 9300050, "start": "09:00:00"})
    before = len(store.get_competitor(comp["id"])["punches"])
    c = appmod.app.test_client()
    r = c.post("/api/radio/punch", json={"card_number": 9300050, "code": 99,
                                         "time": "09:07:00", "station_id": "radio1"})
    assert r.status_code == 200
    punches = store.get_competitor(comp["id"])["punches"]
    assert len(punches) == before + 1
    assert punches[-1]["code"] == 99 and punches[-1]["station_id"] == "radio1"


def test_radio_punch_unknown_card_400():
    c = appmod.app.test_client()
    r = c.post("/api/radio/punch", json={"card_number": 424299, "code": 99,
                                         "time": "09:07:00"})
    assert r.status_code == 400
