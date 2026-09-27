"""Class options: names-only or hidden results, entry caps, online entry."""

import pytest

import app as appmod
import display
import online_entry
import season
import store

PUBLIC = {"SERVER_PORT": "8800"}


@pytest.fixture
def kids(cfg):
    """A class 'Kids' on M21A's course with two finishers, removed afterwards."""
    course = store.get_class(next(c["id"] for c in store.class_options()
                                  if c["name"] == "M21A"))["course_id"]
    cls = store.create_class({"name": "Kids", "course_id": course})
    punches = [{"code": c, "time": f"10:{10 + i}:00"} for i, c in enumerate((138, 130, 142, 155))]
    ids = [store.create_competitor({"name": n, "class_id": cls["id"], "read": True,
                                    "start": "10:00:00", "finish": f"10:{m}:00",
                                    "punches": punches})["id"]
           for n, m in (("Zed Young", 30), ("Amy Young", 40))]
    yield cls
    for cid in ids:
        store.delete_competitor(cid)
    store.delete_class(cls["id"])


def _block(blocks, name):
    return next((b for b in blocks if b["name"] == name), None)


def test_names_only_class(kids):
    store.update_class(kids["id"], {"results_mode": "no_times"})
    block = _block(display.results_view(), "Kids")
    assert [r["name"] for r in block["rows"]] == ["Amy Young", "Zed Young"]   # A-Z, not by time
    assert all(r["time"] is None and r["position"] is None for r in block["rows"])
    c = appmod.app.test_client()
    assert 'data-name="Kids"' not in c.get("/live").get_data(as_text=True)
    assert all(p["class"] != "Kids" for p in season.prizes_for_open_event()["classes"])
    feed = c.get("/get-results").get_json()
    kid = next(x for cls in feed if cls["className"] == "Kids" for x in cls["competitors"])
    assert kid["timeSecs"] is None and kid["place"] is None


def test_hidden_class_stays_off_public_pages(kids):
    store.update_class(kids["id"], {"results_mode": "hidden"})
    c = appmod.app.test_client()
    admin = c.get("/results").get_data(as_text=True)
    assert "Kids" in admin and "hidden from public" in admin
    public = c.get("/results", environ_overrides=PUBLIC).get_data(as_text=True)
    assert "Zed Young" not in public
    assert "Zed Young" not in c.get(f"/public/{store.EVENT['slug']}").get_data(as_text=True)
    assert "Zed Young" not in c.get("/splits", environ_overrides=PUBLIC).get_data(as_text=True)
    assert all(cls["className"] != "Kids" for cls in c.get("/get-results").get_json())
    xml = appmod._published_files()["results.xml"].decode()
    assert "Zed" not in xml


def test_entry_cap_and_closing_online_entry(kids):
    item = {"name": "New Kid", "club": "", "card": 9500001, "class_id": kids["id"], "type": "senior"}
    online_entry._check_items([item])                           # open, no cap: fine
    store.update_class(kids["id"], {"entry_max": 2})           # two already entered
    with pytest.raises(online_entry.EntryError, match="full"):
        online_entry._check_items([item])
    xml = appmod.app.test_client().get("/get-classes").get_data(as_text=True)
    assert "Kids" not in xml
    store.update_class(kids["id"], {"entry_max": 5, "online_entry": False})
    with pytest.raises(online_entry.EntryError, match="isn't open"):
        online_entry._check_items([item])
    store.update_class(kids["id"], {"online_entry": True})
    two = [item, dict(item, name="Other Kid", card=9500002)]
    online_entry._check_items(two)                              # 2 + 2 <= 5
    store.update_class(kids["id"], {"entry_max": 3})
    with pytest.raises(online_entry.EntryError, match="full"):
        online_entry._check_items(two)                          # 2 + 2 > 3


def test_class_options_are_validated_and_saved(kids):
    with pytest.raises(store.StoreError):
        store.update_class(kids["id"], {"results_mode": "secret"})
    store.update_class(kids["id"], {"results_mode": "no_times", "entry_max": "12",
                                    "online_entry": False})
    import db
    saved = db.load_event(store._active_event_id)["classes"][kids["id"]]
    assert saved["results_mode"] == "no_times" and saved["entry_max"] == 12
    assert saved["online_entry"] is False
