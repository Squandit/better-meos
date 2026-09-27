"""The customisable home screen."""

from datetime import datetime

import app as appmod
import config
import dashboard
import store


def test_every_widget_renders(cfg):
    c = appmod.app.test_client()
    everything = [{"id": w.id, "size": w.size} for w in dashboard.WIDGETS]
    assert c.post("/api/dashboard", json={"layout": everything}).status_code == 200
    html = c.get("/").get_data(as_text=True)
    for w in dashboard.WIDGETS:
        assert f'data-widget="{w.id}"' in html, w.id


def test_layout_is_cleaned_and_resettable(cfg):
    c = appmod.app.test_client()
    saved = c.post("/api/dashboard", json={"layout": [
        {"id": "stats", "size": "small"}, {"id": "stats"}, {"id": "nope"},
        {"id": "notes", "size": "gigantic"}]}).get_json()["layout"]
    assert saved == [{"id": "stats", "size": "small"}, {"id": "notes", "size": "small"}]
    assert dashboard.layout() == saved
    c.post("/api/dashboard/reset")
    assert dashboard.layout() == dashboard.DEFAULT_LAYOUT
    c.post("/api/dashboard", json={"layout": []})          # an empty screen is allowed
    assert dashboard.layout() == []
    assert "Press Customise" in c.get("/").get_data(as_text=True)


def test_notes_are_kept_with_the_event(cfg):
    c = appmod.app.test_client()
    c.post("/api/dashboard/notes", json={"text": "Radio channel 3"})
    assert config.get("dashboard_notes") == "Radio channel 3"
    assert config.source("dashboard_notes") == "event"


def test_alerts_flag_unmatched_cards_and_undrawn_classes():
    import si_reader
    si_reader.process_card({"card_number": 9990001, "punches": [],
                            "finish": store.parse_clock("12:00:00")})
    cid = store.class_options()[0]["id"]
    store.create_competitor({"name": "No Start Yet", "class_id": cid})
    texts = [a["text"] for a in dashboard._alerts({"classes": [], "rows": [],
                                                   "evaluated": store.evaluate()[0]})["items"]]
    assert any("unmatched" in t for t in texts)
    assert any("no start time" in t for t in texts)


def test_mp_hotspots_count_missed_controls():
    from results import build_result
    d = datetime(2026, 5, 17)
    course = {"type": "linear", "controls": [31, 32, 33]}
    runs = []
    for i, punched in enumerate(([31, 33], [31, 33], [31, 32, 33])):
        card = {"id": i, "name": f"R{i}", "class": "X", "start": d.replace(hour=10),
                "finish": d.replace(hour=11),
                "punches": [(code, d.replace(hour=10, minute=10 + n)) for n, code in enumerate(punched)]}
        runs.append(build_result(card, course))
    rows = dashboard.mp_hotspots([{"course": course, "results": runs}])
    assert rows[0] == {"code": 32, "missed": 2, "of": 3, "pct": 67}
