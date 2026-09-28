"""Start page / file-per-event lifecycle + the no-event guard."""

import os

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



def test_start_clock_call_up_and_beeps(cfg):
    import app as appmod
    c = appmod.app.test_client()
    html = c.get("/starter").get_data(as_text=True)
    assert 'data-callup-minutes="3"' in html and "Call up" in html and '<button class="sound"' in html
    cfg.save({"start_callup_minutes": 0}, target="event")
    cfg.save({"start_beeps": "off"}, target="computer")
    html = c.get("/starter").get_data(as_text=True)
    assert "Call up" not in html and '<button class="sound"' not in html


def test_stage_matching_survives_a_card_change(tmp_path, monkeypatch):
    """A runner on a hire card in stage 2 is still one person across stages."""
    import stages

    who = stages._People()
    day1 = {"name": "Ada  Lovelace", "club": "LOST", "card_number": 111}
    day2 = {"name": "ada lovelace", "club": "lost", "card_number": 999, "hired": True}
    other = {"name": "Bo Brown", "club": "LOST", "card_number": 999, "hired": True}
    for comp in (day1, day2, other):
        who.link(stages._keys(comp))
    assert who.of(day1) == who.of(day2)          # same name + club
    assert who.of(other) != who.of(day2)          # a shared hire card links nobody


def test_old_bmeos_events_are_renamed_and_still_open(tmp_path):
    before = store.current_event_path()
    made = store.new_event({"name": "Old Champs", "date": "2026-03-01"}, folder=str(tmp_path))
    assert made["path"].endswith(".ctrl")
    old = tmp_path / "old-champs.bmeos"
    store.open_event(before)                      # Windows can't rename an open file
    os.rename(made["path"], old)
    try:
        store.open_event(str(old))               # a .bmeos anywhere still opens
        store.open_event(before)
        listed = store.events_in_folder(str(tmp_path))
        assert [e["filename"] for e in listed] == ["old-champs.ctrl"]
        assert not old.exists()
        (tmp_path / "busy.bmeos").write_bytes(b"x")
        (tmp_path / "busy.ctrl").write_bytes(b"y")   # never overwritten
        store.rename_old_event_files(str(tmp_path))
        assert (tmp_path / "busy.bmeos").exists() and (tmp_path / "busy.ctrl").read_bytes() == b"y"
    finally:
        store.open_event(before)


def test_sample_event_is_a_drawn_sprint_ready_for_the_finish(tmp_path, monkeypatch):
    import simulator
    monkeypatch.setenv("BMEOS_EVENTS_DIR", str(tmp_path))
    r = appmod.app.test_client().post("/api/events/sample")
    assert r.status_code == 201
    assert store.current_event_path().endswith("sample-sprint.ctrl")
    comps = [c for c in store._competitors.values() if not c.get("vacant")]
    assert len(comps) == 130 and len(store._classes) == 12 and len(store._courses) == 4
    assert all(c["start"] and c["card_number"] and c["bib"] for c in comps)
    assert len({c["name"] for c in comps}) == 130                 # nobody twice
    assert not any(c.get("read_at") for c in comps)                # nobody in yet
    # Simulate read brings in the entered runners, not made-up walk-ups.
    out = simulator.simulate_one()
    assert out["ok"] and out["card"] in {c["card_number"] for c in comps}
    assert len(store._competitors) == len(comps) + 12              # + vacants, no one new
    # A second one is another file, not the same one again.
    assert appmod.app.test_client().post("/api/events/sample").status_code == 201
    assert store.current_event_path().endswith("sample-sprint-2.ctrl")
