"""Multi-day: combined standings across stage files and chase starts."""

import os

import app as appmod
import store


def _stage(name, date, runs):
    """Create a stage file with one class and the given (name, card, seconds) runs."""
    store.new_event({"name": name, "date": date, "first_start": "10:00:00"})
    course = store.create_course({"name": "S", "type": "linear", "controls": [31]})
    cls = store.create_class({"name": "M21", "course_id": course["id"]})
    for runner, card, secs in runs:
        if secs is None:                     # entered, not run yet
            store.create_competitor({"name": runner, "class_id": cls["id"], "card_number": card})
            continue
        finish = store.format_clock(store.parse_clock("10:00:00").replace(
            minute=secs // 60, second=secs % 60))
        store.create_competitor({"name": runner, "class_id": cls["id"], "card_number": card,
                                 "start": "10:00:00", "finish": finish,
                                 "punches": [{"code": 31, "time": "10:00:30"}]})
    return store.current_event_path()


def test_stages_page_and_chase_start():
    original = store.current_event_path()
    try:
        s1 = _stage("Stage One", "2026-07-01", [("Ana", 9600001, 1500), ("Ben", 9600002, 1200)])
        s2 = _stage("Stage Two", "2026-07-02", [("Ana", 9600001, 1200), ("Ben", 9600002, 1800)])
        c = appmod.app.test_client()
        html = c.get("/stages?stage=" + os.path.basename(s1)
                     + "&stage=" + os.path.basename(s2)).get_data(as_text=True)
        # Ana 25:00 + 20:00 = 45:00 beats Ben 20:00 + 30:00 = 50:00.
        assert html.index("Ana") < html.index("Ben") and "00:45:00" in html

        _stage("Stage Three", "2026-07-03", [("Ana", 9600001, None), ("Ben", 9600002, None)])
        r = c.post("/api/stages/chase", json={
            "stages": [os.path.basename(s1), os.path.basename(s2)], "first_start": "09:00:00"})
        assert r.get_json()["assigned"] == 2
        starts = {comp["name"]: store.format_clock(comp["start"])
                  for comp in store._competitors.values()}
        assert starts == {"Ana": "09:00:00", "Ben": "09:05:00"}
        # Paths from the browser are ignored unless they're files in the folder.
        assert c.post("/api/stages/chase", json={"stages": ["/etc/passwd"],
                                                 "first_start": "09:00"}).status_code == 400
    finally:
        store.open_event(original)
