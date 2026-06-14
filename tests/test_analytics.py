"""Analytics (pure stats) and the AI layer's rule-based fallback."""

from datetime import datetime

import ai
import analytics
from results import build_result


def t(h, m, s=0):
    return datetime(2026, 5, 17, h, m, s)


def _linear(pid, name, finish, punches, club=""):
    return build_result({"id": pid, "name": name, "class": "X", "club": club,
                         "start": t(9, 0), "finish": finish, "punches": punches},
                        {"type": "linear", "controls": [138, 130]})


def test_class_leg_stats_average_and_best():
    a = _linear(1, "A", t(9, 20), [(138, t(9, 5)), (130, t(9, 12))])   # legs 300, 420
    b = _linear(2, "B", t(9, 25), [(138, t(9, 7)), (130, t(9, 17))])   # legs 420, 600
    stats = {s["control"]: s for s in analytics.class_leg_stats([a, b], [138, 130])}
    assert stats[138]["best_seconds"] == 300
    assert stats[138]["avg_seconds"] == 360       # (300+420)/2
    assert stats[130]["count"] == 2


def test_competitor_legs_flags_weakness():
    # B is much slower than A on the 130 leg -> weak flag.
    a = _linear(1, "A", t(9, 20), [(138, t(9, 5)), (130, t(9, 10))])   # 130 leg 300
    b = _linear(2, "B", t(9, 40), [(138, t(9, 6)), (130, t(9, 30))])   # 130 leg 1440
    data = analytics.competitor_legs([a, b], [138, 130], competitor_id=2)
    leg130 = next(l for l in data["legs"] if l["control"] == 130)
    assert leg130["weak"] is True
    assert any(w["control"] == 130 for w in data["weak_legs"])


def test_shared_legs_detected_across_courses():
    courses = [
        {"name": "Hard", "type": "linear", "controls": [101, 102, 103]},
        {"name": "Easy", "type": "linear", "controls": [102, 103, 104]},  # shares 102->103
    ]
    shared = analytics.shared_legs(courses)
    legs = {s["leg"] for s in shared}
    assert (102, 103) in legs


def test_course_checks_flags_back_to_back():
    findings = analytics.course_checks(
        {"type": "linear", "controls": [138, 138, 130]})
    assert any(f["level"] == "error" for f in findings)


def test_training_advice_rule_based(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    weak = [{"control": 130, "pct_vs_avg": 0.8}]
    out = ai.training_advice("A", weak)
    assert out["source"] == "rules"
    assert "control 130" in out["text"]


def test_course_review_rule_based(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    findings = [{"level": "error", "message": "Control 138 is visited twice back-to-back."}]
    out = ai.course_review("Hard", findings, [])
    assert out["source"] == "rules"
    assert "back-to-back" in out["text"]


def test_ai_routes(client_unused=None):
    import app as appmod
    c = appmod.app.test_client()
    import store
    cid = next(iter(store._courses))
    assert c.get(f"/api/courses/{cid}/review").status_code == 200
    clsid = next(iter(store._classes))
    assert c.get(f"/api/classes/{clsid}/legs").status_code == 200
    assert c.get("/api/profile/advice?name=Test%20Runner").status_code == 200
