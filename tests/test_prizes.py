"""Season prize ledger: one prize per person per season, cascading per course."""

import os

import pytest

import app as appmod
import config
import prizes


@pytest.fixture
def fresh_prizes(tmp_path):
    """Per-test prize DB + config (season) so awards don't leak between tests."""
    prev = {k: os.environ.get(k) for k in ("BMEOS_PRIZES_DB", "BMEOS_CONFIG")}
    os.environ["BMEOS_PRIZES_DB"] = str(tmp_path / "prizes.db")
    os.environ["BMEOS_CONFIG"] = str(tmp_path / "config.json")
    prizes.reset_connection()
    config.reload()
    prizes.set_season("TST")
    try:
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        prizes.reset_connection()
        config.reload()


def test_person_key_prefers_card_then_name():
    assert prizes.person_key("Al", "OC", 123) == "card:123"
    assert prizes.person_key("Al", "OC", None) == "name:al|oc"


def test_one_prize_per_person_per_season(fresh_prizes):
    al = {"name": "Al", "club": "OC", "card_number": 1}
    assert prizes.award("TST", al, "Long") is True
    assert prizes.has_won("TST", "card:1") is True
    # A second prize for the same person (any course) is refused.
    assert prizes.award("TST", al, "Short") is False
    assert len(prizes.ledger("TST")) == 1


def test_recommend_cascades_past_an_existing_winner(fresh_prizes):
    prizes.award("TST", {"name": "Al", "club": "OC", "card_number": 1}, "Long")
    standings = [{"course_name": "Long", "rows": [
        {"name": "Al", "club": "OC", "card": 1, "position": 1},
        {"name": "Bo", "club": "OC", "card": 2, "position": 2}]}]
    rec = prizes.recommend(standings, "TST")[0]
    assert rec["recommended"]["name"] == "Bo"
    assert "Al" in rec["skipped"]


def test_recommend_no_one_wins_two_courses_at_one_event(fresh_prizes):
    standings = [
        {"course_name": "A", "rows": [{"name": "Al", "club": "OC", "card": 1, "position": 1}]},
        {"course_name": "B", "rows": [
            {"name": "Al", "club": "OC", "card": 1, "position": 1},
            {"name": "Cy", "club": "OC", "card": 3, "position": 2}]},
    ]
    recs = prizes.recommend(standings, "TST")
    assert recs[0]["recommended"]["name"] == "Al"
    assert recs[1]["recommended"]["name"] == "Cy"   # Al already taken this event


def test_changing_season_resets_eligibility(fresh_prizes):
    prizes.award("TST", {"name": "Al", "club": "OC", "card_number": 1}, "Long")
    assert prizes.has_won("TST", "card:1") is True
    prizes.set_season("2099")
    assert prizes.current_season() == "2099"
    assert prizes.has_won("2099", "card:1") is False


def test_unaward_and_clear(fresh_prizes):
    prizes.award("TST", {"name": "Al", "card_number": 1}, "Long")
    prizes.unaward("TST", "card:1")
    assert prizes.has_won("TST", "card:1") is False
    prizes.award("TST", {"name": "Bo", "card_number": 2}, "Short")
    assert prizes.clear("TST") == 1
    assert prizes.ledger("TST") == []


def test_prizes_page_and_award_endpoint(fresh_prizes):
    c = appmod.app.test_client()
    assert c.get("/prizes").status_code == 200
    r = c.post("/api/prizes/award", json={
        "course_name": "M21A course", "name": "Fast Ferdy",
        "club": "TESTOC", "card_number": 8500001})
    assert r.status_code == 200 and r.get_json()["awarded"] is True
    assert any(w["name"] == "Fast Ferdy" for w in prizes.ledger("TST"))
