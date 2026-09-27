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
    bibs = [c.get("bib") for c in store._competitors.values() if not c.get("vacant")]
    assert all(b is not None for b in bibs)


def test_meos_feature_routes():
    c = appmod.app.test_client()
    for p in ["/teams", "/economy", "/speaker"]:
        assert c.get(p).status_code == 200
    assert c.get("/export/startlist.pdf").data[:4] == b"%PDF"
    assert c.get("/export/bibs.pdf").data[:4] == b"%PDF"
    assert c.post("/api/bibs/assign", json={"start": 1}).status_code == 200


def test_linear_max_time_is_overtime():
    from datetime import datetime
    from results import build_result
    d = datetime(2026, 5, 17)
    course = {"type": "linear", "controls": [31], "time_limit_minutes": 60}
    card = {"name": "Slow", "class": "M", "start": d.replace(hour=10),
            "punches": [(31, d.replace(hour=10, minute=30))], "finish": d.replace(hour=11, minute=5)}
    assert build_result(card, course)["status"] == "oot"
    card["finish"] = d.replace(hour=10, minute=55)
    assert build_result(card, course)["status"] == "ok"


def test_max_time_round_trips_through_the_editor_api():
    import app as appmod
    c = appmod.app.test_client()
    r = c.post("/api/courses", json={"name": "Max T", "type": "linear", "controls": [31],
                                     "time_limit_minutes": "90"})
    cid = r.get_json()["course"]["id"]
    assert c.get(f"/api/courses/{cid}").get_json()["course"]["time_limit_minutes"] == 90
    assert "max 90 min" in store.course_meta(store.get_course(cid))


def test_not_competing_is_timed_but_unranked():
    cid = store.class_options()[0]["id"]
    comp = store.create_competitor({"name": "Guest Runner", "class_id": cid,
                                    "start": "10:00:00", "finish": "10:20:00",
                                    "manual_status": "nc"})
    res = store.result_for(comp["id"])
    assert res["status"] == "nc" and res["position"] is None and res["total_seconds"] == 1200


def test_vacant_slot_hidden_from_results_until_filled():
    import app as appmod
    cid = store.class_options()[0]["id"]
    with store.batch():
        cid_vac = store._insert_competitor(
            name=store.VACANT_NAME, club="", class_id=cid, card_number=None,
            start=store.parse_clock("11:11:00"), finish=None, punches=[],
            manual_status="", vacant=True)
    assert store.result_for(cid_vac) is None
    c = appmod.app.test_client()
    assert "11:11:00" in c.get("/competitors").get_data(as_text=True)
    store.update_competitor(cid_vac, {"name": "Late Larry", "card_number": 9800001})
    assert store.get_competitor(cid_vac)["vacant"] is False
    assert store.result_for(cid_vac)["name"] == "Late Larry"


def _course(name, controls):
    return store.create_course({"name": name, "type": "linear", "controls": controls})


def test_individual_forks_assigned_and_judged_on_own_course():
    import app as appmod
    a, b = _course("Fork A", [31, 32]), _course("Fork B", [32, 31])
    cls = store.create_class({"name": "Forked", "course_id": a["id"],
                              "fork_courses": [a["id"], b["id"]]})
    r1 = store.create_competitor({"name": "F1", "class_id": cls["id"], "start": "10:00:00",
                                  "finish": "10:30:00",
                                  "punches": [{"code": 32, "time": "10:10:00"},
                                              {"code": 31, "time": "10:20:00"}]})
    r2 = store.create_competitor({"name": "F2", "class_id": cls["id"], "start": "10:02:00"})
    c = appmod.app.test_client()
    assert c.post(f"/api/classes/{cls['id']}/forks").get_json()["assigned"] == 2
    assert store.get_competitor(r1["id"])["course_id"] == a["id"]
    assert store.get_competitor(r2["id"])["course_id"] == b["id"]
    # F1 ran the B order but was given fork A -> MP; give them B and it's OK.
    assert store.result_for(r1["id"])["status"] == "mp"
    html = c.get("/splits").get_data(as_text=True)
    assert "Forked · Fork A" in html and "Forked · Fork B" in html   # a table per fork
    store.update_competitor(r1["id"], {"course_id": b["id"]})
    assert store.result_for(r1["id"])["status"] == "ok"


def test_relay_forks_restart_and_leg_places():
    a, b = _course("Relay F1", [31]), _course("Relay F2", [32])
    cls = store.create_class({"name": "Relay Forks", "course_id": a["id"], "kind": "relay",
                              "legs": 2, "fork_courses": f"{a['id']},{b['id']}",
                              "restart": "12:40:00"})
    t1 = store.create_team({"name": "T1", "class_id": cls["id"], "start": "12:00:00"})
    t2 = store.create_team({"name": "T2", "class_id": cls["id"], "start": "12:00:00"})
    def run(team, leg, finish, code, punch):
        return store.create_competitor({"name": f"{team['name']}L{leg}", "class_id": cls["id"],
                                        "team_id": team["id"], "leg": leg, "finish": finish,
                                        "punches": [{"code": code, "time": punch}]})
    run(t1, 1, "12:20:00", 31, "12:10:00")
    run(t1, 2, "12:50:00", 32, "12:30:00")
    run(t2, 1, "12:45:00", 32, "12:30:00")   # slow leg 1, past the restart
    run(t2, 2, "13:00:00", 31, "12:50:00")
    store.assign_forks(cls["id"])            # T1: A then B, T2: B then A
    standings = next(x for x in store.team_results() if x["class"]["id"] == cls["id"])
    teams = {t["team"]["name"]: t for t in standings["teams"]}
    assert [l["seconds"] for l in teams["T1"]["legs"]] == [1200, 1800]
    # T2 leg 2 went at the 12:40 restart, not T2's 12:45 changeover.
    assert [l["seconds"] for l in teams["T2"]["legs"]] == [2700, 1200]
    assert teams["T1"]["legs"][0]["place"] == 1 and teams["T2"]["legs"][1]["place"] == 1
