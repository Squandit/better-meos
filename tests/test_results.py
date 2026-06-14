"""Unit tests for the pure result engine (no Flask, no DB)."""

from datetime import datetime

import results
from results import (
    STATUS_DNF, STATUS_DNS, STATUS_MP, STATUS_OK, STATUS_OOT,
    build_result, build_splits_matrix, calculate_splits, format_duration,
    format_split, rank_results, score_points, validate_linear,
)


def t(h, m, s=0):
    return datetime(2026, 5, 17, h, m, s)


def test_format_duration():
    assert format_duration(0) == "00:00:00"
    assert format_duration(3661) == "01:01:01"
    assert format_duration(-65) == "-00:01:05"


def test_calculate_splits_legs_and_finish():
    splits = calculate_splits(t(9, 0), [(138, t(9, 5)), (130, t(9, 12))], t(9, 20))
    assert [s["control"] for s in splits] == [138, 130, "F"]
    assert splits[0]["leg_seconds"] == 300
    assert splits[1]["leg_seconds"] == 420
    assert splits[1]["cumulative_seconds"] == 720
    assert splits[2]["control"] == "F"
    assert splits[2]["cumulative_seconds"] == 1200


def test_validate_linear_ok_and_tolerant_of_extras():
    # extra/spurious punch (99) ignored; required subsequence still present
    status, missed = validate_linear([138, 130, 142], [138, 99, 130, 142])
    assert status == STATUS_OK and missed is None


def test_validate_linear_detects_missed_control():
    status, missed = validate_linear([138, 130, 142], [138, 142])
    assert status == STATUS_MP and missed == 130


def test_score_points_counts_each_control_once():
    values = {138: 30, 130: 30, 142: 40}
    assert score_points(values, [138, 138, 130, 999, 142]) == 100


def test_build_result_dns_when_no_start():
    course = {"type": "linear", "controls": [138]}
    r = build_result({"name": "x", "class": "A", "start": None, "finish": t(10, 0),
                      "punches": []}, course)
    assert r["status"] == STATUS_DNS


def test_build_result_dnf_when_no_finish():
    course = {"type": "linear", "controls": [138]}
    r = build_result({"name": "x", "class": "A", "start": t(9, 0), "finish": None,
                      "punches": [(138, t(9, 5))]}, course)
    assert r["status"] == STATUS_DNF


def test_build_result_score_oot_penalty():
    course = {"type": "score", "controls": {138: 50}, "time_limit_minutes": 10,
              "penalty_per_minute": 10}
    # 12:30 over an hmm 10-minute limit -> 3 started minutes over -> 30 pts off
    r = build_result({"name": "x", "class": "A", "start": t(9, 0),
                      "finish": t(9, 12, 30), "punches": [(138, t(9, 5))]}, course)
    assert r["status"] == STATUS_OOT
    assert r["points"] == 50 - 30


def test_build_result_manual_override_keeps_time():
    course = {"type": "linear", "controls": [138]}
    r = build_result({"name": "x", "class": "A", "start": t(9, 0), "finish": t(9, 30),
                      "punches": [(138, t(9, 5))], "manual_status": "dsq"}, course)
    assert r["status"] == "dsq" and r["manual"] is True
    assert r["auto_status"] == STATUS_OK
    assert r["total_seconds"] == 1800  # time still recorded


def test_rank_results_ties_share_position():
    rows = [
        {"class": "A", "course_type": "linear", "status": STATUS_OK, "points": None,
         "total_seconds": 100},
        {"class": "A", "course_type": "linear", "status": STATUS_OK, "points": None,
         "total_seconds": 100},
        {"class": "A", "course_type": "linear", "status": STATUS_OK, "points": None,
         "total_seconds": 200},
        {"class": "A", "course_type": "linear", "status": STATUS_MP, "points": None,
         "total_seconds": 50},
    ]
    ranked = rank_results(rows)["A"]
    positions = [r["position"] for r in ranked]
    assert positions == [1, 1, 3, None]


def test_format_split_compact():
    assert format_split(65) == "1:05"
    assert format_split(3725) == "1:02:05"
    assert format_split(None) == ""


def _linear(pid, name, finish, punches):
    return build_result({"id": pid, "name": name, "class": "X", "start": t(9, 0),
                         "finish": finish, "punches": punches},
                        {"type": "linear", "controls": [138, 130]})


def test_splits_matrix_ranks_and_best_leg():
    a = _linear(1, "A", t(9, 20), [(138, t(9, 5)), (130, t(9, 12))])
    b = _linear(2, "B", t(9, 25), [(138, t(9, 4)), (130, t(9, 15))])
    m = build_splits_matrix([a, b], [138, 130])

    assert [leg["label"] for leg in m["legs"]] == ["138", "130", "F"]
    row_a = next(r for r in m["rows"] if r["name"] == "A")
    row_b = next(r for r in m["rows"] if r["name"] == "B")

    # Leg to 138: A=5:00, B=4:00 -> B fastest.
    assert row_b["cells"][0]["best_leg"] is True
    assert row_a["cells"][0]["best_leg"] is False
    assert row_a["cells"][0]["leg_rank"] == 2
    assert row_a["cells"][0]["leg_behind"] == "1:00"
    # Leg to 130: A=7:00, B=11:00 -> A fastest.
    assert row_a["cells"][1]["best_leg"] is True


def test_splits_matrix_missing_control_is_blank():
    a = _linear(1, "A", t(9, 20), [(138, t(9, 5))])  # skipped 130 (mispunch)
    m = build_splits_matrix([a], [138, 130])
    row = m["rows"][0]
    assert row["cells"][1]["missing"] is True   # the 130 column
    assert row["cells"][0]["missing"] is False  # 138 present
    assert row["cells"][2]["missing"] is False  # Finish present


def test_splits_matrix_handles_repeated_control_code():
    # Butterfly/loop course visits 138 twice; the two columns must show
    # distinct splits, not the same (last) one.
    course = {"type": "linear", "controls": [138, 130, 138]}
    a = build_result({"id": 1, "name": "A", "class": "X", "start": t(9, 0),
                      "finish": t(9, 25),
                      "punches": [(138, t(9, 5)), (130, t(9, 12)), (138, t(9, 20))]},
                     course)
    m = build_splits_matrix([a], [138, 130, 138])
    cells = m["rows"][0]["cells"]
    assert [leg["label"] for leg in m["legs"]] == ["138", "130", "138", "F"]
    # First 138 visit at +5:00, second at +20:00 cumulative — must differ.
    assert cells[0]["cum"] == "5:00"
    assert cells[2]["cum"] == "20:00"
    assert cells[0]["cum"] != cells[2]["cum"]


def test_splits_matrix_recovers_after_missed_middle_control():
    # Runner misses control 2 of [1,2,3] but still punches 1 and 3 in order;
    # the 3 column should still show a split, not be lost to the gap.
    course = {"type": "linear", "controls": [101, 102, 103]}
    a = build_result({"id": 1, "name": "A", "class": "X", "start": t(9, 0),
                      "finish": t(9, 30),
                      "punches": [(101, t(9, 5)), (103, t(9, 20))]}, course)
    m = build_splits_matrix([a], [101, 102, 103])
    cells = m["rows"][0]["cells"]
    assert cells[0]["missing"] is False   # 101
    assert cells[1]["missing"] is True    # 102 skipped
    assert cells[2]["missing"] is False   # 103 still recorded
    assert cells[2]["cum"] == "20:00"
