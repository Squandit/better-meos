"""MEOS-parity features: punch start, geometry, relay, economy, bibs, printing."""

from datetime import datetime

import app as appmod
import iofxml
import store
from results import build_result, build_splits_matrix


def t(h, m, s=0):
    return datetime(2026, 5, 17, h, m, s)


def test_punch_start_derives_start_from_punch():
    course = {"type": "linear", "controls": [31, 32],
              "start_mode": "punch", "start_control": 1}
    r = build_result({"id": 1, "name": "P", "class": "X", "start": None,
                      "finish": t(9, 30),
                      "punches": [(1, t(9, 0)), (31, t(9, 5)), (32, t(9, 12))]},
                     course)
    assert r["start"] == t(9, 0)        # derived from the start punch
    assert r["status"] == "ok"          # start punch removed, 31/32 still match
    assert r["total_seconds"] == 1800


def test_velocity_in_splits_matrix():
    a = build_result({"id": 1, "name": "A", "class": "X", "start": t(9, 0),
                      "finish": t(9, 20), "punches": [(31, t(9, 5))]},
                     {"type": "linear", "controls": [31]})
    m = build_splits_matrix([a], [31], leg_lengths=[1000])  # 1 km leg in 5:00
    assert m["rows"][0]["cells"][0]["velocity"] == "5:00"   # 5:00 /km


def test_iofxml_parses_course_geometry():
    xml = ('<CourseData xmlns="http://www.orienteering.org/datastandard/3.0">'
           '<RaceCourseData><Course><Name>C</Name><Length>4800</Length>'
           '<CourseControl type="Control"><Control>31</Control><LegLength>520</LegLength></CourseControl>'
           '<CourseControl type="Control"><Control>32</Control><LegLength>610</LegLength></CourseControl>'
           '</Course></RaceCourseData></CourseData>')
    courses = iofxml.parse_courses(xml)
    assert courses[0]["length_m"] == 4800
    assert courses[0]["leg_lengths"] == [520, 610]


def test_relay_team_results():
    course = store.create_course({"name": "Relay leg", "type": "linear", "controls": [210]})
    cls = store.create_class({"name": "RelayClass", "course_id": course["id"],
                              "kind": "relay", "legs": 2, "fee": 10})
    team = store.create_team({"name": "Team A", "class_id": cls["id"], "club": "ROC"})
    store.create_competitor({"name": "Leg One", "class_id": cls["id"], "card_number": 9200001,
                             "start": "10:00:00", "finish": "10:10:00",
                             "punches": [{"code": 210, "time": "10:05:00"}],
                             "team_id": team["id"], "leg": 1})
    store.create_competitor({"name": "Leg Two", "class_id": cls["id"], "card_number": 9200002,
                             "start": "10:10:00", "finish": "10:25:00",
                             "punches": [{"code": 210, "time": "10:18:00"}],
                             "team_id": team["id"], "leg": 2})
    entry = next(e for e in store.team_results() if e["class"]["name"] == "RelayClass")
    top = entry["teams"][0]
    assert top["position"] == 1
    assert top["total_seconds"] == 600 + 900   # 10 min + 15 min
    assert [leg["name"] for leg in top["legs"]] == ["Leg One", "Leg Two"]


def test_course_edit_preserves_leg_lengths():
    course = store.create_course({"name": "Geo course", "type": "linear",
                                  "controls": [41, 42], "length_m": 2000,
                                  "leg_lengths": [900, 1100]})
    # Editing via the UI payload (no leg_lengths sent) must NOT wipe them.
    store.update_course(course["id"], {"name": "Geo course v2", "type": "linear",
                                       "controls": [41, 42], "length_m": 2100})
    kept = store.get_course(course["id"])
    assert kept["leg_lengths"] == [900, 1100]
    assert kept["length_m"] == 2100


def test_punch_start_requires_start_control():
    try:
        store.create_course({"name": "Bad punch", "type": "linear",
                             "controls": [1, 2], "start_mode": "punch"})
        assert False, "expected StoreError"
    except store.StoreError as err:
        assert "start control" in str(err).lower()


def test_economy_summary():
    econ = store.economy_summary()
    row = next((r for r in econ["rows"] if r["class"] == "RelayClass"), None)
    assert row and row["entries"] == 2 and row["fee"] == 10 and row["subtotal"] == 20


def test_assign_bibs():
    n = store.assign_bibs(1)
    assert n >= 2
    bibs = [c.get("bib") for c in store._competitors.values()]
    assert all(b is not None for b in bibs)


def test_meos_feature_routes():
    c = appmod.app.test_client()
    for p in ["/teams", "/economy", "/speaker"]:
        assert c.get(p).status_code == 200
    assert c.get("/export/startlist.pdf").data[:4] == b"%PDF"
    assert c.get("/export/bibs.pdf").data[:4] == b"%PDF"
    assert c.post("/api/bibs/assign", json={"start": 1}).status_code == 200
