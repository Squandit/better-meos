"""
Every kind of competition, each in its own event (one app, events switched
between): score-O (clock, mass start and a formula), a punch-start course,
relays (team start with a mass restart and forks, and a mass-start course),
patrols, a night event across midnight, and an event happening right now.
"""

from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import oracle
import runs
from harness import YESTERDAY, App, Check, Client, at

NS = "{http://www.orienteering.org/datastandard/3.0}"


def run(check: Check) -> None:
    with App("formats") as app:
        c = Client(app, check)
        rng = random.Random(21)
        score_event(c, check, rng)
        punch_start_event(c, check, rng)
        relay_event(c, check, rng)
        patrol_event(c, check, rng)
        night_event(c, check, rng)
        live_event(c, check, rng)
        # Switch back to an earlier event: its results are intact.
        check.part("switching events")
        events = c.page("/start")
        paths = re.findall(r'data-path="([^"]+\.ctrl)"', events)
        check(len(paths) >= 6, "all six event files listed", len(paths))
        score_path = next((p for p in paths if "score" in p), None)
        c.post("/api/events/open", {"path": score_path}, expect=200)
        c._slug = "stress-score"
        check("S-Long" in c.results(), "reopened the score event with its results")


def _people(c, cls_id, names, cards, club=None, rng=None):
    ids = {}
    for name, card in zip(names, cards):
        r = c.post("/api/competitors", {"name": name, "club": club or rng.choice(runs.CLUBS),
                                        "class_id": cls_id, "card_number": card}, expect=201)
        ids[name] = r["competitor"]["id"]
    return ids


def _compare(c, check, label, want: dict, score=False):
    got = c.results()
    bad = []
    for cls, rows in want.items():
        places = oracle.places(rows, score=score)
        for name, r in rows.items():
            row = got.get(cls, {}).get(name)
            if r["status"] == "pending":
                if row is not None:
                    bad.append((name, "pending listed"))
                continue
            if row is None:
                bad.append((name, "missing", r))
                continue
            for field, value in (("status", r["status"]), ("seconds", r["seconds"]),
                                 ("points", r["points"]), ("place", places[name])):
                if r["status"] in ("dns", "dnf") and field == "seconds":
                    continue
                if row[field] != value:
                    bad.append((name, cls, field, value, row[field]))
                    break
    check(not bad, f"every result matches ({label})", bad[:6])


# ---------------------------------------------------------------------------
# Score-O
# ---------------------------------------------------------------------------

def score_event(c: Client, check: Check, rng) -> None:
    check.part("score-O")
    day = YESTERDAY
    c.new_event("Stress Score", day, first_start="10:00:00")
    c.setting(results_unplaced="all")
    # Bad courses are refused, never half-saved.
    for body, why in (
            ({"name": "Dup", "type": "score", "controls": [{"code": 31, "points": 10},
                                                           {"code": 31, "points": 20}]},
             "a control listed twice"),
            ({"name": "Neg", "type": "score", "controls": [{"code": 31, "points": -5}]},
             "negative points"),
            ({"name": "Evil", "type": "score", "controls": [{"code": 31, "points": 5}],
              "score_formula": "__import__('os').system('echo hi')"}, "code in a formula"),
            ({"name": "Huge", "type": "score", "controls": [{"code": 31, "points": 5}],
              "score_formula": "9**9**9"}, "a runaway formula"),
            ({"name": "NoCtl", "type": "score", "controls": []}, "no controls"),
            ({"name": "Mass", "type": "score", "controls": [{"code": 31, "points": 5}],
              "start_mode": "mass"}, "mass start without a time"),
    ):
        r = c.call("POST", "/api/courses", body, raw=True)
        check(r.status_code == 400, f"refuses {why}", r.status_code)

    values_long = {code: 10 * (1 + (code % 5)) for code in range(31, 51)}
    values_mass = {code: 20 + (code % 3) * 10 for code in range(51, 66)}
    values_formula = {code: 10 for code in range(70, 82)}
    specs = {
        "S-Long": {"name": "Score60", "limit": 60, "penalty": 2, "values": values_long},
        "S-Mass": {"name": "ScoreMass", "limit": 45, "penalty": 5, "values": values_mass,
                   "mass": "11:00:00"},
        "S-Formula": {"name": "ScoreF", "limit": 30, "penalty": 0, "values": values_formula,
                      "formula": "controls * 10 - over_minutes * 3"},
    }
    cls_ids = {}
    for cls, spec in specs.items():
        body = {"name": spec["name"], "type": "score", "time_limit_minutes": spec["limit"],
                "penalty_per_minute": spec["penalty"],
                "controls": [{"code": k, "points": v} for k, v in spec["values"].items()]}
        if spec.get("mass"):
            body.update(start_mode="mass", mass_start=spec["mass"])
        if spec.get("formula"):
            body["score_formula"] = spec["formula"]
        course = c.post("/api/courses", body, expect=201)["course"]
        check.equal(course.get("start_mode"), "mass" if spec.get("mass") else "clock",
                    f"{spec['name']} start mode saved")
        spec["id"] = course["id"]
        cls_ids[cls] = c.post("/api/classes", {"name": cls, "course_id": course["id"]},
                              expect=201)["class"]["id"]

    names = runs.names(rng, 90)
    card = 7100001
    who = {}
    for i, cls in enumerate(specs):
        chunk = names[i * 30:(i + 1) * 30]
        _people(c, cls_ids[cls], chunk, range(card, card + 30), rng=rng)
        for n, k in zip(chunk, range(card, card + 30)):
            who[n] = (cls, k)
        card += 30
    c.post("/api/draw", {"class_ids": [cls_ids["S-Long"], cls_ids["S-Formula"]],
                         "first_start": "10:00:00", "interval_seconds": 30, "method": "random"},
           expect=200)
    comps = {x["name"]: x for x in c.competitors()}
    want = {}
    for name, (cls, k) in who.items():
        spec = specs[cls]
        start = at(day, spec["mass"]) if spec.get("mass") else at(day, comps[name]["start"])
        kind = rng.choice(["clean", "clean", "clean", "over", "over", "repeat", "dnf"])
        plan = runs.score_run(rng, spec["values"], start, spec["limit"], kind)
        if kind == "repeat" or rng.random() < 0.2:
            plan["punches"].append((99, plan["punches"][-1][1] + timedelta(seconds=5))
                                   if plan["punches"] else (99, start + timedelta(minutes=1)))
            plan["punches"].sort(key=lambda p: p[1])
        c.read(k, None, plan["finish"], plan["punches"], check_time=plan["check"])
        course = {"type": "score", "controls": spec["values"],
                  "time_limit_minutes": spec["limit"], "penalty_per_minute": spec["penalty"]}
        if spec.get("mass"):
            course.update(start_mode="mass", mass_start=start)
        res = oracle.result(dict(plan, start=None if spec.get("mass") else start), course)
        if spec.get("formula") and res["status"] in ("ok", "oot"):
            hit = {code for code, _ in plan["punches"] if code in spec["values"]
                   and plan["start"] <= _ <= plan["finish"]}
            over = 0
            if res["seconds"] > spec["limit"] * 60:
                over = -(-(res["seconds"] - spec["limit"] * 60) // 60)
            res["points"] = max(0, len(hit) * 10 - over * 3)
        want.setdefault(cls, {})[name] = res
    _compare(c, check, "score-O", want, score=True)
    # A mass-start runner who never left the line is DNS, not timed from the gun.
    out = c.post("/api/competitors", {"name": "Stayed Home", "club": "Solo",
                                      "class_id": cls_ids["S-Mass"], "card_number": 7199999},
                 expect=201)
    c.read(7199999, None, None, [], check_time=None)
    check.equal(c.results()["S-Mass"].get("Stayed Home", {}).get("status"), "dns",
                "an empty card on a mass start is DNS")
    for path in ("/results", "/splits", "/live", "/prizes", "/courses", "/speaker"):
        c.page(path)
    xml = c.get("/export/results.xml", raw=True).text
    check(ET.fromstring(xml).tag.endswith("ResultList"), "IOF export of a score event")


# ---------------------------------------------------------------------------
# Punch start
# ---------------------------------------------------------------------------

def punch_start_event(c: Client, check: Check, rng) -> None:
    check.part("punch start")
    day = YESTERDAY
    c.new_event("Stress Punch Start", day, first_start="09:00:00")
    c.setting(results_unplaced="all")
    controls = [31, 32, 33, 34, 35, 36]
    course = c.post("/api/courses", {"name": "Free", "type": "linear", "controls": controls,
                                     "start_mode": "punch", "start_control": 100},
                    expect=201)["course"]
    cls = c.post("/api/classes", {"name": "Free Start", "course_id": course["id"]},
                 expect=201)["class"]["id"]
    names = runs.names(rng, 25)
    _people(c, cls, names, range(7200001, 7200026), rng=rng)
    want = {"Free Start": {}}
    for i, name in enumerate(names):
        go = at(day, "09:00:00") + timedelta(minutes=rng.randint(0, 120), seconds=rng.randint(0, 59))
        plan = runs.linear_run(rng, controls, go, "clean" if i % 6 else "skip")
        punches = list(plan["punches"])
        if i != 3:                                   # runner 3 forgot the start punch
            punches = [(100, go)] + punches
        c.read(7200001 + i, None, plan["finish"], punches)
        want["Free Start"][name] = oracle.result(
            {"start": None, "finish": plan["finish"], "punches": punches},
            {"type": "linear", "controls": controls, "start_mode": "punch", "start_control": 100})
    check.equal(want["Free Start"][names[3]]["status"], "dns", "(oracle) no start punch is DNS")
    _compare(c, check, "punch start", want)
    c.page("/results")
    c.page("/setup", contains="punch")


# ---------------------------------------------------------------------------
# Relays
# ---------------------------------------------------------------------------

def relay_event(c: Client, check: Check, rng) -> None:
    check.part("relay")
    day = YESTERDAY
    c.new_event("Stress Relay", day, first_start="11:00:00")
    c.setting(results_unplaced="all", prize_places=3)
    base = [41, 42, 43, 44, 45, 46, 47, 48]
    forks = {"X1": [41, 42, 43, 44, 45, 46, 47, 48], "X2": [41, 43, 42, 44, 46, 45, 47, 48],
             "X3": [41, 42, 44, 43, 45, 47, 46, 48]}
    fork_ids = {}
    for name, codes in forks.items():
        fork_ids[name] = c.post("/api/courses", {"name": name, "type": "linear",
                                                 "controls": codes}, expect=201)["course"]["id"]
    mass = c.post("/api/courses", {"name": "MassLeg", "type": "linear", "controls": base,
                                   "start_mode": "mass", "mass_start": "11:30:00"},
                  expect=201)["course"]
    relay = c.post("/api/classes", {"name": "Relay", "course_id": fork_ids["X1"], "kind": "relay",
                                    "legs": 3, "restart": "12:40:00",
                                    "fork_courses": list(fork_ids.values())},
                   expect=201)["class"]["id"]
    relay2 = c.post("/api/classes", {"name": "Mass Relay", "course_id": mass["id"],
                                     "kind": "relay", "legs": 2}, expect=201)["class"]["id"]
    # Teams: Relay has a team start at 11:00; Mass Relay starts on the course's gun.
    teams = {}
    card = 7300001
    for t in range(10):
        team = c.post("/api/teams", {"name": f"Team R{t + 1}", "club": runs.CLUBS[t % 8],
                                     "class_id": relay, "bib": t + 1, "start": "11:00:00"},
                      expect=201)["team"]
        teams[team["name"]] = {"id": team["id"], "class": "Relay", "legs": {}}
        for leg in (1, 2, 3):
            name = f"R{t + 1} Leg {leg}"
            comp = c.post("/api/competitors", {"name": name, "club": runs.CLUBS[t % 8],
                                               "class_id": relay, "card_number": card,
                                               "team_id": team["id"], "leg": leg},
                          expect=201)["competitor"]
            teams[team["name"]]["legs"][leg] = {"name": name, "card": card, "id": comp["id"]}
            card += 1
    for t in range(6):
        team = c.post("/api/teams", {"name": f"Team M{t + 1}", "club": runs.CLUBS[t % 8],
                                     "class_id": relay2, "bib": 50 + t}, expect=201)["team"]
        teams[team["name"]] = {"id": team["id"], "class": "Mass Relay", "legs": {}}
        for leg in (1, 2):
            name = f"M{t + 1} Leg {leg}"
            comp = c.post("/api/competitors", {"name": name, "club": runs.CLUBS[t % 8],
                                               "class_id": relay2, "card_number": card,
                                               "team_id": team["id"], "leg": leg},
                          expect=201)["competitor"]
            teams[team["name"]]["legs"][leg] = {"name": name, "card": card, "id": comp["id"]}
            card += 1
    out = c.post(f"/api/classes/{relay}/forks", {}, expect=200)
    comps = {x["name"]: x for x in c.competitors()}
    order = sorted([n for n in teams if n.startswith("Team R")], key=lambda n: int(n[6:]))
    fork_list = list(fork_ids.values())
    wrong = []
    for ti, tname in enumerate(order):
        for leg in (1, 2, 3):
            got = comps[teams[tname]["legs"][leg]["name"]]["course_id"]
            if got != fork_list[(ti + leg - 1) % 3]:
                wrong.append((tname, leg, got))
    check(not wrong, "every team runs every fork once", wrong[:4])
    by_id = {v: k for k, v in fork_ids.items()}

    # Runs. Leg 1 at the team start; each next leg at the changeover, or at the
    # 12:40 restart for teams whose previous runner was still out then.
    restart = at(day, "12:40:00")
    faults = {"Team R3": (2, "skip"), "Team R5": (3, "not_read"), "Team R7": (2, "dnf"),
              "Team M2": (2, "skip")}
    slow_team = "Team R9"
    want_legs, want_team = {}, {}
    for tname, team in teams.items():
        is_mass = team["class"] == "Mass Relay"
        leg_start = at(day, "11:30:00") if is_mass else at(day, "11:00:00")
        total, ok = 0, True
        prev_finish = None
        for leg in sorted(team["legs"]):
            person = team["legs"][leg]
            codes = base if is_mass else forks[by_id[comps[person["name"]]["course_id"]]]
            if leg > 1:
                if is_mass:
                    leg_start = prev_finish          # no restart in this class
                elif prev_finish is not None and prev_finish <= restart:
                    leg_start = prev_finish          # the changeover
                else:
                    leg_start = restart              # still out at the mass restart
            fault = faults.get(tname, (0, "clean"))
            kind = fault[1] if fault[0] == leg else "clean"
            pace = (400, 700) if tname == slow_team and leg < 3 else (120, 240)
            plan = runs.linear_run(rng, codes, leg_start, kind, pace=pace)
            if kind != "not_read":
                c.read(person["card"], None, plan["finish"], plan["punches"])
            res = oracle.result(dict(plan, start=leg_start), {"type": "linear", "controls": codes})
            want_legs.setdefault(team["class"], {})[person["name"]] = (leg, res)
            if res["status"] == "ok":
                total += res["seconds"]
            else:
                ok = False
            prev_finish = plan["finish"]
        want_team.setdefault(team["class"], {})[tname] = total if ok else None

    data = c.get(f"/public/{c.slug()}/results.json", public=True)
    by_class = {b["name"]: b for b in data["classes"]}
    for cls, want in want_team.items():
        teams_got = {t["name"]: t for t in by_class.get(cls, {}).get("teams", [])}
        check(teams_got, f"{cls}: team standings published")
        ranked = sorted((n for n, v in want.items() if v is not None), key=lambda n: want[n])
        bad = []
        for n, secs in want.items():
            got = teams_got.get(n)
            if got is None:
                bad.append((n, "missing"))
            elif got["seconds"] != secs:
                bad.append((n, "seconds", secs, got["seconds"]))
            elif (got["place"] or None) != (ranked.index(n) + 1 if secs is not None else None):
                bad.append((n, "place", ranked.index(n) + 1 if secs else None, got["place"]))
        check(not bad, f"{cls}: team times and places", bad[:5])
        # Legs placed against the same leg only.
        rows = {r["name"]: r for r in by_class.get(cls, {}).get("results", [])}
        legs = {}
        for name, (leg, res) in want_legs[cls].items():
            legs.setdefault(leg, {})[name] = res
        bad = []
        for leg, results in legs.items():
            places = oracle.places(results)
            for name, res in results.items():
                row = rows.get(name)
                if res["status"] == "pending":
                    continue
                if row is None or row["status"] != res["status"] or \
                        (res["status"] == "ok" and row["seconds"] != res["seconds"]) or \
                        row["place"] != places[name]:
                    bad.append((name, leg, res["status"], res["seconds"], places[name],
                                row and (row["status"], row["seconds"], row["place"])))
        check(not bad, f"{cls}: each leg timed and placed on its own", bad[:5])
    prizes = c.page("/prizes")
    winner = min((n for n, v in want_team["Relay"].items() if v), key=lambda n: want_team["Relay"][n])
    check(winner in prizes, "relay prize goes to the winning team", winner)
    for path in ("/teams", "/results", "/splits", "/live", "/speaker", "/download"):
        c.page(path)
    xml = c.get("/export/results.xml", raw=True).text
    check(ET.fromstring(xml).tag.endswith("ResultList"), "IOF export of a relay event")


# ---------------------------------------------------------------------------
# Patrols
# ---------------------------------------------------------------------------

def patrol_event(c: Client, check: Check, rng) -> None:
    check.part("patrol")
    day = YESTERDAY
    c.new_event("Stress Patrol", day, first_start="10:00:00")
    controls = [31, 32, 33, 34, 35]
    course = c.post("/api/courses", {"name": "P", "type": "linear", "controls": controls},
                    expect=201)["course"]
    cls = c.post("/api/classes", {"name": "Patrol", "course_id": course["id"],
                                  "kind": "patrol"}, expect=201)["class"]["id"]
    card = 7400001
    want = {}
    for t in range(4):
        team = c.post("/api/teams", {"name": f"Patrol {t + 1}", "class_id": cls,
                                     "start": "10:00:00"}, expect=201)["team"]
        start = at(day, "10:00:00")
        plan = runs.linear_run(rng, controls, start, "clean" if t != 2 else "skip")
        for m in (1, 2):
            c.post("/api/competitors", {"name": f"P{t + 1}.{m}", "class_id": cls,
                                        "card_number": card, "team_id": team["id"], "leg": m},
                   expect=201)
            # Both carry cards; one of them missed a punch the other got.
            punches = plan["punches"] if m == 1 else plan["punches"][1:]
            c.read(card, start, plan["finish"], punches)
            card += 1
        want[f"Patrol {t + 1}"] = oracle.result(dict(plan, start=start),
                                                {"type": "linear", "controls": controls})
    teams = c.page("/teams")
    for name, res in want.items():
        check(name in teams, f"{name} on the teams page")
    c.page("/results")


# ---------------------------------------------------------------------------
# A night event across midnight
# ---------------------------------------------------------------------------

def night_event(c: Client, check: Check, rng) -> None:
    check.part("night")
    day = YESTERDAY - timedelta(days=1)
    c.new_event("Stress Night", day, first_start="22:30:00")
    c.setting(results_unplaced="all")
    controls = list(range(31, 43))
    course = c.post("/api/courses", {"name": "Night", "type": "linear", "controls": controls,
                                     "time_limit_minutes": 120}, expect=201)["course"]
    cls = c.post("/api/classes", {"name": "Night Open", "course_id": course["id"]},
                 expect=201)["class"]["id"]
    names = runs.names(rng, 40)
    _people(c, cls, names, range(7500001, 7500041), rng=rng)
    c.post("/api/draw", {"class_ids": [cls], "first_start": "22:30:00", "interval_seconds": 60,
                         "method": "random"}, expect=200)
    comps = {x["name"]: x for x in c.competitors()}
    want = {"Night Open": {}}
    crossed = 0
    for i, name in enumerate(names):
        start = at(day, comps[name]["start"])
        kind = ["clean", "clean", "clean", "skip", "slow", "own_start_punch"][i % 6]
        plan = runs.linear_run(rng, controls, start, kind, pace=(200, 420), slow_minutes=60)
        crossed += plan["finish"].date() > start.date()
        c.read(7500001 + i, plan["card_start"], plan["finish"], plan["punches"],
               check_time=plan["check"])
        want["Night Open"][name] = oracle.result(
            dict(plan, start=plan["start"]),
            {"type": "linear", "controls": controls, "time_limit_minutes": 120})
    check(crossed > 10, "plenty of runs cross midnight", crossed)
    _compare(c, check, "night", want)
    c.page("/splits")
    c.page("/results")


# ---------------------------------------------------------------------------
# Happening right now
# ---------------------------------------------------------------------------

def live_event(c: Client, check: Check, rng) -> None:
    check.part("live (today)")
    now = datetime.now().replace(microsecond=0)
    today = date.today()
    base = max(now - timedelta(minutes=45), datetime.combine(today, datetime.min.time())
               + timedelta(minutes=2))
    c.new_event("Stress Live", today, first_start=base.strftime("%H:%M:%S"))
    c.setting(results_unplaced="all", results_show_on_course=True)
    controls = [31, 32, 33, 34, 35, 36, 37, 38]
    course = c.post("/api/courses", {"name": "Live", "type": "linear", "controls": controls},
                    expect=201)["course"]
    cls = c.post("/api/classes", {"name": "Live Class", "course_id": course["id"]},
                 expect=201)["class"]["id"]
    ids = {}
    groups = {"home": [], "out": [], "later": []}
    for i in range(24):
        name = f"Live Runner {i + 1}"
        if i < 10:
            start, group = base + timedelta(minutes=i), "home"
        elif i < 18:
            start, group = now - timedelta(minutes=20 - i), "out"
        else:
            start, group = now + timedelta(minutes=10 + i), "later"
        if start.date() != today:
            start = datetime.combine(today, datetime.min.time()) + timedelta(minutes=1 + i)
        groups[group].append(name)
        ids[name] = c.post("/api/competitors", {"name": name, "club": "Solo", "class_id": cls,
                                                "card_number": 7600001 + i,
                                                "start": start.strftime("%H:%M:%S")},
                           expect=201)["competitor"]["id"]
    for name in groups["home"]:
        i = int(name.split()[-1]) - 1
        start = datetime.strptime(c.get(f"/api/competitors/{ids[name]}")["competitor"]["start"],
                                  "%H:%M:%S")
        start = datetime.combine(today, start.time())
        plan = runs.linear_run(rng, controls, start, "clean", pace=(60, 120))
        if plan["finish"] > now:
            continue
        c.read(7600001 + i, None, plan["finish"], plan["punches"])
    for name in groups["out"][:4]:
        i = int(name.split()[-1]) - 1
        c.post("/api/radio/punch", {"card_number": 7600001 + i, "code": 34,
                                    "time": (now - timedelta(minutes=2)).strftime("%H:%M:%S")},
               expect=200)
    got = c.results().get("Live Class", {})
    check(not any(n in got for n in groups["out"] + groups["later"]),
          "runners still out aren't in the results yet")
    html = c.page("/results")
    check(sum(1 for n in groups["out"] if n in html) >= len(groups["out"]) - 1,
          "results page lists who's still out")
    comps_page = c.page("/competitors")
    check("On course" in comps_page or "on course" in comps_page.lower(), "On course label")
    check("Not started" in comps_page, "Not started label")
    speaker = c.page("/speaker")
    check(any(n in speaker for n in groups["out"][:4]), "speaker shows radio runners")
    starters = c.get("/api/starters")
    check(starters.get("now") and isinstance(starters.get("starters"), list), "start clock data")
    later = [s["name"] for s in starters["starters"]]
    check(all(n in later for n in groups["later"]), "start clock lists the later starters")
    latest = c.get("/api/readout/latest")
    check(isinstance(latest, dict), "readout screen data")
    for path in ("/", "/live", "/starter", "/readout", "/download", "/setup"):
        c.page(path)
    out = c.post("/api/close-out/remaining", {"status": "dnf"}, expect=200)
    check(out.get("count", 0) >= len(groups["out"]), "close-out: runners out marked DNF",
          out)
