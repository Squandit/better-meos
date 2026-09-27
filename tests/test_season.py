"""Seasons: standings across event files, and the prize rules (places, share of
starters, classes, one prize per season)."""

import os

import pytest

import app as appmod
import config
import season
import store


def _make_event(name, date, runners):
    """A new event file in the events folder (left open) with one M21A class;
    ``runners`` maps name -> minutes (None = MP)."""
    store.new_event({"name": name, "date": date, "first_start": "10:00:00"})
    course = store.create_course({"name": "Cup course", "type": "linear", "controls": [31, 32]})
    cls = store.create_class({"name": "M21A", "course_id": course["id"]})
    for runner, minutes in runners.items():
        punches = [{"code": 31, "time": "10:05:00"}]
        if minutes is not None:
            punches.append({"code": 32, "time": "10:06:00"})
        store.create_competitor({
            "name": runner, "class_id": cls["id"], "read": True, "start": "10:00:00",
            "finish": f"10:{minutes or 30:02d}:00", "punches": punches})
    config.save({"season_name": "Test Cup"}, target="event")
    return store.current_event_path()


@pytest.fixture
def cup(cfg):
    """Three events in the season "Test Cup", the last one open. The shared test
    event is reopened afterwards."""
    home = store.current_event_path()
    made = []
    try:
        made.append(_make_event("Cup One", "2026-04-01",
                                {"Ann Apple": 20, "Bob Brown": 25, "Cid Cole": None}))
        made.append(_make_event("Cup Two", "2026-04-15",
                                {"Bob Brown": 22, "Ann Apple": 24, "Dee Dunn": 30}))
        made.append(_make_event("Cup Three", "2026-05-01",
                                {"Ann Apple": 21, "Bob Brown": 23, "Dee Dunn": 26,
                                 "Eve Earl": 28, "Fay Ford": 40}))
        yield made
    finally:
        store.open_event(home)
        for path in made:
            try:
                os.remove(path)
            except OSError:
                pass


def _row(table, name):
    return next(r for c in table["classes"] for r in c["rows"] if r["name"] == name)


def test_standings_by_points_table(cup):
    config.save({"season_points": "10, 6, 4", "season_finish_points": 1,
                 "season_start_points": 0}, target="event")
    table = season.standings("Test Cup")
    assert [e["name"] for e in table["events"]] == ["Cup One", "Cup Two", "Cup Three"]
    ann, bob = _row(table, "Ann Apple"), _row(table, "Bob Brown")
    assert ann["points"] == [10, 6, 10] and ann["total"] == 26 and ann["position"] == 1
    assert bob["points"] == [6, 10, 6] and bob["total"] == 22 and bob["position"] == 2
    assert _row(table, "Eve Earl")["points"] == [None, None, 1]      # past the table
    # Cid only mispunched and MP scores nothing: not in the table at all...
    assert all(r["name"] != "Cid Cole" for c in table["classes"] for r in c["rows"])
    # ...until turning up is worth something.
    config.save({"season_start_points": 2}, target="event")
    assert _row(season.standings("Test Cup"), "Cid Cole")["points"] == [2, None, None]


def test_best_of_and_minimum_events(cup):
    config.save({"season_points": "10, 6, 4", "season_best_of": 2, "season_min_events": 2},
                target="event")
    table = season.standings("Test Cup")
    ann, bob, dee = (_row(table, n) for n in ("Ann Apple", "Bob Brown", "Dee Dunn"))
    assert ann["total"] == 20 and ann["counted"] == [True, False, True]
    assert bob["total"] == 16
    assert dee["events_run"] == 2 and dee["position"] is not None
    fay = _row(table, "Fay Ford")
    assert fay["events_run"] == 1 and fay["position"] is None       # needs 2 events


def test_time_ratio_scoring(cup):
    config.save({"season_scoring": "time_ratio", "season_max_points": 1000}, target="event")
    table = season.standings("Test Cup")
    # Cup Three: Ann won in 21:00, Dee ran 26:00 -> 1000 * 21 / 26.
    assert _row(table, "Ann Apple")["points"][2] == 1000
    assert _row(table, "Dee Dunn")["points"][2] == round(1000 * 21 / 26)


def test_one_prize_per_season_passes_earlier_winners_over(cup):
    config.save({"prize_places": 2}, target="event")
    plain = season.prizes_for_open_event()["classes"][0]
    assert [w["name"] for w in plain["winners"]] == ["Ann Apple", "Bob Brown"]

    config.save({"prize_one_per_season": True}, target="event")
    # Earlier events have their own rules; give them the same so the chain is
    # Cup One: Ann, Bob. Cup Two: Dee (Ann and Bob already won).
    for path in cup[:2]:
        store.open_event(path)
        config.save({"prize_places": 2, "prize_one_per_season": True}, target="event")
    store.open_event(cup[2])
    prizes = season.prizes_for_open_event()
    m21a = prizes["classes"][0]
    assert prizes["earlier"] == ["Cup One", "Cup Two"]
    assert [w["name"] for w in m21a["winners"]] == ["Eve Earl", "Fay Ford"]
    passed = {p["name"]: p["won_at"] for p in m21a["passed_over"]}
    assert passed == {"Ann Apple": "Cup One", "Bob Brown": "Cup One", "Dee Dunn": "Cup Two"}
    html = appmod.app.test_client().get("/prizes").get_data(as_text=True)
    assert "Passed over" in html and "prize at Cup One" in html


def test_prize_share_and_classes(cup):
    config.save({"prize_places": 5, "prize_share": 40}, target="event")
    m21a = season.prizes_for_open_event()["classes"][0]
    assert m21a["starters"] == 5 and m21a["prizes"] == 2           # 40% of 5
    config.save({"prize_classes": "W21A"}, target="event")
    assert season.prizes_for_open_event()["classes"] == []


def test_season_name_is_per_event_only(cup):
    config.save({"season_name": "Everything"}, target="computer")   # ignored
    store.open_event(cup[0])
    assert config.get("season_name") == "Test Cup"
    config.save({"season_name": ""}, target="event")
    assert config.get("season_name") == ""                         # no default
    assert "Test Cup" in season.season_names()


def test_pages_render(cup):
    c = appmod.app.test_client()
    html = c.get("/season").get_data(as_text=True)
    assert "Cup One" in html and "Ann Apple" in html
    assert c.get("/export/prizes.pdf").status_code == 200
