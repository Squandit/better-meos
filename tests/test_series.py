"""Cross-event series standings + competitor profiles (folder scan)."""

import app as appmod
import series
import store


def _add(cls_id, name, card, finish):
    store.create_competitor({"name": name, "club": "OC", "class_id": cls_id,
                             "card_number": card, "start": "10:00:00",
                             "finish": finish, "punches": [{"code": 31, "time": "10:10:00"}]})


def test_series_points_and_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("BMEOS_EVENTS_DIR", str(tmp_path))
    original = store.current_event_path()
    try:
        # Event 1: Ann 1st (30:00), Ben 2nd (35:00).
        store.new_event({"name": "E1", "date": "2026-05-01",
                         "first_start": "10:00:00", "type": "linear"})
        course = store.create_course({"name": "C", "type": "linear", "controls": [31]})
        cls = store.create_class({"name": "Open", "course_id": course["id"]})
        _add(cls["id"], "Ann", 1, "10:30:00")
        _add(cls["id"], "Ben", 2, "10:35:00")
        # Event 2: Ann 1st again.
        store.new_event({"name": "E2", "date": "2026-05-08",
                         "first_start": "10:00:00", "type": "linear"})
        course2 = store.create_course({"name": "C", "type": "linear", "controls": [31]})
        cls2 = store.create_class({"name": "Open", "course_id": course2["id"]})
        _add(cls2["id"], "Ann", 1, "10:31:00")

        st = series.standings(str(tmp_path))
        people = next(c["people"] for c in st if c["class"] == "Open")
        ann = next(p for p in people if p["name"] == "Ann")
        ben = next(p for p in people if p["name"] == "Ben")
        assert ann["total"] == 200          # 1st (100) twice, base 100 / step 2
        assert ben["total"] == 98           # 2nd once: 100 - 1*2
        assert people[0]["name"] == "Ann"   # ranked first

        prof = series.profile("card:1", str(tmp_path))
        assert prof["person"]["name"] == "Ann"
        assert len(prof["results"]) == 2
    finally:
        store.open_event(original)


def test_series_and_profile_pages_render():
    c = appmod.app.test_client()
    assert c.get("/series").status_code == 200
    # Test Runner (card 8635918) is in the seeded event.
    assert c.get("/profile?key=card:8635918").status_code == 200
    assert c.get("/profile?key=card:0").status_code == 404
