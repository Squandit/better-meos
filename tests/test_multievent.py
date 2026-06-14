"""Multi-event: events, switching, series standings, cross-event profiles."""

import pytest

import store


@pytest.fixture(autouse=True)
def restore_active_event():
    """These tests switch the active event; restore it so other test modules
    (which assume the seeded event is active) aren't affected by ordering."""
    original = store.active_event_id()
    yield
    if store.active_event_id() != original:
        store.set_active_event(original)


def _make_event_with_runner(name, date_iso, card, runner_name, finish_clock):
    """Create an event, switch to it, add a 1-control class + one finished runner."""
    ev = store.create_event({"name": name, "date": date_iso})
    store.set_active_event(ev["id"])
    course = store.create_course({"name": "C", "type": "linear", "controls": [200]})
    cls = store.create_class({"name": "Open", "course_id": course["id"]})
    store.create_competitor({
        "name": runner_name, "class_id": cls["id"], "card_number": card,
        "start": "10:00:00", "finish": finish_clock,
        "punches": [{"code": 200, "time": "10:05:00"}],
    })
    return ev


def test_create_and_switch_event_isolates_model():
    original = store.active_event_id()
    ev = store.create_event({"name": "Sprint Cup", "date": "2026-07-01"})
    store.set_active_event(ev["id"])
    assert store.active_event_id() == ev["id"]
    assert store.EVENT["name"] == "Sprint Cup"
    assert store.EVENT_DATE.isoformat() == "2026-07-01"
    # Fresh event has no classes/competitors of the original.
    assert store._competitors == {}
    # Switch back restores the original model.
    store.set_active_event(original)
    assert store.active_event_id() == original
    assert len(store._competitors) > 0


def test_series_standings_aggregate_across_events():
    series = store.create_series({"name": "Summer Series"})["id"]
    e1 = _make_event_with_runner("Round 1", "2026-08-01", 6000001, "Sam Series", "10:30:00")
    e2 = _make_event_with_runner("Round 2", "2026-08-08", 6000001, "Sam Series", "10:25:00")
    store.set_event_series(e1["id"], series)
    store.set_event_series(e2["id"], series)

    standings = store.series_standings(series)
    sam = next(s for s in standings["standings"] if s["name"] == "Sam Series")
    # Won both rounds (only runner) -> 100 + 100, across 2 events.
    assert sam["points"] == 200
    assert sam["events"] == 2


def test_profile_collects_results_across_events():
    # Reuses the two events created above (same card 6000001).
    profile = store.competitor_profile(card=6000001)
    assert profile["name"] == "Sam Series"
    assert len(profile["results"]) == 2
    event_names = {row["event"]["name"] for row in profile["results"]}
    assert {"Round 1", "Round 2"} <= event_names


def test_event_routes(client_unused=None):
    import app as appmod
    c = appmod.app.test_client()
    assert c.get("/events").status_code == 200
    r = c.post("/api/events", json={"name": "API Event", "date": "2026-09-09"})
    assert r.status_code == 201
    eid = r.get_json()["event"]["id"]
    assert c.post("/api/events/active", json={"event_id": eid}).status_code == 200
    # restore active to the seeded event so later tests see the roster
    c.post("/api/events/active", json={"event_id": 1})
