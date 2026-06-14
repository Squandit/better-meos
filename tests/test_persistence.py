"""Persistence, card-read and reload behaviour of the store."""

from datetime import datetime

import db
import si_reader
import store


def t(h, m, s=0):
    return datetime(2026, 5, 17, h, m, s)


def test_seed_populated():
    # Order-independent: other tests share this singleton store and may add
    # records, so assert the seeded roster is present rather than exact counts.
    names = {c["name"] for c in store._classes.values()}
    assert {"M21A", "Score-O"} <= names
    assert store.find_by_card(8635918)["name"] == "Test Runner"


def test_card_read_updates_competitor():
    out = si_reader.simulate({
        "card_number": 8635918,  # seeded Test Runner
        "start": t(9, 0), "finish": t(10, 0),
        "punches": [(138, t(9, 10)), (130, t(9, 20))],
    })
    assert out["ok"] is True
    comp = store.find_by_card(8635918)
    assert comp["finish"] == t(10, 0)
    assert len(comp["punches"]) == 2


def test_duplicate_card_rejected():
    cls_id = next(iter(store._classes))
    store.create_competitor({"name": "First Owner", "class_id": cls_id,
                             "card_number": 7900001})
    try:
        store.create_competitor({"name": "Second Owner", "class_id": cls_id,
                                 "card_number": 7900001})
        assert False, "expected StoreError on duplicate card"
    except store.StoreError as err:
        assert "7900001" in str(err)


def test_card_read_unknown_card_is_reported_not_raised():
    out = si_reader.simulate({"card_number": 424242, "punches": []})
    assert out["ok"] is False
    assert "424242" in out["error"]


def test_data_survives_reload():
    # Create a competitor, then reconnect + reload as if the process restarted.
    cls_id = next(iter(store._classes))
    comp = store.create_competitor({
        "name": "Persisted Pat", "class_id": cls_id, "card_number": 7000001,
        "start": "09:00:00", "finish": "09:45:00",
    })
    db.close()
    db.connect()
    store.reload()
    again = store.get_competitor(comp["id"])
    assert again is not None
    assert again["name"] == "Persisted Pat"
    # counter resumed past the highest id, so the next create won't collide
    assert store._counters["competitor"] >= comp["id"]


def test_backup_path_exists():
    assert db.path()  # non-empty filesystem path for the backup download


def test_restart_via_init_preserves_data():
    # A real restart re-runs store._init(), which calls db.save_event for the
    # active event. That must NOT cascade-delete the event's records.
    before = len(store._competitors)
    assert before > 0
    store._init()
    assert len(store._competitors) == before
    assert store.find_by_card(8635918)["name"] == "Test Runner"


def test_backup_restore_round_trip(tmp_path):
    cls_id = next(iter(store._classes))
    comp = store.create_competitor({
        "name": "Backup Bea", "class_id": cls_id, "card_number": 7700001,
    })
    backup = str(tmp_path / "snap.db")
    db.backup_to(backup)

    # Mutate after the snapshot, then restore and confirm the snapshot wins.
    store.delete_competitor(comp["id"])
    assert store.get_competitor(comp["id"]) is None
    store.restore(backup)
    assert store.get_competitor(comp["id"])["name"] == "Backup Bea"


def test_restore_rejects_non_backup(tmp_path):
    bogus = tmp_path / "not.db"
    bogus.write_bytes(b"this is not a sqlite database")
    try:
        store.restore(str(bogus))
        assert False, "expected StoreError"
    except store.StoreError:
        pass
