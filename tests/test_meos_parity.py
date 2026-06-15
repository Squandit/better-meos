"""
MeOS-parity backends that previously had no UI/tests: mass start, chase
(handicap) start, patrol teams, custom score formulas, auto-create-from-card,
and multi-stage combined results.

Each test runs in its own throwaway event file (so it never pollutes the shared
seeded event), restoring the original event on teardown.
"""

import pytest

import db
import stages
import store


@pytest.fixture
def temp_event(tmp_path, monkeypatch):
    monkeypatch.setenv("BMEOS_EVENTS_DIR", str(tmp_path))
    original = store.current_event_path()
    info = store.new_event({"name": "Parity", "date": "2026-05-17",
                            "first_start": "09:00:00", "type": "linear"})
    try:
        yield info
    finally:
        store.open_event(original)


def test_mass_start_times_everyone_from_the_gun(temp_event):
    course = store.create_course({
        "name": "Mass", "type": "linear", "controls": [31],
        "start_mode": "mass", "mass_start": "10:00:00"})
    cls = store.create_class({"name": "MassC", "course_id": course["id"]})
    comp = store.create_competitor({
        "name": "Gun Gail", "class_id": cls["id"], "card_number": 7000001,
        "finish": "10:30:00", "punches": [{"code": 31, "time": "10:10:00"}]})
    res = store.result_for(comp["id"])
    assert res["status"] == "ok"
    assert res["total_seconds"] == 30 * 60     # 10:00 gun -> 10:30 finish


def test_custom_score_formula_overrides_points(temp_event):
    course = store.create_course({
        "name": "ScoreF", "type": "score",
        "controls": [{"code": 31, "points": 10}, {"code": 32, "points": 10}],
        "time_limit_minutes": 60, "penalty_per_minute": 5,
        "score_formula": "controls * 100"})
    cls = store.create_class({"name": "ScoreFC", "course_id": course["id"]})
    comp = store.create_competitor({
        "name": "Formula Fran", "class_id": cls["id"], "card_number": 7000002,
        "start": "13:00:00", "finish": "13:30:00",
        "punches": [{"code": 31, "time": "13:10:00"}, {"code": 32, "time": "13:20:00"}]})
    res = store.result_for(comp["id"])
    assert res["points"] == 200                # 2 controls * 100, not the 20 base


def test_patrol_team_combines_members_into_one_run(temp_event):
    course = store.create_course({"name": "PatrolC", "type": "linear", "controls": [31, 32]})
    cls = store.create_class({"name": "PatrolClass", "course_id": course["id"], "kind": "patrol"})
    team = store.create_team({"name": "The Pair", "class_id": cls["id"]})
    store.create_competitor({
        "name": "A", "class_id": cls["id"], "card_number": 7000010, "team_id": team["id"], "leg": 1,
        "start": "10:00:00", "finish": "10:20:00", "punches": [{"code": 31, "time": "10:05:00"}]})
    store.create_competitor({
        "name": "B", "class_id": cls["id"], "card_number": 7000011, "team_id": team["id"], "leg": 2,
        "start": "10:02:00", "finish": "10:25:00", "punches": [{"code": 32, "time": "10:10:00"}]})
    classes = store.team_results()
    patrol = next(c for c in classes if c["class"]["id"] == cls["id"])
    result = patrol["teams"][0]
    assert result["ok"] is True
    assert result["total_seconds"] == 25 * 60   # earliest start 10:00 -> latest finish 10:25


def test_auto_create_from_unknown_card(temp_event):
    card = {
        "card_number": 7000020,
        "start": store.parse_clock("11:00:00"),
        "finish": store.parse_clock("11:20:00"),
        "punches": [(41, store.parse_clock("11:05:00")), (42, store.parse_clock("11:12:00"))],
    }
    comp = store.auto_create_from_card(card)
    assert comp["card_number"] == 7000020
    assert store.find_by_card(7000020) is not None
    # A course (from the punched sequence) and a class were created for it.
    assert any(c["controls"] == [41, 42] for c in store._courses.values())


def _seed_stage(path_info, *, finish_b):
    """Helper: fill the OPEN event with a leader (A, 30 min) and B (finish_b)."""
    course = store.create_course({"name": "S", "type": "linear", "controls": [31]})
    cls = store.create_class({"name": "Open", "course_id": course["id"]})
    store.create_competitor({"name": "Leader Lou", "club": "OC", "class_id": cls["id"],
                             "card_number": 100, "start": "10:00:00", "finish": "10:30:00",
                             "punches": [{"code": 31, "time": "10:10:00"}]})
    store.create_competitor({"name": "Chaser Chris", "club": "OC", "class_id": cls["id"],
                             "card_number": 101, "start": "10:00:00", "finish": finish_b,
                             "punches": [{"code": 31, "time": "10:10:00"}]})


def test_combined_results_and_chase_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("BMEOS_EVENTS_DIR", str(tmp_path))
    original = store.current_event_path()
    try:
        # Stage 1: Lou 30:00, Chris 35:00 (5 min behind).
        stage1 = store.new_event({"name": "Day1", "date": "2026-05-17",
                                  "first_start": "10:00:00", "type": "linear"})
        _seed_stage(stage1, finish_b="10:35:00")
        p1 = stage1["path"]

        combined = stages.combined_results([p1])
        rows = next(c["rows"] for c in combined if c["class"] == "Open")
        lou = next(r for r in rows if r["name"] == "Leader Lou")
        chris = next(r for r in rows if r["name"] == "Chaser Chris")
        assert lou["position"] == 1 and chris["position"] == 2
        assert chris["total_seconds"] - lou["total_seconds"] == 5 * 60

        # Stage 2: same runners (matched by card); set chase starts from stage 1.
        store.new_event({"name": "Day2", "date": "2026-05-18",
                         "first_start": "12:00:00", "type": "linear"})
        course = store.create_course({"name": "S2", "type": "linear", "controls": [31]})
        cls = store.create_class({"name": "Open", "course_id": course["id"]})
        store.create_competitor({"name": "Leader Lou", "club": "OC", "class_id": cls["id"],
                                 "card_number": 100})
        store.create_competitor({"name": "Chaser Chris", "club": "OC", "class_id": cls["id"],
                                 "card_number": 101})
        assigned = stages.apply_chase_starts([p1], "12:00:00")
        assert assigned == 2
        starts = {c["card_number"]: store.format_clock(c["start"])
                  for c in store._competitors.values()}
        assert starts[100] == "12:00:00"      # leader off first
        assert starts[101] == "12:05:00"      # 5 min behind -> starts 5 min later
    finally:
        store.open_event(original)
