"""
Garbage in: every route that changes data gets malformed, oversized, wrongly
typed and hostile input. The app must refuse cleanly (4xx), never fall over
(5xx), and still work normally afterwards.
"""

from __future__ import annotations

import random

from guard import concrete, routes
from harness import YESTERDAY, App, Check, Client

WEIRD_VALUES = [None, "", " ", "abc", -1, 0, 2 ** 70, -2 ** 70, 1e308, float("inf") if False else 1e-9,
                "NaN", "Infinity", "1e999", True, [], {}, [1, 2], {"a": 1}, "x" * 100_000,
                "10:61:99", "25:00:00", "2026-13-45", "../../etc/passwd", "\u0000", "💥" * 50,
                "<script>", "' OR 1=1 --"]
FIELDS = ["name", "club", "class_id", "course_id", "card_number", "start", "finish", "punches",
          "code", "time", "status", "manual_status", "fee", "paid", "legs", "kind", "type",
          "controls", "bib", "team_id", "leg", "first_start", "interval_seconds", "method",
          "vacants", "class_ids", "event_id", "competitor_id", "values", "target", "reset",
          "path", "entries", "email", "stages", "amount", "method", "alternates", "restart",
          "fork_courses", "entry_max", "results_mode", "time_limit_minutes", "mass_start",
          "start_mode", "score_formula", "hired", "card_returned", "layout", "text", "q",
          "date", "orderID", "replace", "course", "start_control", "check"]


def bodies(rng: random.Random, n: int):
    yield None
    yield []
    yield "just a string"
    yield 12345
    yield {}
    for _ in range(n):
        yield {rng.choice(FIELDS): rng.choice(WEIRD_VALUES) for _ in range(rng.randint(1, 6))}


def run(check: Check) -> None:
    rng = random.Random(int(__import__("os").environ.get("BMEOS_STRESS_SEED", "55")))
    table = routes()
    with App("fuzz") as app:
        c = Client(app, check)
        ev = c.new_event("Fuzz", YESTERDAY)
        course = c.post("/api/courses", {"name": "C", "type": "linear", "controls": [31, 32, 33]},
                        expect=201)["course"]
        cls = c.post("/api/classes", {"name": "Open", "course_id": course["id"]},
                     expect=201)["class"]["id"]
        for i in range(5):
            c.post("/api/competitors", {"name": f"Keep {i}", "class_id": cls,
                                        "card_number": 7950001 + i, "start": "10:00:00"},
                   expect=201)
        # Never fuzz the routes that end the event or swap the file out.
        skip = {"/api/events/close", "/api/events/new", "/api/events/sample", "/api/events/open", "/api/restore",
                "/api/sync/import", "/api/remote/start", "/api/remote/stop", "/api/stream",
                "/api/publish/now", "/api/backups/now", "/api/dashboard/reset"}
        errors = []
        calls = 0
        check.part("mutating routes")
        for rule, methods in table:
            if rule in skip or not rule.startswith("/api/"):
                continue
            for method in methods:
                if method == "GET":
                    continue
                for ids in ("1", "999999999", "0"):
                    path = concrete(rule, ev["slug"]).replace("/1", "/" + ids) \
                        if "<int:" in rule else concrete(rule, ev["slug"])
                    for body in bodies(rng, 25):
                        try:
                            if isinstance(body, (dict, list)) or body is None:
                                r = c.s.request(method, app.admin + path, json=body, timeout=30)
                            else:
                                r = c.s.request(method, app.admin + path, data=str(body),
                                                headers={"Content-Type": "application/json"},
                                                timeout=30)
                        except Exception as err:  # noqa: BLE001
                            errors.append((method, path, "no answer", str(err)[:80]))
                            continue
                        calls += 1
                        if r.status_code >= 500:
                            errors.append((method, path, str(body)[:300], r.status_code,
                                           app.last_traceback()[-500:]))
                    if "<int:" not in rule:
                        break
        print(f"     {calls} garbage calls")
        seen, unique = set(), []
        for e in errors:
            if (e[0], e[1]) not in seen:
                seen.add((e[0], e[1]))
                unique.append(e)
        check(not unique, "garbage never gets a server error", unique[:12])

        check.part("uploads")
        for rule in ("/api/import/courses", "/api/import/startlist", "/api/import/results",
                     "/api/import/runners", "/api/import/members", "/api/import/eventor"):
            for name, content in (("a.xml", b"\xff\xfe\x00garbage"), ("b.xml", b"<a>" * 20000),
                                  ("c.csv", b"name,class\n" + b"x," * 50000),
                                  ("d.xml", b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">'
                                            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><x>&b;</x>'),
                                  ("e.txt", b"")):
                r = c.s.post(app.admin + rule, files={"file": (name, content)}, timeout=60)
                if r.status_code >= 500:
                    errors.append((rule, name, r.status_code))
            r = c.s.post(app.admin + rule, data={"nofile": "x"})
            if r.status_code >= 500:
                errors.append((rule, "no file", r.status_code))
        check(not [e for e in errors if e[0].startswith("/api/import")],
              "bad uploads never get a server error",
              [e for e in errors if e[0].startswith("/api/import")][:6])

        check.part("GET with odd queries")
        bad = []
        for rule, methods in table:
            if "GET" not in methods or rule in ("/api/stream", "/logout", "/lock"):
                continue
            path = concrete(rule, ev["slug"])
            for q in ("?classId=abc", "?card=-1&q=%27%20OR%201%3D1", "?name=" + "x" * 5000,
                      "?stage=../../x.ctrl&stage=", "?classes=%00", "?target=nope&q=%3Cb%3E",
                      "?page=%ff", "?next=//evil"):
                r = c.s.get(app.admin + path + q, timeout=30, allow_redirects=False)
                if r.status_code >= 500:
                    bad.append((path + q[:40], r.status_code, app.last_traceback()[-400:]))
        check(not bad, "odd query strings never get a server error", bad[:8])

        check.part("public port")
        bad = []
        for i in range(60):
            body = next(bodies(rng, 1)) if i % 3 else {"entries": [
                {k: rng.choice(WEIRD_VALUES) for k in ("name", "club", "card", "classId", "type")}]}
            r = c.s.post(app.public + "/api/online-entry/order", json=body)
            if r.status_code >= 500:
                bad.append((str(body)[:80], r.status_code, r.text[:80]))
            r = c.s.post(app.public + "/api/online-entry/capture", json={"orderID": rng.choice(
                WEIRD_VALUES)})
            if r.status_code >= 500:
                bad.append(("capture", r.status_code))
        check(not bad, "public entry never gets a server error", bad[:5])

        check.part("still works")
        # The garbage may have deleted or renamed things with id 1: start clean.
        course = c.post("/api/courses", {"name": "After", "type": "linear",
                                         "controls": [31, 32, 33]}, expect=201)["course"]
        cls = c.post("/api/classes", {"name": "After Class", "course_id": course["id"]},
                     expect=201)["class"]["id"]
        c.post("/api/competitors", {"name": "After Runner", "class_id": cls,
                                    "card_number": 7959999, "start": "10:00:00"}, expect=201)
        c.read(7959999, None, "10:20:00", [(31, "10:05:00"), (32, "10:10:00"), (33, "10:15:00")])
        res = c.results()
        row = res.get("After Class", {}).get("After Runner", {})
        check(row.get("status") == "ok" and row.get("seconds") == 1200,
              "a normal read still works after all that", row)
        for path in ("/", "/results", "/competitors", "/download", "/config"):
            c.page(path)
