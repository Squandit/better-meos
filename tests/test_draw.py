"""Start-list draw: club separation, vacants, shared-course staggering."""

import random
from datetime import timedelta

import app as appmod
import draw
import store


def test_club_separation_keeps_clubmates_apart():
    runners = [{"name": f"{club}{i}", "club": club}
               for club, n in (("A", 4), ("B", 3), ("C", 3)) for i in range(n)]
    for seed in range(20):
        order = draw.club_separated(runners, random.Random(seed))
        assert sorted(r["name"] for r in order) == sorted(r["name"] for r in runners)
        assert all(order[i]["club"] != order[i + 1]["club"] for i in range(len(order) - 1))


def test_draw_classes_with_vacants_and_shared_course():
    course = store.create_course({"name": "Draw loop", "type": "linear", "controls": [31]})
    a = store.create_class({"name": "Draw A", "course_id": course["id"]})
    b = store.create_class({"name": "Draw B", "course_id": course["id"]})
    for cls, n in ((a, 4), (b, 3)):
        for i in range(n):
            store.create_competitor({"name": f"{cls['name']} {i}", "club": f"C{i % 2}",
                                     "class_id": cls["id"]})
    out = appmod.app.test_client().post("/api/draw", json={
        "class_ids": [a["id"], b["id"]], "first_start": "10:00:00",
        "interval_seconds": 60, "vacants": 1, "method": "club"}).get_json()
    assert {c["class"]: c["drawn"] for c in out["classes"]} == {"Draw A": 4, "Draw B": 3}
    starts = [c["start"] for cid in (a["id"], b["id"])
              for c in store._competitors_in_class(cid)]
    # Shared course: no two runners (or vacants) of these classes on the same minute.
    assert len(starts) == len(set(starts)) == 4 + 3 + 2
    first = store.parse_clock("10:00:00")
    assert min(starts) == first
    a_starts = sorted(c["start"] for c in store._competitors_in_class(a["id"]))
    assert all(y - x == timedelta(minutes=2) for x, y in zip(a_starts, a_starts[1:]))
    vac = [c for c in store._competitors_in_class(a["id"]) if c["vacant"]]
    assert len(vac) == 1 and vac[0]["name"] == store.VACANT_NAME

    # Redraw replaces vacants instead of piling them up.
    draw.draw_classes([a["id"]], first_start="10:00:00", interval_seconds=60, vacants=1)
    assert sum(1 for c in store._competitors_in_class(a["id"]) if c["vacant"]) == 1


def test_keep_existing_only_places_late_entries():
    course = store.create_course({"name": "Late loop", "type": "linear", "controls": [31]})
    cls = store.create_class({"name": "Draw Late", "course_id": course["id"]})
    early = store.create_competitor({"name": "Early", "class_id": cls["id"], "start": "10:00:00"})
    late = store.create_competitor({"name": "Late", "class_id": cls["id"]})
    draw.draw_classes([cls["id"]], first_start="10:00:00", interval_seconds=120,
                      keep_existing=True)
    assert store.get_competitor(early["id"])["start"] == store.parse_clock("10:00:00")
    assert store.get_competitor(late["id"])["start"] == store.parse_clock("10:02:00")


def test_draw_page_renders():
    assert "Draw start list" in appmod.app.test_client().get("/draw").get_data(as_text=True)
