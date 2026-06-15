"""Phase 2: runners database, autofill, the download simulator, splits printing."""

import app as appmod
import runners
import simulator
import store


def test_runners_learn_and_lookup():
    runners.record(8800001, "Pat Pace", "PACEOC", "Long")
    runners.record(8800001, "Pat Pace", "PACEOC", "Long")
    runners.record(8800001, "Pat Pace", "PACEOC", "Short")
    r = runners.lookup(8800001)
    assert r["name"] == "Pat Pace" and r["club"] == "PACEOC"
    assert r["usual_class"] == "Long"  # entered Long twice, Short once
    assert any(m["card_number"] == 8800001 for m in runners.search("pat pace"))


def test_simulator_creates_and_downloads():
    before = len(store._competitors)
    out = simulator.simulate_one()
    assert out["ok"] is True and out["name"] and out["card"]
    # The simulated person now exists and has a finish recorded.
    comp = store.find_by_card(out["card"])
    assert comp is not None and comp["finish"] is not None
    assert len(store._competitors) >= before
    # And the runner DB learned them.
    assert runners.lookup(out["card"])["name"] == out["name"]


def test_simulate_requires_a_class_no_demo_pollution(tmp_path, monkeypatch):
    monkeypatch.setenv("BMEOS_EVENTS_DIR", str(tmp_path))
    original = store.current_event_path()
    try:
        store.new_event({"name": "Bare", "date": "2026-07-01"})
        out = simulator.simulate_one()
        assert out["ok"] is False and "class" in out["error"].lower()
        assert store._courses == {} and store._classes == {}  # nothing injected
    finally:
        store.open_event(original)  # restore the seeded event for other tests


def test_simulate_route_no_body_uses_pool():
    c = appmod.app.test_client()
    r = c.post("/api/reader/simulate")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["name"]


def test_runner_lookup_endpoint_autofills():
    runners.record(8800050, "Auto Ann", "AUTOC", "M21A")  # M21A exists (seeded)
    c = appmod.app.test_client()
    d = c.get("/api/runners/lookup?card=8800050").get_json()
    assert d["name"] == "Auto Ann" and d["club"] == "AUTOC"
    assert d["class_id"] is not None  # resolved usual class to a class id


def test_slip_print_param_auto_prints():
    cid = next(iter(store._competitors))
    html = appmod.app.test_client().get(f"/slip/{cid}?print=1").get_data(as_text=True)
    assert "window.print()" in html


def test_team_list_endpoint_and_assignment():
    course = store.create_course({"name": "Relay UI", "type": "linear", "controls": [300]})
    cls = store.create_class({"name": "RelayUI", "course_id": course["id"],
                              "kind": "relay", "legs": 2})
    team = store.create_team({"name": "Alpha", "class_id": cls["id"]})
    c = appmod.app.test_client()
    teams = c.get(f"/api/teams?class_id={cls['id']}").get_json()
    assert any(t["id"] == team["id"] for t in teams)
    comp = store.create_competitor({"name": "Leg Runner", "class_id": cls["id"],
                                    "card_number": 8810001})
    r = c.put(f"/api/competitors/{comp['id']}", json={"team_id": team["id"], "leg": 1})
    assert r.status_code == 200
    assert store.get_competitor(comp["id"])["team_id"] == team["id"]


def test_entry_submit_records_runner():
    c = appmod.app.test_client()
    cid = store.class_options()[0]["id"]
    c.get(f"/submit-entry?class={cid}&name=Entry Eddie&club=EOC&card=8800099")
    assert runners.lookup(8800099)["name"] == "Entry Eddie"
