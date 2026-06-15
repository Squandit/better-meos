"""Start page / file-per-event lifecycle + the no-event guard."""

import pytest

import app as appmod
import store


@pytest.fixture(autouse=True)
def restore_event():
    """These tests create/close events; restore the seeded conftest event after
    each so the rest of the suite still sees an open, populated event."""
    original = store.current_event_path()
    yield
    if original and store.current_event_path() != original:
        store.open_event(original)


def test_no_event_redirects_to_start():
    original = store.current_event_path()
    store.close_event()
    c = appmod.app.test_client()
    assert c.get("/").status_code == 302           # -> /start
    assert c.get("/start").status_code == 200
    assert c.get("/competitors").status_code == 302
    assert c.post("/api/competitors", json={}).status_code == 409
    store.open_event(original)  # restore for the rest of this test/file


def test_create_open_close_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("BMEOS_EVENTS_DIR", str(tmp_path))
    ev = store.new_event({"name": "Cup A", "date": "2026-08-01",
                          "first_start": "10:00:00", "type": "relay"})
    assert store.has_open_event()
    assert store.EVENT["name"] == "Cup A" and store.EVENT["type"] == "relay"
    assert store._competitors == {}  # new events start empty
    names = [e["name"] for e in store.events_in_folder(str(tmp_path))]
    assert "Cup A" in names
    store.close_event()
    assert not store.has_open_event()


def test_new_event_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("BMEOS_EVENTS_DIR", str(tmp_path))
    a = store.new_event({"name": "Dup", "date": "2026-08-01"})
    b = store.new_event({"name": "Dup", "date": "2026-08-02"})
    assert a["path"] != b["path"]  # second got a -2 suffix


def test_open_foreign_file_does_not_swap_event(tmp_path):
    bogus = tmp_path / "bogus.bmeos"
    bogus.write_bytes(b"this is not a sqlite database")
    before = store.current_event_path()
    try:
        store.open_event(str(bogus))
        assert False, "expected StoreError"
    except store.StoreError:
        pass
    # Still on the original event; saves still target it.
    assert store.current_event_path() == before
    assert store.find_by_card(8635918)["name"] == "Test Runner"


def test_create_event_via_api_imports_entries(tmp_path, monkeypatch):
    import io
    monkeypatch.setenv("BMEOS_EVENTS_DIR", str(tmp_path))
    c = appmod.app.test_client()
    # create event then add a class + import a competitor referencing it
    r = c.post("/api/events/new", data={"name": "API Cup", "date": "2026-09-01",
                                        "type": "linear"},
               content_type="multipart/form-data")
    assert r.status_code == 201
    assert c.get("/setup").status_code == 200
