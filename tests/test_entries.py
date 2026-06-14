"""Registration: entry create/dedupe and the start-list draw."""

import entries as entries_mod
import store


def _class_id():
    return next(iter(store._classes))


def test_create_entry_and_dedupe():
    cid = _class_id()
    e = entries_mod.create({"name": "Reg Reggie", "class_id": cid,
                            "card_number": 7710001, "club": "REGOC"})
    assert e["id"] and e["class_name"]

    # same card -> rejected
    try:
        entries_mod.create({"name": "Other", "class_id": cid, "card_number": 7710001})
        assert False, "expected duplicate-card StoreError"
    except store.StoreError as err:
        assert "7710001" in str(err)

    # same name + class -> rejected
    try:
        entries_mod.create({"name": "Reg Reggie", "class_id": cid})
        assert False, "expected duplicate-name StoreError"
    except store.StoreError:
        pass


def test_create_entry_requires_valid_class():
    try:
        entries_mod.create({"name": "No Class", "class_id": 999999})
        assert False, "expected StoreError"
    except store.StoreError:
        pass


def test_draw_creates_competitors_with_start_times():
    # A fresh class so the draw is isolated from seeded competitors.
    course = store.create_course({"name": "Draw course", "type": "linear",
                                  "controls": [71, 72]})
    cls = store.create_class({"name": "DrawClass", "course_id": course["id"]})
    cid = cls["id"]
    for i, name in enumerate(["Bravo", "Alpha", "Charlie"]):
        entries_mod.create({"name": name, "class_id": cid, "card_number": 7720000 + i})

    # The draw is event-wide; other tests may leave pending entries, so assert
    # our three were created (>=3) and verify the per-class result precisely.
    outcome = entries_mod.draw_startlist("10:00:00", 3)
    assert outcome["created"] >= 3

    members = sorted(store._competitors_in_class(cid), key=lambda c: c["start"])
    # Alphabetical draw: Alpha 10:00, Bravo 10:03, Charlie 10:06
    assert [c["name"] for c in members] == ["Alpha", "Bravo", "Charlie"]
    assert members[0]["start"].strftime("%H:%M:%S") == "10:00:00"
    assert members[1]["start"].strftime("%H:%M:%S") == "10:03:00"

    # Re-running the draw skips already-converted entries.
    again = entries_mod.draw_startlist("11:00:00", 2)
    assert again["created"] == 0


def test_late_entry_redraw_does_not_collide():
    course = store.create_course({"name": "Late course", "type": "linear",
                                  "controls": [81, 82]})
    cls = store.create_class({"name": "LateClass", "course_id": course["id"]})
    cid = cls["id"]
    entries_mod.create({"name": "Early Ann", "class_id": cid, "card_number": 7740001})
    entries_mod.draw_startlist("10:00:00", 5)  # Ann -> 10:00

    # A late entry, re-draw at the same first start must NOT reuse 10:00.
    entries_mod.create({"name": "Late Lou", "class_id": cid, "card_number": 7740002,
                        "late": True})
    entries_mod.draw_startlist("10:00:00", 5)

    starts = sorted(c["start"] for c in store._competitors_in_class(cid))
    assert len(starts) == 2
    assert starts[0] != starts[1]  # no collision
    assert starts[1].strftime("%H:%M:%S") == "10:05:00"


def test_entry_rejected_when_card_belongs_to_competitor():
    cid = next(iter(store._classes))
    store.create_competitor({"name": "Has Card", "class_id": cid,
                             "card_number": 7750001})
    try:
        entries_mod.create({"name": "Wants Card", "class_id": cid,
                            "card_number": 7750001})
        assert False, "expected StoreError"
    except store.StoreError as err:
        assert "already registered" in str(err)


def test_entry_routes(client_fixture=None):
    import app as appmod
    c = appmod.app.test_client()
    assert c.get("/enter").status_code == 200
    assert c.get("/entries").status_code == 200
    cid = _class_id()
    r = c.post("/api/entries", json={"name": "Route Rita", "class_id": cid,
                                     "card_number": 7730001})
    assert r.status_code == 201
    body = r.get_json()
    assert body["entry"]["name"] == "Route Rita"
    assert body["payment"]["enabled"] is False  # no Stripe key in tests
