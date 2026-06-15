"""Ops/auth/polish: login gating, multi-station reads, dashboard, sync."""

from datetime import datetime

import app as appmod
import auth
import si_reader
import store


def test_station_id_recorded_on_read():
    out = si_reader.simulate({
        "card_number": 8500002,  # seeded Tie Tina
        "start": datetime(2026, 5, 17, 9, 0, 0),
        "finish": datetime(2026, 5, 17, 10, 0, 0),
        "punches": [(138, datetime(2026, 5, 17, 9, 10, 0))],
    }, station_id="finish")
    assert out["ok"] is True
    comp = store.find_by_card(8500002)
    assert comp["punches"][0]["station_id"] == "finish"
    recent = si_reader.recent_reads()
    assert recent[0]["station"] == "finish"
    assert recent[0]["name"] == comp["name"]


def test_sync_endpoints():
    c = appmod.app.test_client()
    assert c.get("/api/sync/export").status_code == 200
    # import with no file -> 400 (delegates to restore)
    assert c.post("/api/sync/import").status_code == 400


def test_auth_disabled_by_default():
    # No BMEOS_AUTH in the test env -> operator pages are open.
    c = appmod.app.test_client()
    assert c.get("/competitors").status_code == 200


def test_auth_gating_when_enabled(monkeypatch):
    monkeypatch.setenv("BMEOS_AUTH", "1")
    if auth.verify("operator1", "secret") is None and store.db.get_user("operator1") is None:
        auth.create_user("operator1", "secret")

    c = appmod.app.test_client()
    # protected browser page -> redirect to login
    assert c.get("/competitors").status_code == 302
    # protected API -> 401
    assert c.get("/api/courses/1").status_code == 401
    # public surfaces remain open
    assert c.get("/enter").status_code == 200
    assert c.get("/login").status_code == 200

    # bad creds rejected
    assert c.post("/login", data={"username": "operator1", "password": "nope"}).status_code == 401
    # good creds -> session, then access granted
    r = c.post("/login", data={"username": "operator1", "password": "secret"})
    assert r.status_code == 302
    assert c.get("/competitors").status_code == 200


def test_club_role_is_read_only(monkeypatch):
    monkeypatch.setenv("BMEOS_AUTH", "1")
    if store.db.get_user("club1") is None:
        auth.create_user("club1", "secret", role="club")
    c = appmod.app.test_client()
    c.post("/login", data={"username": "club1", "password": "secret"})
    # reads allowed
    assert c.get("/competitors").status_code == 200
    # mutations blocked for non-operator role
    r = c.post("/api/classes", json={"name": "X", "course_id": 1})
    assert r.status_code == 403
