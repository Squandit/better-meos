"""Persistence, card-read and reload behaviour of the store."""

import threading
import time
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
    # Create a competitor, then re-open the event file as if the process restarted.
    cls_id = next(iter(store._classes))
    comp = store.create_competitor({
        "name": "Persisted Pat", "class_id": cls_id, "card_number": 7000001,
        "start": "09:00:00", "finish": "09:45:00",
    })
    store.open_event(store.current_event_path())  # reconnect + reload from disk
    again = store.get_competitor(comp["id"])
    assert again is not None
    assert again["name"] == "Persisted Pat"
    # counter resumed past the highest id, so the next create won't collide
    assert store._counters["competitor"] >= comp["id"]


def test_backup_path_exists():
    assert db.path()  # non-empty filesystem path for the backup download


def test_reopen_preserves_data():
    # Re-opening the event file (a real restart) must NOT lose records -- guards
    # the save_event UPSERT against cascade-deleting the event's children.
    before = len(store._competitors)
    assert before > 0
    store.open_event(store.current_event_path())
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


def test_backup_waits_for_a_card_read_in_progress(tmp_path):
    # A backup that started while a batch had uncommitted writes used to spin
    # inside SQLite holding the db lock, so the batch could never commit and
    # the whole app froze (seen as a hung stress run).
    cls_id = next(iter(store._classes))
    finished = threading.Event()

    def slow_batch():
        with store.batch():
            store.create_competitor({"name": "Mid Batch", "class_id": cls_id,
                                     "card_number": 7700002})
            time.sleep(0.5)
        finished.set()

    writer = threading.Thread(target=slow_batch, daemon=True)
    writer.start()
    time.sleep(0.1)
    backup = threading.Thread(target=db.backup_to, args=(str(tmp_path / "b.db"),),
                              daemon=True)
    backup.start()
    writer.join(10)
    backup.join(10)
    assert finished.is_set() and not backup.is_alive()
    assert (tmp_path / "b.db").exists()


def test_restore_rejects_non_backup(tmp_path):
    bogus = tmp_path / "not.db"
    bogus.write_bytes(b"this is not a sqlite database")
    try:
        store.restore(str(bogus))
        assert False, "expected StoreError"
    except store.StoreError:
        pass
