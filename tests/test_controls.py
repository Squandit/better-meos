"""Control statuses: bad, optional, no timing and alternate codes."""

from datetime import datetime

import pytest

import app as appmod
import controls
import results
import store
from results import linear_course_rules, parse_control_config


def _t(m, s=0):
    return datetime(2026, 5, 17, 10, m, s)


def _course(config):
    rules = parse_control_config(config)
    return {"type": "linear", "controls": [31, 32, 33], **linear_course_rules([31, 32, 33], rules)}


def _run(punches, course):
    card = {"name": "X", "class": "M", "start": _t(0), "finish": _t(40), "punches": punches}
    return results.build_result(card, course)


def test_a_bad_control_is_not_needed():
    skipped = [(31, _t(10)), (33, _t(30))]
    assert _run(skipped, _course({}))["status"] == "mp"
    assert _run(skipped, _course({"32": {"status": "bad"}}))["status"] == "ok"


def test_an_optional_control_may_be_skipped_or_punched():
    course = _course({"32": {"status": "optional"}})
    assert _run([(31, _t(10)), (33, _t(30))], course)["status"] == "ok"
    assert _run([(31, _t(10)), (32, _t(20)), (33, _t(30))], course)["status"] == "ok"
    assert _run([(31, _t(10)), (32, _t(20))], course)["missed_control"] == 33


def test_the_leg_to_a_no_timing_control_is_taken_off():
    res = _run([(31, _t(10)), (32, _t(25)), (33, _t(30))], _course({"32": {"status": "no_timing"}}))
    assert res["status"] == "ok"
    assert res["untimed_seconds"] == 15 * 60 and res["total_seconds"] == 25 * 60


def test_an_alternate_code_counts_as_the_control():
    course = _course({"32": {"alternates": [132]}})
    res = _run([(31, _t(10)), (132, _t(20)), (33, _t(30))], course)
    assert res["status"] == "ok"
    assert [code for code, _ in res["punches"]] == [31, 32, 33]


def test_parse_control_config_skips_junk():
    rules = parse_control_config({"31": {"status": "bad"}, "x": {}, "32": {"status": "weird"},
                                  "33": {"alternates": ["133", "nope", 33]}})
    assert rules == {31: {"status": "bad", "alternates": []},
                     33: {"status": "ok", "alternates": [133]}}
    assert parse_control_config(None) == {} and parse_control_config("") == {}


@pytest.fixture
def clean_controls(cfg):
    yield
    for code in list(store.control_config()):
        controls.update(code, "ok", "")


def test_marking_a_control_bad_fixes_everyone_who_missed_it(clean_controls):
    mary = next(c for c in store._competitors.values() if c["name"] == "Mispunch Mary")
    if store.result_for(mary["id"])["status"] != "mp":
        pytest.skip("another test changed Mispunch Mary")
    c = appmod.app.test_client()
    assert c.post("/api/controls/142", json={"status": "bad"}).status_code == 200
    assert store.result_for(mary["id"])["status"] == "ok"
    html = c.get("/controls").get_data(as_text=True)
    assert 'data-code="142" class="ctl-bad' in html
    assert c.post("/api/controls/142", json={"status": "ok"}).status_code == 200
    assert store.result_for(mary["id"])["status"] == "mp"
    assert c.post("/api/controls/142", json={"status": "ok", "alternates": "12a"}).status_code == 400
    assert any(a["action"] == "control changed" for a in store.audit_log(50))


def test_controls_report_lists_unknown_codes_and_misses(clean_controls):
    report = controls.report()
    row = next(r for r in report["controls"] if r["code"] == 142)
    assert row["needed"] >= 1 and row["missed"] >= 1 and row["courses"]
    assert 999 in [u["code"] for u in report["unknown"]]          # Score Sam's stray punch


def test_split_columns_leave_out_bad_controls_and_merge_leg_lengths(clean_controls):
    course = store.create_course({"name": "Lengths", "type": "linear", "controls": [51, 52, 53],
                                  "leg_lengths": [100, 200, 300]})
    try:
        controls.update(52, "bad", "")
        assert store.split_controls(store.get_course(course["id"])) == ([51, 53], [100, 500])
    finally:
        store.delete_course(course["id"])
