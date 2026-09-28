"""
Things that span events or outlive one: a three-day multi-stage with a chase
start, a season of four events (plus one from another series), online and
pre-event entries, the runner database, backups and restore, publishing,
closing and reopening, restarting the app, and IOF imports.
"""

from __future__ import annotations

import html as htmlmod
import os
import random
import re
import sqlite3
import time
from datetime import timedelta

import oracle
import runs
from harness import YESTERDAY, App, Check, Client, at

NS = "http://www.orienteering.org/datastandard/3.0"
CONTROLS = [31, 32, 33, 34, 35, 36, 37, 38]


def run(check: Check) -> None:
    rng = random.Random(33)
    with App("series") as app:
        c = Client(app, check)
        stages = multi_stage(c, check, rng, app)
        season(c, check, rng)
        entries(c, check, rng, app)
        lifecycle(c, check, rng, app)
        imports(c, check, rng)


def _event_with(c, name, day, people, first="10:00:00"):
    """A new event with course + classes and the given people entered."""
    ev = c.new_event(name, day, first_start=first)
    c.setting(results_unplaced="all")
    course = c.post("/api/courses", {"name": "Main", "type": "linear", "controls": CONTROLS},
                    expect=201)["course"]
    ids = {}
    for cls in sorted({p["class"] for p in people}):
        ids[cls] = c.post("/api/classes", {"name": cls, "course_id": course["id"]},
                          expect=201)["class"]["id"]
    for p in people:
        c.post("/api/competitors", {"name": p["name"], "club": p["club"], "class_id": ids[p["class"]],
                                    "card_number": p["card"]}, expect=201)
    return ev, ids


def _race(c, rng, day, people, kinds=None, first="10:00:00", ids=None, draw=True):
    """Draw and read everyone; returns {name: oracle result}."""
    if draw:
        c.post("/api/draw", {"class_ids": list(ids.values()), "first_start": first,
                             "interval_seconds": 60, "method": "random"}, expect=200)
    comps = {x["name"]: x for x in c.competitors()}
    out = {}
    for p in people:
        comp = comps[" ".join(p["name"].split())]      # names are saved tidied
        kind = (kinds or {}).get(p["name"], "clean")
        start = at(day, comp["start"])
        plan = runs.linear_run(rng, CONTROLS, start, kind, pace=(80, 200))
        if plan["read"]:
            c.read(p["card"], None, plan["finish"], plan["punches"])
        out[p["name"]] = oracle.result(dict(plan, start=start),
                                       {"type": "linear", "controls": CONTROLS})
    return out


def _events(c) -> dict:
    page = c.page("/start")
    return {htmlmod.unescape(m.group(2)): m.group(1) for m in re.finditer(
        r'data-path="([^"]+\.ctrl)"[^>]*data-name="([^"]*)"', page)} or {
        os.path.basename(p): p for p in re.findall(r'data-path="([^"]+\.ctrl)"', page)}


# ---------------------------------------------------------------------------
# Multi-stage
# ---------------------------------------------------------------------------

def multi_stage(c: Client, check: Check, rng, app) -> list[str]:
    check.part("multi-stage")
    people = [{"name": n, "club": rng.choice(runs.CLUBS), "class": "M21" if i % 2 else "W21",
               "card": 7700001 + i} for i, n in enumerate(runs.names(rng, 30))]
    days = [YESTERDAY - timedelta(days=2), YESTERDAY - timedelta(days=1)]
    per_stage = []
    # Stage 2: one runner on a hire card, one who mispunches.
    hire = people[3]
    mp = people[4]
    for s, day in enumerate(days, start=1):
        roster = [dict(p, card=7790001 if (s == 2 and p is hire) else p["card"]) for p in people]
        _, ids = _event_with(c, f"Champs Stage {s}", day, roster)
        kinds = {mp["name"]: "skip"} if s == 2 else {}
        per_stage.append(_race(c, rng, day, roster, kinds, ids=ids))
    events = c.page("/stages")
    files = re.findall(r'value="([^"]+\.ctrl)"', events)
    stage_files = [f for f in files if "stage" in f]
    check.equal(len(stage_files), 2, "both stage files listed")
    q = "&".join(f"stage={f}" for f in stage_files)
    page = htmlmod.unescape(c.page(f"/stages?{q}"))
    # Expected combined: complete = OK in both.
    totals = {}
    for p in people:
        times = [st[p["name"]]["seconds"] if st[p["name"]]["status"] == "ok" else None
                 for st in per_stage]
        totals[p["name"]] = sum(times) if None not in times else None
    check(totals[hire["name"]] is not None, "(plan) the hire-card runner finished both")
    check(totals[mp["name"]] is None, "(plan) the mispuncher is incomplete")
    for cls in ("M21", "W21"):
        members = [p["name"] for p in people if p["class"] == cls]
        ranked = sorted((n for n in members if totals[n] is not None), key=lambda n: totals[n])
        for pos, name in enumerate(ranked[:5], start=1):
            h, rest = divmod(totals[name], 3600)
            m, s = divmod(rest, 60)
            shown = f"{h:02d}:{m:02d}:{s:02d}"
            row = re.search(re.escape(name) + r".{0,2000}?" + re.escape(shown), page, re.S)
            check(row is not None, f"combined {cls}: {name} {shown}")
    hire_row = re.search(re.escape(hire["name"]) + r"(.{0,1500}?)</tr>", page, re.S)
    check(hire_row and hire_row.group(1).count(":") >= 6,
          "a runner who changed SI card between stages still has both stages", hire["name"])

    # Stage 3: chase start from stages 1 + 2.
    check.part("chase start")
    day3 = YESTERDAY
    _, ids = _event_with(c, "Champs Stage 3", day3, people, first="09:00:00")
    out = c.post("/api/stages/chase", {"stages": stage_files, "first_start": "09:00:00"},
                 expect=200)
    comps = {x["name"]: x for x in c.competitors()}
    wrong = []
    for cls in ("M21", "W21"):
        members = [p["name"] for p in people if p["class"] == cls and totals[p["name"]] is not None]
        lead = min(totals[n] for n in members)
        for n in members:
            want = (at(day3, "09:00:00") + timedelta(seconds=totals[n] - lead)).strftime("%H:%M:%S")
            if comps[n]["start"] != want:
                wrong.append((n, want, comps[n]["start"]))
    check(not wrong, "chase starts = leader's time + deficit", wrong[:4])
    check(not comps[mp["name"]]["start"], "an incomplete runner gets no chase start")
    return stage_files


# ---------------------------------------------------------------------------
# Season
# ---------------------------------------------------------------------------

def season(c: Client, check: Check, rng) -> None:
    check.part("season")
    pool = [{"name": n, "club": rng.choice(runs.CLUBS), "class": "Open", "card": 7710001 + i}
            for i, n in enumerate(runs.names(rng, 16))]
    table = [25, 20, 16, 13, 11, 10, 9, 8, 7, 6]
    c.setting("computer", season_points=",".join(map(str, table)), season_best_of=3,
              season_min_events=2, season_finish_points=1, prize_places=3,
              prize_one_per_season=True)
    points = {p["name"]: [] for p in pool}
    winners_before = set()
    for e in range(4):
        day = YESTERDAY - timedelta(days=30 - e * 7)
        attend = [p for p in pool if rng.random() < 0.75]
        # The same person typed differently at one event still counts once.
        if e == 2 and attend:
            attend[0] = dict(attend[0], name="  " + attend[0]["name"].upper() + " ")
        _, ids = _event_with(c, f"Series Round {e + 1}", day, attend)
        c.setting(season_name="Winter Series")
        kinds = {p["name"]: "skip" for p in attend[:2]}
        res = _race(c, rng, day, attend, kinds, ids=ids)
        places = oracle.places(res)
        for p in attend:
            key = p["name"].strip().title() if e == 2 and p is attend[0] else p["name"]
            key = next(q["name"] for q in pool if q["name"].lower() == p["name"].strip().lower())
            r = res[p["name"]]
            if r["status"] == "ok":
                pl = places[p["name"]]
                points[key].append(table[pl - 1] if pl <= len(table) else 1)
        if e == 3:
            prizes = htmlmod.unescape(c.page("/prizes"))
            placed = sorted((n for n in res if places[n]), key=lambda n: places[n])
            given = [n for n in placed if n.strip().lower() not in winners_before][:3]
            passed = [n for n in placed[:3] if n.strip().lower() in winners_before]
            for n in given:
                check(n in prizes, f"round 4 prize for {n}")
            for n in passed:
                check("passed over" in prizes.lower() or "already won" in prizes.lower()
                      or n not in prizes.split("Passed")[0], f"{n} passed over (won earlier)")
        placed = sorted((n for n in res if places[n]), key=lambda n: places[n])
        for n in [n for n in placed if n.strip().lower() not in winners_before][:3]:
            winners_before.add(n.strip().lower())
    # Another series' event in the same folder doesn't count.
    other = [dict(p, card=p["card"]) for p in pool[:5]]
    _, ids = _event_with(c, "Summer Other", YESTERDAY - timedelta(days=3), other)
    c.setting(season_name="Summer Series")
    _race(c, rng, YESTERDAY - timedelta(days=3), other, ids=ids)

    page = htmlmod.unescape(c.page("/season?name=Winter Series"))
    header = re.findall(r'<th class="num" title="[^"]*">([^<]+)</th>', page)
    check("Summer Other" not in header and len(header) == 4,
          "the season's four events, and not another series' event", header)
    bad = []
    for name, pts in points.items():
        best = sum(sorted(pts, reverse=True)[:3])
        row = re.search(r"<tr[^>]*>((?:(?!</tr>).)*?cell-name\">" + re.escape(name)
                        + r"</span>.*?)</tr>", page, re.S)
        if not pts:
            continue
        if row is None:
            bad.append((name, "missing", pts))
            continue
        total = re.findall(r'num strong">(\d+)</td>', row.group(1))
        if not total or int(total[-1]) != best:
            bad.append((name, pts, best, total))
        ranked = "pos-none" not in row.group(0)
        if ranked != (len(pts) >= 2):
            bad.append((name, "ranked with", len(pts), "events", row.group(1)[:600]))
    check(not bad, "season totals = best 3, ranked only with 2+ events", bad[:5])


# ---------------------------------------------------------------------------
# Entries and the runner database
# ---------------------------------------------------------------------------

def entries(c: Client, check: Check, rng, app) -> None:
    check.part("online entry")
    day = YESTERDAY + timedelta(days=8)          # a future event people enter
    ev = c.new_event("Entry Test", day, first_start="10:00:00")
    course = c.post("/api/courses", {"name": "Main", "type": "linear", "controls": CONTROLS},
                    expect=201)["course"]
    open_cls = c.post("/api/classes", {"name": "Open", "course_id": course["id"]},
                      expect=201)["class"]["id"]
    capped = c.post("/api/classes", {"name": "Capped", "course_id": course["id"],
                                     "entry_max": 3}, expect=201)["class"]["id"]
    closed = c.post("/api/classes", {"name": "Invitation", "course_id": course["id"],
                                     "online_entry": False}, expect=201)["class"]["id"]
    c.setting(fee_senior=0, fee_junior=0, entry_message="Bring a whistle")

    def order(entries_list, expect=200):
        r = c.call("POST", "/api/online-entry/order", {"entries": entries_list,
                                                       "email": "a@example.org"},
                   public=True, raw=True)
        check(r.status_code == expect, f"online order -> {expect}", (r.status_code, r.text[:150]))
        return r

    page = c.page("/enter", public=True)
    classes_xml = c.call("GET", "/get-classes", public=True, raw=True).text
    check("Invitation" not in classes_xml, "a class closed to online entry isn't offered")
    order([{"name": "Web One", "club": "Solo", "card": "7720001", "classId": open_cls}])
    order([{"name": "Web Two", "club": "Solo", "card": "7720002", "classId": open_cls},
           {"name": "Web Three", "club": "Solo", "card": "7720003", "classId": open_cls}])
    order([{"name": "Dupe", "club": "Solo", "card": "7720001", "classId": open_cls}], 400)
    order([{"name": "", "club": "Solo", "card": "7720009", "classId": open_cls}], 400)
    order([{"name": "Invited", "club": "Solo", "card": "7720010", "classId": closed}], 400)
    for i in range(3):
        order([{"name": f"Cap {i}", "club": "Solo", "card": str(7720020 + i), "classId": capped}])
    order([{"name": "Cap 4", "club": "Solo", "card": "7720030", "classId": capped}], 400)
    order([{"name": "Bad Card", "club": "Solo", "card": "abc", "classId": open_cls}], 400)
    order([], 400)
    comps = {x["name"] for x in c.competitors()}
    check({"Web One", "Web Two", "Web Three", "Cap 0", "Cap 1", "Cap 2"} <= comps,
          "online entries became competitors")
    check("Dupe" not in comps and "Cap 4" not in comps, "refused entries didn't sneak in")
    check(c.get("/check-entered?card=7720001", public=True) not in (None, {}),
          "check-entered answers")
    # Entry closes.
    c.setting(entry_close=(YESTERDAY - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"))
    order([{"name": "Too Late", "club": "Solo", "card": "7720040", "classId": open_cls}], 400)
    c.setting(entry_close="")
    c.setting(online_entry_open=False)
    order([{"name": "Switched Off", "club": "Solo", "card": "7720041", "classId": open_cls}], 400)
    c.setting(online_entry_open=True)
    # Paid entry without PayPal is refused politely.
    c.setting(fee_senior=15)
    r = order([{"name": "Payer", "club": "Solo", "card": "7720042", "classId": open_cls}], 400)
    check("enter on the day" in r.text.lower() or "payment" in r.text.lower(),
          "paid entry without PayPal says why", r.text[:120])
    c.setting(fee_senior=0)
    # Hammering the order endpoint is rate limited, not a crash.
    codes = set()
    for i in range(40):
        r = c.call("POST", "/api/online-entry/order", {"entries": []}, public=True, raw=True)
        codes.add(r.status_code)
    check(codes <= {400, 429}, "order spam gets 400/429, never 500", codes)

    check.part("pre-event entries")
    ok = c.call("POST", "/api/entries", {"name": "Pre One", "club": "Solo", "class_id": open_cls,
                                         "card_number": 7730001}, raw=True)
    check(ok.status_code == 201, "pre-event entry", ok.text[:150])
    dup = c.call("POST", "/api/entries", {"name": "Pre Dup", "club": "Solo",
                                          "class_id": open_cls, "card_number": 7730001}, raw=True)
    check(dup.status_code == 400, "same card twice refused", dup.status_code)
    c.page("/entries")

    check.part("runner database")
    csv_text = "card,name,club\n7740001,Db Person,Solo\n7740002,Db Other,Wildflower OC\n"
    out = c.upload("/api/import/runners", "runners.csv", csv_text)
    check(out.get("imported", 0) >= 2 or out.get("created", 0) >= 2, "runner CSV imported", out)
    look = c.get("/api/runners/lookup?card=7740001")
    check(look.get("name") == "Db Person", "card lookup fills the name", look)
    members = (f'<?xml version="1.0"?><CompetitorList xmlns="{NS}" iofVersion="3.0">'
               "<Competitor><Person><Name><Family>Member</Family><Given>Mo</Given></Name></Person>"
               "<Organisation><Name>Solo</Name></Organisation>"
               '<ControlCard punchingSystem="SI">7740003</ControlCard></Competitor>'
               "</CompetitorList>")
    out = c.upload("/api/import/members", "members.xml", members)
    check(c.get("/api/runners/lookup?card=7740003").get("name") == "Mo Member",
          "member list into the runner database", out)
    found = c.get("/search-competitors?q=Db", public=True)
    check(found not in (None, [], {}), "entry page search finds the runner database")


# ---------------------------------------------------------------------------
# Backups, restore, publishing, closing, restarting
# ---------------------------------------------------------------------------

def lifecycle(c: Client, check: Check, rng, app) -> None:
    check.part("backup + restore")
    people = [{"name": n, "club": "Solo", "class": "Open", "card": 7750001 + i}
              for i, n in enumerate(runs.names(rng, 12))]
    ev, ids = _event_with(c, "Lifecycle", YESTERDAY, people)
    before = _race(c, rng, YESTERDAY, people, ids=ids)
    snap = c.get("/api/backup", raw=True)
    check(snap.status_code == 200 and snap.content[:15] == b"SQLite format 3", "backup download")
    results_before = c.results()
    # A mistake: someone deletes a runner and DSQs another.
    comps = c.competitors()
    c.delete(f"/api/competitors/{comps[0]['id']}", expect=200)
    c.put(f"/api/competitors/{comps[1]['id']}", {"manual_status": "dsq"}, expect=200)
    check(c.results() != results_before, "(the mistake shows)")
    c.upload("/api/restore", "backup.ctrl", snap.content)
    check.equal(c.results(), results_before, "restore brings back exactly the backed-up results")
    r = c.call("POST", "/api/restore", files={"file": ("junk.ctrl", b"not a database")}, raw=True)
    check(r.status_code == 400, "a junk backup is refused", r.status_code)
    check.equal(c.results(), results_before, "a refused restore changes nothing")
    c.post("/api/backups/now", {}, expect=200)

    check.part("publishing")
    pub = os.path.join(app.dir, "published")
    c.setting("computer", publish_dir=pub)
    out = c.post("/api/publish/now", {}, expect=200)
    files = set(os.listdir(pub)) if os.path.isdir(pub) else set()
    check({"results.html", "results.xml"} <= files, "results.html + results.xml published", files)
    with open(os.path.join(pub, "results.html"), encoding="utf-8") as f:
        text = f.read()
    check(people[3]["name"] in htmlmod.unescape(text), "published page has the results")
    c.setting("computer", publish_dir="")

    check.part("close + reopen + restart")
    path = next(p for n, p in _events(c).items() if "lifecycle" in p.lower() or n == "Lifecycle")
    c.post("/api/events/close", {}, expect=200)
    r = c.call("GET", "/api/competitors/1", raw=True)
    check(r.status_code == 409, "API refuses with no event open", r.status_code)
    r = c.s.get(app.admin + "/results", allow_redirects=False)
    check(r.status_code in (301, 302, 303), "pages send you to the start page", r.status_code)
    for bad in ("/etc/passwd", path.replace(".ctrl", ".txt"), "../../x.ctrl"):
        r = c.call("POST", "/api/events/open", {"path": bad}, raw=True)
        check(r.status_code == 400, f"refuses to open {bad!r}", r.status_code)
    c.post("/api/events/open", {"path": path}, expect=200)
    check.equal(c.results(), results_before, "reopened with the same results")
    app.restart()
    c.post("/api/events/open", {"path": path}, expect=200)
    check.equal(c.results(), results_before, "same results after restarting the app")
    # The file itself is a normal SQLite database with integrity intact.
    con = sqlite3.connect(path)
    check.equal(con.execute("PRAGMA integrity_check").fetchone()[0], "ok", "event file integrity")
    con.close()


# ---------------------------------------------------------------------------
# IOF imports: start list, results (MeOS migration), Eventor entry file
# ---------------------------------------------------------------------------

def imports(c: Client, check: Check, rng) -> None:
    check.part("IOF imports")
    day = YESTERDAY
    c.new_event("Imports", day)
    c.setting(results_unplaced="all")
    course = c.post("/api/courses", {"name": "Main", "type": "linear", "controls": CONTROLS},
                    expect=201)["course"]
    for cls in ("M21", "W21"):
        c.post("/api/classes", {"name": cls, "course_id": course["id"]}, expect=201)
    startlist = [f'<?xml version="1.0"?><StartList xmlns="{NS}" iofVersion="3.0">'
                 "<Event><Name>X</Name></Event>"]
    for cls, who in (("M21", ["Sam Start", "Olle Östlund"]), ("W21", ["Wen Li"])):
        startlist.append(f"<ClassStart><Class><Name>{cls}</Name></Class>")
        for i, n in enumerate(who):
            given, family = n.split(" ", 1)
            startlist.append(
                f"<PersonStart><Person><Name><Family>{family}</Family><Given>{given}</Given></Name>"
                f"</Person><Organisation><Name>Solo</Name></Organisation><Start>"
                f"<StartTime>{day}T10:0{i}:00</StartTime><ControlCard>77600{i}{cls[0] == 'M' and 1 or 2}"
                f"</ControlCard></Start></PersonStart>")
        startlist.append("</ClassStart>")
    startlist.append("</StartList>")
    out = c.upload("/api/import/startlist", "start.xml", "".join(startlist))
    check.equal(out.get("created"), 3, "IOF start list imported")
    comps = {x["name"]: x for x in c.competitors()}
    check.equal(comps.get("Olle Östlund", {}).get("start"), "10:01:00", "start time from the XML")
    # A MeOS result list.
    rl = [f'<?xml version="1.0"?><ResultList xmlns="{NS}" iofVersion="3.0">'
          "<Event><Name>X</Name></Event><ClassResult><Class><Name>M21</Name></Class>"]
    rl.append("<PersonResult><Person><Name><Family>Migrant</Family><Given>Mia</Given></Name>"
              "</Person><Organisation><Name>Solo</Name></Organisation><Result>"
              f"<StartTime>{day}T11:00:00</StartTime><FinishTime>{day}T11:30:00</FinishTime>"
              "<Time>1800</Time><Status>OK</Status><ControlCard>7769999</ControlCard>")
    for i, code in enumerate(CONTROLS):
        rl.append(f"<SplitTime><ControlCode>{code}</ControlCode><Time>{(i + 1) * 180}</Time>"
                  "</SplitTime>")
    rl.append("</Result></PersonResult></ClassResult></ResultList>")
    out = c.upload("/api/import/results", "results.xml", "".join(rl))
    row = c.results().get("M21", {}).get("Mia Migrant")
    check(row and row["status"] == "ok" and row["seconds"] == 1800, "imported MeOS result", row)
    # Eventor's IOF 3.0 entry file.
    el = (f'<?xml version="1.0"?><EntryList xmlns="{NS}" iofVersion="3.0">'
          "<Event><Name>X</Name></Event><PersonEntry><Person><Name><Family>Ventor</Family>"
          "<Given>Eve</Given></Name></Person><Organisation><Name>Solo</Name></Organisation>"
          "<ControlCard>7768888</ControlCard><Class><Name>W21</Name></Class></PersonEntry>"
          "</EntryList>")
    out = c.upload("/api/import/eventor", "entries.xml", el)
    check.equal(out.get("created"), 1, "Eventor entry file imported")
    for bad_name, bad in (("broken.xml", "<StartList><oops"), ("v2.xml", "<StartList></StartList>"),
                          ("empty.csv", ""), ("binary.xml", "\x00\x01\x02")):
        r = c.call("POST", "/api/import/startlist", files={"file": (bad_name, bad)}, raw=True)
        body = r.json() if r.status_code in (200, 400) else {}
        check(r.status_code == 400 or (r.status_code == 200 and not body.get("created")),
              f"bad start list {bad_name} imports nothing", (r.status_code, body))
