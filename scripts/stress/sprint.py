"""
A big individual event: 16 classes, ~400 runners, 5 courses plus a forked
class and a butterfly, all three draw methods, late entries, hire cards,
every kind of card read while pages are being hammered, control statuses,
manual statuses, results settings, splits, exports, economy, prizes and
close-out. Every runner's result is checked against the oracle.
"""

from __future__ import annotations

import random
import re
import statistics
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import oracle
import runs
from harness import YESTERDAY, App, Check, Client, at

NS = "{http://www.orienteering.org/datastandard/3.0}"

COURSES = {
    "A": list(range(31, 46)),                       # 15 controls
    "B": [31, 33, 35, 37, 39, 41, 43, 45, 47, 49, 51, 53],
    "C": [60, 61, 62, 63, 64, 65, 66, 67, 68],
    "D": [70, 71, 72, 73, 74, 75],
    "E": [31, 50, 32, 50, 33, 50, 34],             # butterfly: 50 three times
    "F1": [80, 81, 82, 83, 84, 85, 86, 87],
    "F2": [80, 82, 81, 83, 85, 84, 86, 87],
    "F3": [80, 81, 83, 82, 84, 86, 85, 87],
}
CLASSES = {  # class: (course, draw method, fee)
    "M21E": ("A", "club", 30), "W21E": ("B", "club", 30), "M20": ("F1", "random", 25),
    "W20": ("B", "random", 25), "M35": ("B", "club", 25), "W35": ("C", "alpha", 25),
    "M45": ("C", "club", 25), "W45": ("C", "random", 25), "M55": ("D", "alpha", 20),
    "W55": ("D", "club", 20), "M16": ("C", "random", 12), "W16": ("D", "random", 12),
    "M12": ("E", "alpha", 8), "W12": ("E", "club", 8), "Open": ("D", "random", 15),
    "Officials": ("D", "random", 0),
}
MAX_TIME_D = 45       # course D has a max time
SPARE = [90, 91, 92, 99]


def course_xml() -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>'
             '<CourseData xmlns="http://www.orienteering.org/datastandard/3.0" iofVersion="3.0">'
             "<Event><Name>Stress Sprint</Name></Event><RaceCourseData>"]
    rng = random.Random(3)
    for name, codes in COURSES.items():
        parts.append(f"<Course><Name>{name}</Name><Length>{len(codes) * 180}</Length>"
                     '<CourseControl type="Start"><Control>S1</Control></CourseControl>')
        for code in codes:
            parts.append(f'<CourseControl type="Control"><Control>{code}</Control>'
                         f"<LegLength>{rng.randint(80, 300)}</LegLength></CourseControl>")
        parts.append('<CourseControl type="Finish"><Control>F1</Control></CourseControl></Course>')
    for cls, (course, _, _) in CLASSES.items():
        parts.append(f"<ClassCourseAssignment><ClassName>{cls}</ClassName>"
                     f"<CourseName>{course}</CourseName></ClassCourseAssignment>")
    parts.append("</RaceCourseData></CourseData>")
    return "".join(parts)


def run(check: Check) -> None:
    rng = random.Random(11)
    with App("sprint") as app:
        c = Client(app, check)
        _event(c, check, rng, app)


def _event(c: Client, check: Check, rng, app: App) -> None:
    day = YESTERDAY
    check.part("setup")
    ev = c.new_event("Stress Sprint", day, first_start="10:00:00")
    c.setting(results_unplaced="all", slip_footer="stress")
    c.setting("computer", auto_print="ok")
    out = c.upload("/api/import/courses", "sprint.xml", course_xml())
    check(out.get("created") == len(COURSES), "all courses imported", out)
    check(set(out.get("classes_created", [])) == set(CLASSES), "classes from the file", out)
    # Re-importing updates, never duplicates.
    out = c.upload("/api/import/courses", "sprint.xml", course_xml())
    check(not out.get("created") and len(out.get("updated") or []) == len(COURSES),
          "re-import updates in place", out)
    courses = c.courses()
    check.equal(sorted(courses), sorted(COURSES), "course list")
    for name, codes in COURSES.items():
        check.equal(courses[name]["controls"], codes, f"course {name} controls")
    d = dict(courses["D"])
    d["time_limit_minutes"] = MAX_TIME_D
    c.put(f"/api/courses/{d['id']}", d, expect=200)
    cls_ids = c.classes()
    check.equal(sorted(cls_ids), sorted(CLASSES), "class list")
    for name, (course, _, fee) in CLASSES.items():
        body = {"name": name, "course_id": courses[course]["id"], "fee": fee}
        if name == "M20":
            body["fork_courses"] = [courses[f]["id"] for f in ("F1", "F2", "F3")]
        if name == "Open":
            body["results_mode"] = "no_times"
        if name == "Officials":
            body["results_mode"] = "hidden"
        if name == "W12":
            body["entry_max"] = 20
        c.put(f"/api/classes/{cls_ids[name]}", body, expect=200)

    # ---- entries ---------------------------------------------------------------
    check.part("entries")
    people = {}
    all_names = runs.names(rng, 16 * 26)
    card = 7000001
    rows = ["Name,Club,Class,Card"]
    for i, name in enumerate(all_names[:16 * 24]):
        cls = list(CLASSES)[i % 16]
        club = rng.choice(runs.CLUBS)
        hire = i % 37 == 5
        people[name] = {"name": name, "club": club, "class": cls,
                        "card": None if hire else card, "hire": hire}
        rows.append(f'"{name}","{club}",{cls},{"" if hire else card}')
        if not hire:
            card += 1
    rows.append(f'"Dup Card Person",Solo,M21E,7000001')        # a card already taken
    rows.append(f'"No Class Person",Solo,M99,7999999')           # a class that doesn't exist
    out = c.upload("/api/import/startlist", "entries.csv", "\n".join(rows))
    reasons = [s["reason"] for s in out.get("skipped", [])]
    check.equal(out.get("created"), len(people), "entries created")
    check(any("7000001" in r or "already" in r.lower() for r in reasons), "duplicate card refused",
          reasons)
    check(any("M99" in r for r in reasons), "unknown class refused", reasons)
    comps = {x["name"]: x for x in c.competitors()}
    check(all(n in comps for n in people), "every accepted entry is a competitor",
          [n for n in people if n not in comps][:5])

    # ---- draw ------------------------------------------------------------------
    check.part("draw")
    by_method = {}
    for name, (_, method, _) in CLASSES.items():
        by_method.setdefault(method, []).append(cls_ids[name])
    first = {"club": "10:00:00", "random": "10:00:00", "alpha": "10:00:00"}
    for method, ids in by_method.items():
        c.post("/api/draw", {"class_ids": ids, "first_start": first[method],
                             "interval_seconds": 60, "method": method, "vacants": 1},
               expect=200)
    comps = {x["name"]: x for x in c.competitors()}
    vacant = [x for x in comps.values() if x.get("vacant")]
    real = {n: x for n, x in comps.items() if not x.get("vacant")}
    check(len([v for v in c.competitors() if v.get("vacant")]) == len(CLASSES),
          "one vacant per class", len(vacant))
    check(all(x["start"] for x in real.values()), "everyone has a start")
    # Nobody on the same course at the same time (classes on one course interleave).
    slots = {}
    for x in c.competitors():
        course = CLASSES[x["class_name"]][0]
        key = (course, x["start"])
        slots.setdefault(key, []).append(x["name"])
    clash = {k: v for k, v in slots.items() if len(v) > 1}
    # M12 (alphabetical) and W12 (club) share course E but were drawn apart:
    # the app can't interleave them, so it must say so. Everything else is clean.
    # Courses C and E carry classes drawn in different batches (different methods).
    split = {course for course in {v[0] for v in CLASSES.values()}
             if len({v[1] for v in CLASSES.values() if v[0] == course}) > 1}
    check(all(k[0] in split for k in clash), "no clashes except the split draws",
          [k for k in clash if k[0] not in split][:3])
    check({k[0] for k in clash} == split, "the split draws clash (as set up)",
          sorted({k[0] for k in clash}))
    draw_page = c.page("/draw")
    check("data-clashes" in draw_page and "M12 and W12" in draw_page,
          "draw page warns about the clash")
    check("M12 and W12" in c.page("/setup"), "race-day checklist warns about the clash")
    for course in sorted(split):
        together = [cls_ids[n] for n, v in CLASSES.items() if v[0] == course]
        c.post("/api/draw", {"class_ids": together, "first_start": "10:00:00",
                             "interval_seconds": 60, "method": "club", "vacants": 1},
               expect=200)
    check("data-clashes" not in c.page("/draw"), "drawing them together clears the clash")
    comps = {x["name"]: x for x in c.competitors()}
    real = {n: x for n, x in comps.items() if not x.get("vacant")}
    # Alphabetical classes really are alphabetical.
    for name, (_, method, _) in CLASSES.items():
        members = sorted((x for x in real.values() if x["class_name"] == name),
                         key=lambda x: x["start"])
        if method == "alpha" and CLASSES[name][0] not in split:
            check.equal([x["name"] for x in members],
                        sorted((x["name"] for x in members), key=str.lower),
                        f"{name} drawn alphabetically")
        if method == "club":
            clubs = [x["club"] for x in members]
            biggest = max(clubs.count(k) for k in set(clubs)) if clubs else 0
            adjacent = sum(1 for a, b in zip(clubs, clubs[1:]) if a == b)
            if biggest * 2 <= len(clubs) + 1:
                check(adjacent == 0, f"{name} clubmates never start together", clubs)

    # Late entries after the draw: drawn at the back, nobody else moves.
    before = {n: x["start"] for n, x in real.items()}
    late = []
    for i in range(4):
        name = f"Late Entry {i}"
        r = c.post("/api/competitors", {"name": name, "club": "Solo",
                                        "class_id": cls_ids["M21E"], "card_number": 7900000 + i},
                   expect=201)
        late.append(name)
        people[name] = {"name": name, "club": "Solo", "class": "M21E",
                        "card": 7900000 + i, "hire": False}
    c.post("/api/draw", {"class_ids": [cls_ids["M21E"]], "first_start": "10:00:00",
                         "interval_seconds": 60, "method": "club", "keep_existing": True},
           expect=200)
    comps = {x["name"]: x for x in c.competitors()}
    moved = [n for n, st in before.items() if comps[n]["start"] != st]
    check(not moved, "late-entry draw leaves drawn runners alone", moved[:5])
    m21e_starts = sorted(x["start"] for x in comps.values()
                         if x["class_name"] == "M21E" and x["name"] not in late)
    check(all(comps[n]["start"] > m21e_starts[-1] for n in late), "late entries start last",
          [comps[n]["start"] for n in late])
    out = c.post("/api/bibs/assign", {"start": 101}, expect=200)
    comps = {x["name"]: x for x in c.competitors()}
    bibs = [x["bib"] for x in comps.values() if not x.get("vacant")]
    check(len(set(bibs)) == len(bibs) and None not in bibs, "unique bibs for everyone")
    c.post(f"/api/classes/{cls_ids['M20']}/forks", {}, expect=200)
    comps = {x["name"]: x for x in c.competitors()}
    m20 = sorted((x for x in comps.values() if x["class_name"] == "M20" and not x["vacant"]),
                 key=lambda x: x["start"])
    fork_ids = [courses[f]["id"] for f in ("F1", "F2", "F3")]
    check.equal([x["course_id"] for x in m20], [fork_ids[i % 3] for i in range(len(m20))],
                "forks handed out in start order")
    for pdf in ("/export/startlist.pdf", "/export/bibs.pdf"):
        r = c.get(pdf, raw=True)
        check(r.status_code == 200 and r.content[:4] == b"%PDF", f"{pdf} is a PDF")
    starters = c.get("/api/starters")["starters"]
    check(len(starters) >= len(people), "start clock lists everyone", len(starters))

    # ---- the race ----------------------------------------------------------------
    check.part("race")
    course_of = {n: next(k for k, v in courses.items() if v["id"] == x["course_id"])
                 if x.get("course_id") else CLASSES[x["class_name"]][0]
                 for n, x in comps.items() if not x.get("vacant")}
    plans = {}
    for name, p in people.items():
        comp = comps[name]
        kind = runs.pick(rng, runs.LINEAR_MIX)
        course = course_of[name]
        if kind == "slow" and course != "D":
            kind = "clean"
        start = at(day, comp["start"])
        plan = runs.linear_run(rng, COURSES[course], start, kind, spare_codes=SPARE,
                               slow_minutes=MAX_TIME_D)
        plan["course"] = course
        plans[name] = plan
    # Hire cards: the runner gets a card at the start (editor), then reads out.
    hire_card = 7800001
    for name, p in people.items():
        if p["hire"]:
            c.put(f"/api/competitors/{comps[name]['id']}",
                  {"card_number": hire_card, "hired": True}, expect=200)
            p["card"] = hire_card
            hire_card += 1

    # Radio punches at the 5th control for the elites while they're out.
    for name, plan in plans.items():
        if people[name]["class"] in ("M21E", "W21E") and len(plan["punches"]) > 5 \
                and plan["read"]:
            code, t = plan["punches"][4]
            c.post("/api/radio/punch", {"card_number": people[name]["card"], "code": code,
                                        "time": t.strftime("%H:%M:%S")}, expect=200)
    c.page("/speaker")

    # Read everyone (in parallel) while pages are being hammered.
    stop = threading.Event()
    page_times, page_errors = [], []

    def hammer(path, public):
        s = __import__("requests").Session()
        base = app.public if public else app.admin
        while not stop.is_set():
            t0 = time.time()
            try:
                r = s.get(base + path, timeout=20)
                if r.status_code != 200:
                    page_errors.append((path, r.status_code))
            except Exception as err:  # noqa: BLE001
                page_errors.append((path, str(err)))
            page_times.append(time.time() - t0)

    hammers = [threading.Thread(target=hammer, args=a, daemon=True) for a in
               [("/results", False), ("/splits", False), ("/live", False), ("/", False),
                (f"/public/{ev['slug']}", True), (f"/public/{ev['slug']}/results.json", True),
                ("/get-results", True), ("/download", False)]]
    for h in hammers:
        h.start()
    read_times = []

    def do_read(name):
        plan = plans[name]
        if not plan["read"]:
            return
        t0 = time.time()
        c.read(people[name]["card"], plan["card_start"], plan["finish"], plan["punches"],
               check_time=plan["check"])
        read_times.append(time.time() - t0)

    order = list(plans)
    rng.shuffle(order)
    t0 = time.time()
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(do_read, order))
    total = time.time() - t0
    stop.set()
    for h in hammers:
        h.join(timeout=30)
    read_times.sort()
    p95 = read_times[int(len(read_times) * 0.95)] if read_times else 0
    print(f"     {len(read_times)} reads in {total:.1f}s, p50 {statistics.median(read_times):.3f}s,"
          f" p95 {p95:.3f}s; {len(page_times)} page loads alongside,"
          f" p95 {sorted(page_times)[int(len(page_times) * .95)]:.3f}s")
    check(not page_errors, "pages stayed up while cards were read", page_errors[:5])
    check(p95 < 2.0, "reads stay quick under load", f"p95 {p95:.2f}s")

    # Re-read three cards: nothing changes.
    for name in order[:3]:
        plan = plans[name]
        if plan["read"]:
            c.read(people[name]["card"], plan["card_start"], plan["finish"], plan["punches"],
                   check_time=plan["check"])

    # ---- check every result against the oracle --------------------------------------
    check.part("results")
    statuses, alternates = {}, {}

    def expected():
        out = {}
        for name, plan in plans.items():
            cls = people[name]["class"]
            course = {"type": "linear", "controls": COURSES[plan["course"]],
                      "time_limit_minutes": MAX_TIME_D if plan["course"] == "D" else None}
            runrec = dict(plan, start=plan["start"], manual=manual.get(name))
            out.setdefault(cls, {})[name] = oracle.result(runrec, course, statuses=statuses,
                                                          alternates=alternates)
        return out

    manual = {}

    def compare(label):
        want = expected()
        got = c.results()
        bad = []
        for cls, rows in want.items():
            if cls == "Officials":
                continue                              # hidden: not public
            shown = got.get(cls, {})
            names_only = cls == "Open"
            places = oracle.places(rows)
            for name, r in rows.items():
                row = shown.get(name)
                if r["status"] == "pending":
                    if row is not None:
                        bad.append((name, "pending runner listed", row["status"]))
                    continue
                if row is None:
                    bad.append((name, "missing", r["status"]))
                    continue
                if names_only:
                    if row["seconds"] is not None or row["place"] is not None:
                        bad.append((name, "names-only class shows a time"))
                    continue
                if row["status"] != r["status"]:
                    bad.append((name, cls, plans[name]["kind"], "status", r["status"],
                                row["status"]))
                elif r["status"] not in ("dns", "dnf") and row["seconds"] != r["seconds"]:
                    bad.append((name, cls, plans[name]["kind"], "seconds", r["seconds"],
                                row["seconds"]))
                elif row["place"] != places[name]:
                    bad.append((name, cls, "place", places[name], row["place"]))
        check(not bad, f"every result matches the rules ({label})", bad[:8])
        return want

    want = compare("as read")
    kinds = {}
    for name, plan in plans.items():
        kinds.setdefault(plan["kind"], set()).add(want[people[name]["class"]][name]["status"])
    print("     kinds -> statuses:", {k: sorted(v) for k, v in sorted(kinds.items())})
    hidden = c.page("/results")
    officials = [n for n, p in people.items() if p["class"] == "Officials"]
    check(all(n.replace("'", "&#39;") in hidden or n in hidden for n in officials[:3]),
          "hidden class on the operator's results")
    public_html = c.page(f"/public/{ev['slug']}", public=True)
    leaked = [n for n in officials if f">{n}<" in public_html]
    check(not leaked, "hidden class not public", leaked[:2])

    # Slips: auto-print was "OK runs only" while cards came in.
    time.sleep(3)
    import glob
    import os
    slips = len(glob.glob(os.path.join(app.printed, "*.png")))
    ok_reads = sum(1 for name, plan in plans.items() if plan["read"]
                   and want[people[name]["class"]][name]["status"] == "ok")
    rereads_ok = sum(1 for name in order[:3] if plans[name]["read"]
                     and want[people[name]["class"]][name]["status"] == "ok")
    check.equal(slips, ok_reads + rereads_ok, "a slip for every OK read and nothing else")

    # ---- control statuses ---------------------------------------------------------------
    check.part("control statuses")
    # 62 is broken, 72 optional, 64 a road crossing, 33's unit was swapped for 133.
    for code, body in ((62, {"status": "bad"}), (72, {"status": "optional"}),
                       (64, {"status": "no_timing"}), (33, {"status": "ok", "alternates": [133]})):
        c.post(f"/api/controls/{code}", body, expect=200)
    statuses.update({62: "bad", 72: "optional", 64: "no_timing"})
    alternates.update({133: 33})
    # A few A-course runners who punched the replacement unit (re-read).
    swapped = [n for n, pl in plans.items() if pl["course"] == "A" and pl["kind"] == "clean"][:3]
    for name in swapped:
        pl = plans[name]
        pl["punches"] = [(133 if code == 33 else code, t) for code, t in pl["punches"]]
        c.read(people[name]["card"], pl["card_start"], pl["finish"], pl["punches"],
               check_time=pl["check"])
    compare("with control statuses")
    report = c.page("/controls")
    check("133" in report or "33" in report, "controls page lists the replacement")
    c.post("/api/controls/62", {"status": "ok"}, expect=200)
    del statuses[62]
    compare("broken control fixed")

    # ---- manual statuses ----------------------------------------------------------------
    check.part("manual statuses")
    targets = {}
    for want_status in ("dsq", "nc", "ok", "dns", "dnf", "mp"):
        for name, plan in plans.items():
            cls = people[name]["class"]
            if cls in ("Open", "Officials") or name in manual or name in targets:
                continue
            auto = want[cls][name]["status"]
            if want_status == "ok" and auto != "mp":
                continue
            if want_status != "ok" and auto != "ok":
                continue
            targets[name] = want_status
            break
    for name, st in targets.items():
        c.put(f"/api/competitors/{comps[name]['id']}", {"manual_status": st}, expect=200)
        manual[name] = st
    compare("with manual statuses")
    for name in list(targets)[:2]:
        c.put(f"/api/competitors/{comps[name]['id']}", {"manual_status": ""}, expect=200)
        del manual[name]
    compare("manual status cleared")

    # ---- results settings -----------------------------------------------------------------
    check.part("results settings")
    c.setting(time_format="hms")
    r = c.results()
    sample = next(row for rows in r.values() for row in rows.values() if row["time"])
    check(re.fullmatch(r"\d\d:\d\d:\d\d", sample["time"]), "hh:mm:ss time format", sample["time"])
    c.setting(time_format="minutes")
    sample = next(row for rows in c.results().values() for row in rows.values() if row["time"])
    check(re.fullmatch(r"\d+:\d\d", sample["time"]), "minutes time format", sample["time"])
    c.setting(time_format="auto", class_order="W12, M12, Open")
    order_seen = [x["name"] for x in c.get(f"/public/{ev['slug']}/results.json",
                                           public=True)["classes"]]
    check.equal(order_seen[:3], ["W12", "M12", "Open"], "custom class order")
    c.setting(results_unplaced="placed")
    check(all(row["place"] for cls, rows in c.results().items() if cls != "Open"
              for row in rows.values()), "placed-only shows placed runners")
    c.setting(results_unplaced="no_dns")
    check(not any(row["status"] == "dns" for rows in c.results().values()
                  for row in rows.values()), "no DNS listed")
    c.setting(results_unplaced="all", results_group_by="course")
    by_course = c.results()
    # Course C is run by W35, M45, W45, M16: one ranking.
    want_now = expected()
    pooled = {}
    for cls, rows in want_now.items():
        if CLASSES[cls][0] == "C" and cls not in ("Open", "Officials"):
            for name, res in rows.items():
                if plans[name]["course"] == "C" and res["status"] != "pending":
                    pooled[name] = res
    got_c = by_course.get("C", {})
    places = oracle.places(pooled)
    wrong = [(n, places[n], got_c.get(n, {}).get("place")) for n in pooled
             if got_c.get(n, {}).get("place") != places[n]]
    check(not wrong, "results by course rank classes together", wrong[:5])
    # The forked class counts each runner on the fork they ran.
    for fork in ("F1", "F2", "F3"):
        ran = {n for n, pl in plans.items() if pl["course"] == fork
               and want_now["M20"][n]["status"] != "pending"}
        check(set(by_course.get(fork, {})) == ran, f"fork {fork} lists its own runners")
    c.setting(results_group_by="class")

    # ---- splits: the IOF export against the oracle ------------------------------------------
    check.part("splits")
    xml = c.get("/export/results.xml", raw=True).text
    root = ET.fromstring(xml)
    bad = []
    for pr in root.iter(NS + "PersonResult"):
        given = pr.findtext(f"{NS}Person/{NS}Name/{NS}Given") or ""
        family = pr.findtext(f"{NS}Person/{NS}Name/{NS}Family") or ""
        name = f"{given} {family}".strip()
        if name not in plans:
            continue
        plan = plans[name]
        res = want_now[people[name]["class"]][name]
        if res["status"] not in ("ok", "mp") or plan["kind"] in ("old_punches",):
            continue
        rows = [(int(st.findtext(NS + "ControlCode")), st.findtext(NS + "Time"),
                 st.get("status")) for st in pr.iter(NS + "SplitTime")]
        timed = [x for x in COURSES[plan["course"]] if statuses.get(x) != "bad"]
        exp = oracle.splits(plan["start"], [(alternates.get(k, k), t) for k, t in plan["punches"]],
                            plan["finish"], timed)
        got = [None if s == "Missing" else int(float(t)) for _, t, s in rows]
        if [k for k, _, _ in rows] != timed or got != exp:
            bad.append((name, plan["kind"], exp, got))
    check(not bad, "IOF split times match the punches", bad[:3])
    c.page("/splits")
    for name in list(plans)[:5]:
        c.page(f"/slip/{comps[name]['id']}")
        r = c.get(f"/slip/{comps[name]['id']}.pdf", raw=True)
        check(r.content[:4] == b"%PDF", "slip PDF")

    # ---- economy ---------------------------------------------------------------------------
    check.part("economy")
    c.setting(hire_card_fee=5)
    money = c.page("/economy")
    due_total = sum(CLASSES[p["class"]][2] for p in people.values()) + \
        5 * sum(1 for p in people.values() if p["hire"])
    check(f"{due_total:.2f}" in money.replace(",", ""), "total due = class fees + hire cards",
          due_total)
    paid = 0
    for name in list(people)[:40]:
        comp = comps[name]
        c.post(f"/api/competitors/{comp['id']}/payment", {"method": "Cash"}, expect=200)
        paid += CLASSES[people[name]["class"]][2] + (5 if people[name]["hire"] else 0)
    money = c.page("/economy")
    check(f"{paid:.2f}" in money.replace(",", ""), "total paid adds up", paid)
    r = c.get("/export/invoices.pdf", raw=True)
    check(r.content[:4] == b"%PDF", "club invoices PDF")
    for name in [n for n, p in people.items() if p["hire"]][:3]:
        c.put(f"/api/competitors/{comps[name]['id']}", {"card_returned": True}, expect=200)
    c.page("/economy")

    # ---- prizes --------------------------------------------------------------------------------
    check.part("prizes")
    c.setting(prize_places=3, prize_share=50)
    prizes = c.page("/prizes")
    check("M21E" in prizes, "prize list")
    r = c.get("/export/prizes.pdf", raw=True)
    check(r.content[:4] == b"%PDF", "prize PDF")

    # ---- the rest of the operator pages ----------------------------------------------------------
    check.part("pages")
    for path in ("/", "/competitors", "/classes", "/courses", "/draw", "/download", "/results",
                 "/splits", "/live", "/live?classes=M21E,W21E", "/speaker", "/teams",
                 "/prizes", "/season", "/stages", "/clubs", "/economy", "/audit",
                 "/controls", "/entries", "/eventor", "/setup", "/tools", "/config",
                 "/readout", "/starter"):
        c.page(path)
    for q in ("Åsa", "7000010", "101", "o'brien", "M21E", "Bibbulmun"):
        out = c.get(f"/api/search?q={q}")
        check(any(out.get(k) for k in ("runners", "classes", "clubs")), f"search finds {q!r}")
    audit = c.page("/audit")
    check(audit.count("<tr") > 50, "change log is filling up")

    # ---- close-out ----------------------------------------------------------------------------
    check.part("close-out")
    still_out = [n for n, pl in plans.items() if not pl["read"]]
    import html as htmlmod
    setup = htmlmod.unescape(c.page("/setup"))
    missing = [n for n in still_out if n not in setup]
    check(not missing, "close-out lists who's still out", missing[:3])
    out = c.post("/api/close-out/remaining", {"status": "dns"}, expect=200)
    check.equal(out.get("count"), len(still_out), "the rest marked DNS")
    for name in still_out:
        manual[name] = "dns"
        plans[name]["read"] = True
    compare("after close-out")
    c.post("/api/backups/now", {}, expect=200)
