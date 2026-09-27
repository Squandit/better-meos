"""Results display: runners not read yet, time formats, class order, results by
course, who is listed, time behind, and the live screen."""

import sqlite3
from datetime import datetime

import pytest

import app as appmod
import db
import display
import iofxml
import store


@pytest.fixture
def scratch():
    """Competitors and classes made by a test are removed afterwards (the test
    event is shared by the whole suite)."""
    comps, classes = set(store._competitors), set(store._classes)
    yield
    for cid in set(store._competitors) - comps:
        store.delete_competitor(cid)
    for cid in set(store._classes) - classes:
        store.delete_class(cid)


def m21a():
    return next(c for c in store.class_options() if c["name"] == "M21A")["id"]


def test_runner_with_no_card_read_is_pending_not_dnf(scratch, monkeypatch):
    monkeypatch.setattr(store, "event_now", lambda: datetime(2026, 5, 17, 12, 0))
    comp = store.create_competitor({"name": "Still Out", "class_id": m21a(),
                                    "card_number": 9300001, "start": "11:00:00"})
    result = store.result_for(comp["id"])
    assert result["status"] == "pending" and result["position"] is None
    assert display.view_row(result)["status_label"] == "On course"

    later = store.create_competitor({"name": "Later Start", "class_id": m21a(),
                                     "start": "13:00:00"})
    assert display.view_row(store.result_for(later["id"]))["status_label"] == "Not started"

    # Reading the card gives the verdict: punches but no finish is a DNF.
    store.apply_card_read({"card_number": 9300001, "punches": [
        (138, datetime(2026, 5, 17, 11, 10))]})
    assert store.result_for(comp["id"])["status"] == "dnf"


def test_typing_a_finish_counts_as_the_run(scratch):
    comp = store.create_competitor({"name": "Paper Backup", "class_id": m21a(),
                                    "start": "10:00:00"})
    assert store.result_for(comp["id"])["status"] == "pending"
    store.update_competitor(comp["id"], {"finish": "10:50:00"})
    assert store.result_for(comp["id"])["status"] == "mp"      # no punches typed in
    store.update_competitor(comp["id"], {"manual_status": "dns"})
    assert store.result_for(comp["id"])["status"] == "dns"


def test_old_event_files_count_existing_runs_as_read():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    conn.execute("INSERT INTO events (id, name, date_iso) VALUES (1, 'Old', '2026-05-17')")
    conn.executemany(
        "INSERT INTO competitors (id, event_id, name, class_id, start, finish) "
        "VALUES (?, 1, ?, 1, ?, ?)",
        [(1, "Finished", "2026-05-17T10:00:00", "2026-05-17T10:40:00"),
         (2, "Read, no finish", "2026-05-17T10:00:00", None),
         (3, "Never read", "2026-05-17T10:00:00", None)])
    conn.execute("INSERT INTO punches (competitor_id, code, time, sequence) "
                 "VALUES (2, 31, '2026-05-17T10:10:00', 0)")
    conn.execute("ALTER TABLE competitors DROP COLUMN read_at")   # as an old file
    db._migrate(conn)
    read = {r["name"]: r["read_at"] for r in conn.execute("SELECT name, read_at FROM competitors")}
    assert read["Finished"] and read["Read, no finish"]
    assert read["Never read"] is None


def test_time_formats(cfg):
    assert display.time_formatter("auto")(2709) == "45:09"
    assert display.time_formatter("auto")(3909) == "1:05:09"
    assert display.time_formatter("minutes")(3909) == "65:09"
    assert display.time_formatter("hms")(3909) == "01:05:09"
    assert display.time_formatter("auto")(None) is None
    cfg.save({"time_format": "minutes"}, target="event")
    row = next(r for c in display.console_data() for r in c["rows"] if r["name"] == "Test Runner")
    assert row["time"] == "82:45"


def test_class_order_setting_and_natural_order(cfg, scratch):
    course = store.get_class(m21a())["course_id"]
    for name in ("W10", "W8"):
        store.create_class({"name": name, "course_id": course})
    names = [c["name"] for c in display.console_data()]
    assert names.index("W8") < names.index("W10")          # numbers sort as numbers
    cfg.save({"class_order": "Score-O, W10"}, target="event")
    names = [c["name"] for c in display.console_data()]
    assert names[:2] == ["Score-O", "W10"]


def test_results_by_course_ranks_classes_together(cfg, scratch):
    course = store.get_class(m21a())["course_id"]
    m35 = store.create_class({"name": "M35", "course_id": course})
    store.create_competitor({"name": "Speedy Veteran", "class_id": m35["id"],
                             "start": "10:00:00", "finish": "10:30:00", "read": True,
                             "punches": [{"code": c, "time": f"10:{10 + i}:00"}
                                         for i, c in enumerate((138, 130, 142, 155))]})
    cfg.save({"results_group_by": "course"}, target="event")
    blocks = display.results_view()
    block = next(b for b in blocks if b["by_course"] and any(r["class"] == "M35" for r in b["rows"]))
    assert block["rows"][0]["name"] == "Speedy Veteran" and block["rows"][0]["position"] == 1
    assert {r["class"] for r in block["rows"]} == {"M21A", "M35"}
    html = appmod.app.test_client().get("/results").get_data(as_text=True)
    assert "Course results" in html


def test_who_is_listed(cfg, scratch, monkeypatch):
    monkeypatch.setattr(store, "event_now", lambda: datetime(2026, 5, 17, 12, 0))
    store.create_competitor({"name": "Out There", "class_id": m21a(), "start": "11:30:00"})
    store.create_competitor({"name": "No Show", "class_id": m21a(), "read": True})

    def m21a_block():
        return next(b for b in display.results_view() if b["name"] == "M21A")

    block = m21a_block()
    names = [r["name"] for r in block["rows"]]
    assert "No Show" not in names                       # DNS left out by default
    assert "Mispunch Mary" in names
    assert "Out There" in [r["name"] for r in block["on_course"]]
    assert block["rows"][0]["behind"] == ""
    second = next(r for r in block["rows"] if (r["position"] or 0) > 1)
    assert second["behind"].startswith("+")

    cfg.save({"results_unplaced": "all", "results_show_on_course": False,
              "results_show_behind": False}, target="event")
    block = m21a_block()
    assert "No Show" in [r["name"] for r in block["rows"]]
    assert block["on_course"] == [] and "behind" not in block["rows"][0]

    cfg.save({"results_unplaced": "placed"}, target="event")
    assert all(r["position"] for r in m21a_block()["rows"])


def test_live_screen_classes_and_settings(cfg):
    c = appmod.app.test_client()
    html = c.get("/live?classes=Score-O").get_data(as_text=True)
    assert 'data-name="Score-O"' in html and 'data-name="M21A"' not in html
    cfg.save({"live_title": "Club Champs", "live_rows": 2, "live_page_seconds": 0,
              "live_show_latest": False}, target="event")
    html = c.get("/live").get_data(as_text=True)
    assert "Club Champs" in html and 'data-page-seconds="0"' in html
    assert "+ 1 more" in html                       # M21A has 3 placed, 2 shown
    assert "live-ticker" not in html


def test_exports_and_entry_page_skip_or_mark_pending(scratch):
    comp = store.create_competitor({"name": "Iof Active", "class_id": m21a(),
                                    "start": "10:00:00"})
    xml = iofxml.export_results(store.evaluate()[0], store.EVENT, courses=store._courses)
    person = xml[xml.index("Active</Given>") if "Active</Given>" in xml else xml.index("Iof"):]
    assert "<Status>Active</Status>" in person.split("</PersonResult>")[0]
    results = appmod.app.test_client().get("/get-results").get_json()
    names = [c["name"] for cls in results for c in cls["competitors"]]
    assert "Iof Active" not in names
    assert store.result_for(comp["id"])["status"] == "pending"
