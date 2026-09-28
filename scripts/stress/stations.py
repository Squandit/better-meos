"""
Two computers: a main one holding the event and a secondary download station
that forwards every card it reads. Also radio controls pushing punches with
the station token, a station with the wrong token, and the main computer
going down mid-event.
"""

from __future__ import annotations

import glob
import os
import random
import time
from datetime import timedelta

import oracle
import runs
from harness import YESTERDAY, App, Check, Client, at

CONTROLS = [31, 32, 33, 34, 35, 36]
TOKEN = "stress-station-token"


def run(check: Check) -> None:
    rng = random.Random(44)
    day = YESTERDAY
    with App("primary") as main:
        m = Client(main, check)
        check.part("main computer")
        ev = m.new_event("Two Desks", day)
        m.setting(results_unplaced="all")
        m.setting("computer", station_token=TOKEN, auto_print="all")
        course = m.post("/api/courses", {"name": "C", "type": "linear", "controls": CONTROLS},
                        expect=201)["course"]
        cls = m.post("/api/classes", {"name": "Open", "course_id": course["id"]},
                     expect=201)["class"]["id"]
        names = runs.names(rng, 30)
        for i, n in enumerate(names):
            m.post("/api/competitors", {"name": n, "club": "Solo", "class_id": cls,
                                        "card_number": 7800001 + i}, expect=201)
        m.post("/api/draw", {"class_ids": [cls], "first_start": "10:00:00",
                             "interval_seconds": 60, "method": "random"}, expect=200)
        comps = {x["name"]: x for x in m.competitors()}

        with App("secondary", env={"BMEOS_PRIMARY": main.admin}) as second:
            s = Client(second, check)
            s.setting("computer", station_token=TOKEN, auto_print="all")
            check.part("reads forwarded from the secondary")
            want = {}
            for i, n in enumerate(names[:20]):
                start = at(day, comps[n]["start"])
                kind = "old_punches" if i % 5 == 0 else ("skip" if i % 7 == 0 else "clean")
                plan = runs.linear_run(rng, CONTROLS, start, kind)
                out = s.read(7800001 + i, None, plan["finish"], plan["punches"],
                             check_time=plan["check"], station="desk2")
                want[n] = oracle.result(dict(plan, start=start),
                                        {"type": "linear", "controls": CONTROLS})
            got = m.results().get("Open", {})
            bad = [(n, w["status"], got.get(n, {}).get("status")) for n, w in want.items()
                   if got.get(n, {}).get("status") != w["status"]
                   or (w["status"] == "ok" and got[n]["seconds"] != w["seconds"])]
            check(not bad, "forwarded reads give the same results (check times included)",
                  bad[:4])
            time.sleep(2)
            main_slips = glob.glob(os.path.join(main.printed, "*.png"))
            desk_slips = glob.glob(os.path.join(second.printed, "*.png"))
            check.equal(len(desk_slips), 20, "slips print at the desk the card was read at")
            check.equal(len(main_slips), 0, "the main computer prints none of them")

            check.part("unknown card at the second desk")
            out = s.read(7899999, None, plan_finish := at(day, "11:30:00"),
                         [(31, at(day, "11:10:00"))], expect_ok=False)
            check(not out.get("push_failed"), "an unknown card isn't a failed send", out)
            dl = m.page("/download")
            check("7899999" in dl, "the main computer kept the unknown card's read")

            check.part("wrong token")
            # Other computers need the admin password set on the main one; with
            # it set, only the right token gets a read in without logging in.
            m.setting("computer", admin_password="stress-admin-pw")
            m.s.post(main.admin + "/unlock", data={"password": "stress-admin-pw"})
            s.setting("computer", station_token="wrong")
            n = names[20]
            start = at(day, comps[n]["start"])
            plan = runs.linear_run(rng, CONTROLS, start, "clean")
            r = s.call("POST", "/api/reader/simulate",
                       {"card_number": 7800021, "finish": plan["finish"].strftime("%H:%M:%S"),
                        "punches": [{"code": c, "time": t.strftime("%H:%M:%S")}
                                    for c, t in plan["punches"]]}, raw=True, expect=502)
            check(r.status_code >= 400 and "station token" in r.text,
                  "a station with the wrong token is refused, and told why", r.text[:160])
            check(n not in m.results().get("Open", {}), "...and nothing reached the results")
            s.setting("computer", station_token=TOKEN)

            check.part("main computer down")
            main.stop()
            r = s.call("POST", "/api/reader/simulate",
                       {"card_number": 7800021, "finish": plan["finish"].strftime("%H:%M:%S"),
                        "punches": [{"code": c, "time": t.strftime("%H:%M:%S")}
                                    for c, t in plan["punches"]]}, raw=True, expect=502)
            check(r.status_code == 502 and r.json().get("push_failed"),
                  "a read with the main computer down says so (the card isn't lost)",
                  (r.status_code, r.text[:120]))
            c2 = s.page("/download")
            main.start()
            m = Client(main, check)
            path = [p for p in os.listdir(os.path.join(main.dir, "events"))][0]
            m._slug = ev["slug"]
            m.s.post(main.admin + "/unlock", data={"password": "stress-admin-pw"})
            m.post("/api/events/open", {"path": os.path.join(main.dir, "events", path)},
                   expect=200)
            s.read(7800021, None, plan["finish"], plan["punches"])
            check(n in m.results().get("Open", {}), "reading again once it's back works")
            # The desk re-checks the link every few seconds.
            for _ in range(20):
                status = s.get("/api/station/status")
                if status.get("reachable"):
                    break
                time.sleep(0.5)
            check(status.get("secondary") and status.get("reachable"), "station status: connected",
                  status)
            s.setting("computer", station_token="wrong-again")
            time.sleep(5.5)
            status = s.get("/api/station/status")
            check(not status.get("reachable") and "token" in status.get("error", ""),
                  "a desk with the wrong token is told so", status.get("error"))
            s.setting("computer", station_token=TOKEN)
            time.sleep(5.5)
            start_page = s.page("/start")
            check("Second download desk" in start_page and "7800021" in start_page,
                  "the station's start page shows its reads")

        check.part("radio controls")
        n = names[25]
        body = {"card_number": 7800026, "code": 33,
                "time": (at(day, comps[n]["start"]) + timedelta(minutes=5)).strftime("%H:%M:%S")}
        r = m.s.post(main.admin + "/api/radio/punch", json=body, headers={"X-Station-Token": TOKEN})
        check(r.status_code == 200, "radio punch with the token", r.text[:100])
        r = m.s.post(main.public + "/api/radio/punch", json=body)
        check(r.status_code == 404, "radio punches can't come in on the public port", r.status_code)
        r = m.s.post(main.admin + "/api/radio/punch", json=dict(body, card_number=1))
        check(r.status_code == 400, "a radio punch for an unknown card is refused", r.status_code)
        check(n in m.page("/speaker"), "radio runner on the speaker page")
