"""The download desk: class guessing, quick entry for unknown cards, the
unknown-card setting, stale check punches, auto-print and split slips."""

from datetime import datetime

import pytest

import app as appmod
import display
import results
import si_reader
import store


def _dt(h, m, s=0):
    return datetime.combine(store.EVENT_DATE, datetime.min.time()).replace(hour=h, minute=m, second=s)


@pytest.fixture
def scratch():
    comps, classes, courses = set(store._competitors), set(store._classes), set(store._courses)
    yield
    for cid in set(store._competitors) - comps:
        store.delete_competitor(cid)
    for cid in set(store._classes) - classes:
        store.delete_class(cid)
    for cid in set(store._courses) - courses:
        store.delete_course(cid)


def test_class_guess_prefers_the_course_that_explains_the_punches(scratch):
    short = store.create_course({"name": "Short guess", "type": "linear", "controls": [138, 142]})
    store.create_class({"name": "Short class", "course_id": short["id"]})
    guesses = store.suggest_classes([138, 130, 142, 155])
    # Both are punched completely, but M21A's course accounts for every punch.
    assert guesses[0]["name"] == "M21A" and guesses[0]["complete"]
    assert "Short class" in [g["name"] for g in guesses]
    assert store.suggest_classes([]) == []
    partial = store.suggest_classes([138, 130])
    assert partial[0]["reason"].startswith("2 of")


def test_quick_entry_from_an_unknown_card(scratch):
    card = {"card_number": 9400001, "start": _dt(10, 0), "finish": _dt(10, 50),
            "punches": [(138, _dt(10, 10)), (130, _dt(10, 20)), (142, _dt(10, 30)),
                        (155, _dt(10, 40))]}
    outcome = si_reader.simulate(card)
    assert outcome["ok"] is False and outcome["read_id"]
    html = appmod.app.test_client().get("/download").get_data(as_text=True)
    assert "all 4 controls in order" in html                    # class suggestion shown

    c = appmod.app.test_client()
    bad = c.post(f"/api/card-reads/{outcome['read_id']}/enter", json={"name": "", "class_id": 1})
    assert bad.status_code == 400
    ok = c.post(f"/api/card-reads/{outcome['read_id']}/enter", json={
        "name": "Walk Up", "club": "NEWOC",
        "class_id": next(x["id"] for x in store.class_options() if x["name"] == "M21A")})
    assert ok.status_code == 200
    comp = store.find_by_card(9400001)
    assert comp["name"] == "Walk Up"
    assert store.result_for(comp["id"])["status"] == "ok"
    assert all(u["card_number"] != 9400001 for u in store.unmatched_reads())
    again = c.post(f"/api/card-reads/{outcome['read_id']}/enter", json={"name": "Twice", "class_id": 1})
    assert again.status_code == 400                              # already dealt with


def test_unknown_card_setting_enters_cards_automatically(cfg, scratch):
    cfg.save({"unknown_card_action": "auto_create"}, target="event")
    card = {"card_number": 9400002, "start": _dt(11, 0), "finish": _dt(11, 30),
            "punches": [(71, _dt(11, 10)), (72, _dt(11, 20))]}
    outcome = si_reader.simulate(card)
    assert outcome["ok"] and outcome.get("auto_created")
    assert store.find_by_card(9400002) is not None


def test_a_check_punch_after_the_start_is_not_this_runs():
    course = {"type": "linear", "controls": [31, 32]}
    # Card last checked at 14:00 some other day, run today 10:00-10:40.
    card = {"name": "Old Check", "class": "M", "start": _dt(10, 0), "finish": _dt(10, 40),
            "check": _dt(14, 0), "punches": [(31, _dt(10, 10)), (32, _dt(10, 20))]}
    res = results.build_result(card, course)
    assert res["status"] == "ok" and res["ignored_punches"] == 0


def test_split_slip_shows_course_order_legs_places_and_footer(cfg, scratch):
    m21a = next(x["id"] for x in store.class_options() if x["name"] == "M21A")
    runner = store.create_competitor({
        "name": "Slip Sam", "class_id": m21a, "read": True, "start": "10:00:00",
        "finish": "10:45:00", "punches": [{"code": c, "time": f"10:{10 * (i + 1)}:00"}
                                          for i, c in enumerate((138, 130, 142, 155))]})
    view = display.slip_view(runner["id"])
    assert [leg["code"] for leg in view["legs"]] == [138, 130, 142, 155, ""]
    assert all("leg_rank" in leg for leg in view["legs"])
    assert view["place"] and view["of"]
    cfg.save({"slip_footer": "Thanks for running!", "slip_leg_places": False}, target="event")
    html = appmod.app.test_client().get(f"/slip/{runner['id']}").get_data(as_text=True)
    assert "Thanks for running!" in html and "<th class=\"num\">Place</th>" not in html
    missed = store.create_competitor({
        "name": "Slip Missed", "class_id": m21a, "read": True, "start": "10:00:00",
        "finish": "10:45:00", "punches": [{"code": 138, "time": "10:10:00"}]})
    html = appmod.app.test_client().get(f"/slip/{missed['id']}").get_data(as_text=True)
    assert "missing" in html


def test_download_page_carries_reads_for_auto_print(cfg):
    cfg.save({"auto_print": "ok"}, target="computer")
    html = appmod.app.test_client().get("/download").get_data(as_text=True)
    assert '<option value="ok" selected>' in html
    assert "data-reads" in html and "printing.js" in html
