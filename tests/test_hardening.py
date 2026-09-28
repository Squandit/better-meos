"""Security hardening + the day-of-event fixes (formulas, SSE, punches, reads, relays)."""

from datetime import datetime

from flask.sessions import SecureCookieSessionInterface

import app as appmod
import config
import events
import results
import rules
import si_reader
import store

PUBLIC = "8800"


def _dt(h, m, s=0):
    return datetime.combine(store.EVENT_DATE, datetime.min.time()).replace(
        hour=h, minute=m, second=s)


# --- Session key / admin lock -------------------------------------------------

def test_session_key_is_not_a_known_default():
    assert appmod.app.secret_key != "dev-insecure-key"
    assert len(appmod.app.secret_key) >= 32


def test_forged_cookie_with_old_default_key_is_rejected(cfg):
    cfg.set_admin_password("pw")
    old = appmod.app.secret_key
    try:
        appmod.app.secret_key = "dev-insecure-key"
        forged = SecureCookieSessionInterface().get_signing_serializer(
            appmod.app).dumps({"admin_ok": True})
    finally:
        appmod.app.secret_key = old
    c = appmod.app.test_client()
    c.set_cookie("session", forged)
    assert c.get("/api/config").status_code == 401


def test_config_api_never_returns_secrets(cfg):
    cfg.save({"smtp_pass": "gmail-app-pw", "ngrok_authtoken": "ngrok-tok",
              "station_token": "st-tok"})
    body = appmod.app.test_client().get("/api/config").get_data(as_text=True)
    for secret in ("gmail-app-pw", "ngrok-tok", "st-tok"):
        assert secret not in body
    cfg.save({"smtp_pass": ""})              # blank = keep
    assert cfg.get_str("smtp_pass") == "gmail-app-pw"


# --- LAN exposure / CSRF / redirects ------------------------------------------

def test_console_refuses_other_computers_without_password(cfg):
    c = appmod.app.test_client()
    lan = {"REMOTE_ADDR": "192.168.1.50"}
    assert c.get("/competitors", environ_overrides=lan).status_code == 403
    assert c.get("/competitors").status_code == 200           # this PC
    cfg.set_admin_password("pw")                              # with a password:
    assert c.get("/competitors", environ_overrides=lan).status_code == 302  # -> unlock


def test_cross_site_posts_are_refused():
    c = appmod.app.test_client()
    evil = {"Origin": "https://evil.example"}
    assert c.post("/api/events/close", headers=evil).status_code == 403
    assert c.post("/api/events/close",
                  headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # The public port's page can't drive the admin port either (same-site).
    assert c.post("/api/courses", json={},
                  headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    # Our own pages still work.
    r = c.post("/api/preview", json={}, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 400   # reached the view (validation error), not blocked


def test_open_redirects_are_blocked(cfg):
    cfg.set_admin_password("pw")
    c = appmod.app.test_client()
    for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
        r = c.post("/unlock", data={"password": "pw", "next": target})
        assert r.status_code == 302 and "evil" not in r.headers["Location"]
    r = c.post("/unlock", data={"password": "pw", "next": "/competitors"})
    assert r.headers["Location"].endswith("/competitors")


def test_public_port_no_longer_serves_slips_or_raw_entries():
    c = appmod.app.test_client()
    env = {"SERVER_PORT": PUBLIC}
    comp_id = next(iter(store._competitors))
    assert c.get(f"/slip/{comp_id}", environ_overrides=env).status_code == 404
    assert c.post("/api/entries", json={}, environ_overrides=env).status_code == 404
    assert c.get("/api/version", environ_overrides=env).status_code == 200


def test_station_token(cfg):
    card = {"card_number": 8635918, "punches": [], "finish": "10:49:53"}
    cfg.set_admin_password("pw")
    c = appmod.app.test_client()
    lan = {"REMOTE_ADDR": "192.168.1.60"}
    assert c.post("/api/station/push", json=card, environ_overrides=lan).status_code == 401
    cfg.save({"station_token": "tok-123"})
    bad = c.post("/api/station/push", json=card, environ_overrides=lan,
                 headers={"X-Station-Token": "nope"})
    assert bad.status_code == 401
    good = c.post("/api/station/push", json=card, environ_overrides=lan,
                  headers={"X-Station-Token": "tok-123"})
    assert good.status_code == 200
    # The token opens only the station endpoints, nothing else.
    other = c.get("/api/config", environ_overrides=lan,
                  headers={"X-Station-Token": "tok-123"})
    assert other.status_code == 401


def test_only_event_files_can_be_opened(tmp_path):
    other = tmp_path / "notes.db"
    other.write_bytes(b"")
    r = appmod.app.test_client().post("/api/events/open", json={"path": str(other)})
    assert r.status_code == 400


# --- Scoring formulas -----------------------------------------------------------

def test_formula_errors_never_escape_as_crashes():
    for bad in ("points / over_minutes", "9**9**9", "int(1e400)", "(10**10)**10",
                "x" * 400):
        try:
            rules.validate_formula(bad)
            assert False, bad
        except rules.RuleError:
            pass


def test_runtime_formula_error_falls_back_to_points():
    course = {"type": "score", "controls": {31: 10}, "time_limit_minutes": 60,
              "penalty_per_minute": 0, "score_formula": "points / minutes"}
    card = {"name": "Zero", "class": "S", "start": _dt(10, 0), "finish": _dt(10, 0),
            "punches": [(31, _dt(10, 0))]}
    res = results.build_result(card, course)       # used to raise ZeroDivisionError
    assert res["points"] == 10


def test_store_rejects_bad_formula_with_400():
    c = appmod.app.test_client()
    r = c.post("/api/courses", json={"name": "Bad F", "type": "score",
                                     "controls": [{"code": 31, "points": 10}],
                                     "score_formula": "points / over_minutes"})
    assert r.status_code == 400 and "zero" in r.get_json()["error"]


# --- Live updates -----------------------------------------------------------------

def test_stream_cap_and_version(monkeypatch):
    monkeypatch.setattr(events, "MAX_STREAMS", 1)
    q = events.subscribe("9999")
    try:
        assert events.subscribe("9999") is None      # over the cap
        assert events.next_message(q, timeout=0.01) is None   # keep-alive tick
        before = events.version()
        events.publish("test")
        assert events.version() == before + 1
        assert events.next_message(q, timeout=0.01) is not None
    finally:
        events.unsubscribe(q, "9999")
    q2 = events.subscribe("9999")                    # slot freed
    assert q2 is not None
    events.unsubscribe(q2, "9999")


# --- Punches outside the run ---------------------------------------------------------

def test_leftover_punches_from_before_start_do_not_count():
    course = {"type": "linear", "controls": [31, 32, 33]}
    card = {"name": "Uncleared", "class": "M", "start": _dt(10, 0), "finish": _dt(10, 30),
            # 31 and 32 are from an earlier run; today they only punched 33.
            "punches": [(31, _dt(9, 10)), (32, _dt(9, 20)), (33, _dt(10, 20))]}
    res = results.build_result(card, course)
    assert res["status"] == "mp" and res["missed_control"] == 31


# --- Unmatched card reads ----------------------------------------------------------

def _unknown_card(number):
    return {"card_number": number, "start": _dt(11, 0), "finish": _dt(11, 40),
            "punches": [(31, _dt(11, 10))]}


def test_unmatched_read_is_kept_and_assignable():
    out = si_reader.process_card(_unknown_card(9700001))
    assert out["ok"] is False and out["read_id"]
    assert any(u["card_number"] == 9700001 for u in store.unmatched_reads())
    cid = store.class_options()[0]["id"]
    comp = store.create_competitor({"name": "Borrowed Card", "class_id": cid})
    c = appmod.app.test_client()
    r = c.post(f"/api/card-reads/{out['read_id']}/assign", json={"competitor_id": comp["id"]})
    assert r.status_code == 200
    got = store.get_competitor(comp["id"])
    assert got["card_number"] == 9700001 and got["finish"] == _dt(11, 40)
    assert all(u["id"] != out["read_id"] for u in store.unmatched_reads())
    assert c.post(f"/api/card-reads/{out['read_id']}/assign",
                  json={"competitor_id": comp["id"]}).status_code == 400


def test_assigning_a_read_never_silently_replaces_a_run():
    out = si_reader.process_card(_unknown_card(9700011))
    cid = store.class_options()[0]["id"]
    comp = store.create_competitor({"name": "Already Home", "class_id": cid, "read": True,
                                    "start": "10:00:00", "finish": "10:30:00"})
    c = appmod.app.test_client()
    url = f"/api/card-reads/{out['read_id']}/assign"
    r = c.post(url, json={"competitor_id": comp["id"]})
    assert r.status_code == 400 and "already has a run" in r.get_json()["error"]
    assert store.get_competitor(comp["id"])["finish"] == _dt(10, 30)
    assert c.post(url, json={"competitor_id": comp["id"], "replace": True}).status_code == 200
    assert store.get_competitor(comp["id"])["finish"] == _dt(11, 40)


def test_unmatched_read_applies_when_card_is_entered_later():
    si_reader.process_card(_unknown_card(9700002))
    cid = store.class_options()[0]["id"]
    comp = store.create_competitor({"name": "Late Entry", "class_id": cid,
                                    "card_number": 9700002})
    assert comp["finish"] == "11:40:00"
    assert all(u["card_number"] != 9700002 for u in store.unmatched_reads())


def test_unmatched_read_can_be_discarded():
    out = si_reader.process_card(_unknown_card(9700003))
    c = appmod.app.test_client()
    assert c.delete(f"/api/card-reads/{out['read_id']}").status_code == 200
    assert all(u["id"] != out["read_id"] for u in store.unmatched_reads())


# --- Reader hardware path ---------------------------------------------------------

def test_hardware_times_are_pinned_to_event_date():
    other_day = datetime(2031, 1, 2, 10, 5, 0)
    card = si_reader._card_from_si({"card_number": 1, "start": other_day,
                                    "finish": other_day, "punches": [(31, other_day)]}, "x")
    assert card["start"].date() == store.EVENT_DATE
    assert card["punches"][0][1].date() == store.EVENT_DATE
    assert card["start"].time() == other_day.time()


def test_reader_settings_come_from_config(cfg):
    assert si_reader.reader_enabled() is False
    cfg.save({"reader_enabled": True})
    assert si_reader.reader_enabled() is True
    cfg.save({"reader_enabled": False})


# --- Relays ---------------------------------------------------------------------------

def test_relay_legs_start_at_previous_finish():
    course = store.create_course({"name": "Relay loop", "type": "linear",
                                  "controls": [31]})
    cls = store.create_class({"name": "Relay Hardening", "course_id": course["id"],
                              "kind": "relay", "legs": 2})
    team = store.create_team({"name": "Changeover", "class_id": cls["id"],
                              "start": "12:00:00"})
    store.create_competitor({"name": "Leg One", "class_id": cls["id"], "team_id": team["id"],
                             "leg": 1, "finish": "12:30:00",
                             "punches": [{"code": 31, "time": "12:10:00"}]})
    store.create_competitor({"name": "Leg Two", "class_id": cls["id"], "team_id": team["id"],
                             "leg": 2, "finish": "13:05:00",
                             "punches": [{"code": 31, "time": "12:45:00"}]})
    standings = next(t for t in store.team_results() if t["class"]["id"] == cls["id"])
    result = standings["teams"][0]
    assert result["ok"] is True
    assert [leg["seconds"] for leg in result["legs"]] == [1800, 2100]
    assert result["total_seconds"] == 3900


# --- Caching / batching ---------------------------------------------------------

def test_results_cache_follows_every_write():
    import db
    c = appmod.app.test_client()
    first = c.get("/results").get_data(as_text=True)
    assert c.get("/results").get_data(as_text=True) == first   # served from cache
    classes, _ = store.evaluate()
    assert store.evaluate()[0] is classes                        # same revision, same object
    cid = store.class_options()[0]["id"]
    before = db.revision()
    store.create_competitor({"name": "Cache Buster", "class_id": cid,
                             "start": "10:00:00", "finish": "10:40:00"})
    assert db.revision() > before
    assert store.evaluate()[0] is not classes
    assert "Cache Buster" in c.get("/results").get_data(as_text=True)


def test_batch_commits_once(monkeypatch):
    import db
    commits = []
    conn = db._c()

    class Spy:
        def __getattr__(self, name):
            return getattr(conn, name)

        def commit(self):
            commits.append(1)
            conn.commit()
    monkeypatch.setattr(db, "_conn", Spy())
    cid = store.class_options()[0]["id"]
    with store.batch():
        for i in range(5):
            store.create_competitor({"name": f"Batch {i}", "class_id": cid})
    assert len(commits) == 1
    monkeypatch.setattr(db, "_conn", conn)
    # Committed for real: a fresh connection sees them.
    import sqlite3
    other = sqlite3.connect(store.current_event_path())
    n = other.execute("SELECT COUNT(*) FROM competitors WHERE name LIKE 'Batch %'").fetchone()[0]
    other.close()
    assert n == 5


# --- Audit log ------------------------------------------------------------------

def test_audit_log_records_who_changed_what(cfg):
    c = appmod.app.test_client()
    cid = store.class_options()[0]["id"]
    comp = store.create_competitor({"name": "Audit Ann", "class_id": cid,
                                    "card_number": 9900001})
    c.put(f"/api/competitors/{comp['id']}", json={"manual_status": "dsq"})
    si_reader.process_card({"card_number": 9900001, "finish": _dt(12, 0),
                            "punches": [(31, _dt(11, 50))]})
    log = store.audit_log(20)
    edit = next(e for e in log if e["action"] == "competitor edited"
                and "Audit Ann" in e["target"])
    assert "status: (blank) -> dsq" in edit["detail"]
    assert edit["actor"].startswith("console")
    read = next(e for e in log if e["action"] == "card read" and "Audit Ann" in e["target"])
    assert read["actor"] == "SI reader (main)"
    page = c.get("/audit").get_data(as_text=True)
    assert "Audit Ann" in page and "competitor edited" in page


# --- Readout desk -----------------------------------------------------------------

def test_readout_latest_and_hire_card_return():
    c = appmod.app.test_client()
    cid = store.class_options()[0]["id"]
    comp = store.create_competitor({"name": "Hire Hana", "class_id": cid,
                                    "card_number": 9910001, "hired": True,
                                    "start": "10:00:00"})
    si_reader.process_card({"card_number": 9910001, "finish": _dt(10, 40),
                            "punches": [(31, _dt(10, 10))]})
    d = c.get("/api/readout/latest").get_json()
    assert d["runner"]["name"] == "Hire Hana" and d["runner"]["hired"] is True
    assert any(h["id"] == comp["id"] for h in store.economy_summary()["outstanding"])
    c.put(f"/api/competitors/{comp['id']}", json={"card_returned": True})
    assert all(h["id"] != comp["id"] for h in store.economy_summary()["outstanding"])
    assert c.get("/readout").status_code == 200


def test_punches_before_check_are_ignored_and_counted():
    course = {"type": "linear", "controls": [31, 32]}
    card = {"name": "Check", "class": "M", "start": _dt(10, 0), "finish": _dt(10, 40),
            "check": _dt(9, 55),
            "punches": [(31, _dt(9, 30)), (32, _dt(10, 20))]}
    res = results.build_result(card, course)
    assert res["status"] == "mp" and res["ignored_punches"] == 1


def test_reader_loop_ignores_card_removed_events(monkeypatch):
    import sys
    import threading
    import types
    reads = []

    class FakeStation:
        def __init__(self, port):
            self.events = [("in", 9920001), ("out", None)]
            self.sicard = None

        def poll_sicard(self):
            if not self.events:
                stop.set()
                return False
            kind, number = self.events.pop(0)
            self.sicard = number if kind == "in" else None
            return True

        def read_sicard(self):
            if self.sicard is None:
                raise RuntimeError("No card in the device.")
            return {"card_number": self.sicard, "start": None, "finish": _dt(10, 30),
                    "check": None, "clear": None, "punches": []}

        def ack_sicard(self):
            reads.append("ack")

        def disconnect(self):
            pass

    monkeypatch.setitem(sys.modules, "sportident",
                        types.SimpleNamespace(SIReaderReadout=FakeStation))
    monkeypatch.setattr(si_reader, "PUNCH_SYSTEM", "sportident")
    stop = threading.Event()
    si_reader._run("COM9", "test", stop)     # must not raise on the removal event
    assert reads == ["ack"]


def test_serial_port_listing(monkeypatch):
    import types
    from serial.tools import list_ports
    fake = [types.SimpleNamespace(device="COM3", description="USB Serial", manufacturer=None, product=None),
            types.SimpleNamespace(device="COM5", description="SPORTident USB to UART",
                                  manufacturer="Silicon Labs", product=None)]
    monkeypatch.setattr(list_ports, "comports", lambda: fake)
    ports = appmod.app.test_client().get("/api/serial-ports").get_json()
    assert ports[0]["device"] == "COM5" and ports[0]["likely_si"] is True


def test_garbage_bodies_are_refused_not_crashes():
    c = appmod.app.test_client()
    for body in (12345, [1, 2], "text"):
        r = c.post("/api/draw", json=body)
        assert r.status_code == 400
    assert c.post("/api/draw", json={"class_ids": 5}).status_code == 400
    assert c.post("/api/competitors", json={"name": "x" * 1000, "class_id": 1}).status_code == 400
    assert c.post("/api/competitors", json={"name": "Big Card", "class_id": 1,
                                            "card_number": 2 ** 70}).status_code == 400
    assert c.post("/api/classes", json={"name": "Fee", "course_id": 1,
                                        "fee": "Infinity"}).status_code == 400


def test_uploads_in_windows_encodings():
    import io
    c = appmod.app.test_client()
    cls = store.class_options()[0]["name"]
    for raw in (f"name,club,class\nÅsa Müller,Ümeå OK,{cls}\n".encode("cp1252"),
                f"name,club,class\nÅsa Müller2,Ümeå OK,{cls}\n".encode("utf-16")):
        r = c.post("/api/import/startlist", data={"file": (io.BytesIO(raw), "e.csv")},
                   content_type="multipart/form-data")
        assert r.status_code == 200 and r.get_json()["created"] == 1
    names = {x["name"] for x in store._competitors.values()}
    assert {"Åsa Müller", "Åsa Müller2"} <= names
    for comp in [x for x in store._competitors.values() if x["name"].startswith("Åsa Müller")]:
        store.delete_competitor(comp["id"])
