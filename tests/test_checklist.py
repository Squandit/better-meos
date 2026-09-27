"""Race-day and close-out checklists."""

from datetime import datetime

import app as appmod
import checklist
import store


def _states(items):
    return {i["text"]: i["state"] for i in items}


def test_race_day_flags_missing_cards_and_start_times(cfg):
    m21a = next(c["id"] for c in store.class_options() if c["name"] == "M21A")
    comp = store.create_competitor({"name": "No Card Nell", "class_id": m21a})
    try:
        texts = " | ".join(i["text"] for i in checklist.race_day())
        assert "with no SI card" in texts and "No start times in" in texts
        assert "SI reader is off" in texts
        cfg.save({"paypal_client_id": "x", "paypal_client_secret": "y", "paypal_sandbox": True})
        assert any("sandbox" in i["text"] for i in checklist.race_day())
    finally:
        store.delete_competitor(comp["id"])


def test_close_out_lists_who_is_still_out_and_marks_the_rest(cfg, monkeypatch):
    monkeypatch.setattr(store, "event_now", lambda: datetime(2026, 5, 17, 23, 0))
    m21a = next(c["id"] for c in store.class_options() if c["name"] == "M21A")
    lost = store.create_competitor({"name": "Lost Larry", "class_id": m21a, "start": "11:00:00"})
    try:
        out = next(i for i in checklist.close_out() if i["action"] == "out")
        assert out["state"] == "bad" and "Lost Larry" in out["text"]
        html = appmod.app.test_client().get("/setup").get_data(as_text=True)
        assert "Race day checklist" in html and "Lost Larry" in html
        r = appmod.app.test_client().post("/api/close-out/remaining", json={"status": "dns"})
        assert r.status_code == 200 and r.get_json()["count"] >= 1
        assert store.result_for(lost["id"])["status"] == "dns"
        assert not any(i["action"] == "out" for i in checklist.close_out())
        assert appmod.app.test_client().post("/api/close-out/remaining",
                                             json={"status": "ok"}).status_code == 400
    finally:
        store.delete_competitor(lost["id"])


def test_checklist_widget_renders(cfg):
    c = appmod.app.test_client()
    c.post("/api/dashboard", json={"layout": [{"id": "checklist", "size": "small"}]})
    html = c.get("/").get_data(as_text=True)
    assert 'data-widget="checklist"' in html and "Full checklist" in html
