"""
A whole event, start to finish, through the app's own HTTP API.

Day 1 of a two-day club champs: courses from an OCAD-style IOF file, classes,
entries (CSV, online, on the day), a relay, the draw, bibs, radio punches,
card reads of every kind (OK, mispunch, retired, score over time, re-read,
unknown card, walk-up), a broken control, a DSQ, payments, close-out,
printing, every page and export. Then day 2 with a chase start, and the
season. Checks what the app says against what the runs should give.

Run it against a running app with an empty events folder and slips going to
a PNG folder (tests/test_full_event.py does all that):

    BMEOS_SIM_ADMIN=http://127.0.0.1:8799 BMEOS_SIM_PUBLIC=http://127.0.0.1:8800 \
    BMEOS_SIM_PRINTED=<the app's BMEOS_PRINT_DIR> python scripts/simulate_event.py

Exits non-zero, listing the problems, if anything is off.
"""

import glob
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import requests

A = os.environ.get("BMEOS_SIM_ADMIN", "http://127.0.0.1:8799")
P = os.environ.get("BMEOS_SIM_PUBLIC", "http://127.0.0.1:8800")
PRINTED = os.environ.get("BMEOS_SIM_PRINTED", "")
DAY = str(date.today() - timedelta(days=1))     # a finished day: nobody is still "out"
random.seed(7)

problems, passes = [], 0


def check(ok, what, detail=""):
    global passes
    if ok:
        passes += 1
    else:
        problems.append(f"{what} {detail}".strip())
        print("  PROBLEM:", what, detail)


def api(method, path, body=None, base=A, files=None, expect=None):
    url = base + path
    if files:
        r = requests.request(method, url, files=files, data=body or {})
    else:
        r = requests.request(method, url, json=body)
    if expect is not None and r.status_code != expect:
        check(False, f"{method} {path} -> {r.status_code}", r.text[:300])
    try:
        return r.json()
    except ValueError:
        return r


def page(path, base=A, name=None):
    r = requests.get(base + path)
    bad = r.status_code != 200 or "Traceback" in r.text or "Internal Server Error" in r.text
    check(not bad, f"page {path}", f"-> {r.status_code}")
    return r


def t(hms):
    return datetime.strptime(f"{DAY} {hms}", "%Y-%m-%d %H:%M:%S")


def clock(dt):
    return dt.strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
print("== Setup")
ev = api("POST", "/api/events/new", {"name": "Sim Champs Day 1", "date": DAY,
                                     "first_start": "10:00:00"}, expect=201)
slug = ev["event"]["slug"]
api("POST", "/api/settings", {"target": "event", "values": {
    "season_name": "Sim Series 2026", "slip_footer": "Results at sim.example"}}, expect=200)
api("POST", "/api/settings", {"target": "computer", "values": {"auto_print": "all"}}, expect=200)

COURSES = {"Long": [31, 32, 33, 34, 35, 36, 37, 38], "Medium": [31, 33, 35, 37, 39],
           "Short": [41, 42, 43, 44]}
ASSIGN = {"M21E": "Long", "W21E": "Long", "M35": "Medium", "W35": "Medium", "Juniors": "Short"}
NS = "http://www.orienteering.org/datastandard/3.0"
xml = [f'<?xml version="1.0"?><CourseData xmlns="{NS}" iofVersion="3.0"><RaceCourseData>']
for name, codes in COURSES.items():
    xml.append(f"<Course><Name>{name}</Name><Length>{len(codes) * 450}</Length>"
               '<CourseControl type="Start"><Control>S1</Control></CourseControl>')
    for code in codes:
        xml.append(f'<CourseControl type="Control"><Control>{code}</Control>'
                   f"<LegLength>{random.randint(250, 700)}</LegLength></CourseControl>")
    xml.append('<CourseControl type="Finish"><Control>F1</Control></CourseControl></Course>')
for cls, course in ASSIGN.items():
    xml.append(f"<ClassCourseAssignment><ClassName>{cls}</ClassName>"
               f"<CourseName>{course}</CourseName></ClassCourseAssignment>")
xml.append("</RaceCourseData></CourseData>")
out = api("POST", "/api/import/courses", files={"file": ("courses.xml", "".join(xml))}, expect=200)
print("  courses:", out)

score = api("POST", "/api/courses", {"name": "Score 45", "type": "score",
                                     "controls": [{"code": 51, "points": 10}, {"code": 52, "points": 20},
                                                  {"code": 53, "points": 30}, {"code": 54, "points": 40}],
                                     "time_limit_minutes": 45, "penalty_per_minute": 5},
            expect=201)
classes_html = page("/classes").text
check(all(c in classes_html for c in ASSIGN), "classes made from the course file")


def class_ids():
    return {m.group(2): int(m.group(1)) for m in re.finditer(
        r'data-class-row data-id="(\d+)" data-name="([^"]+)"', page("/classes").text)}


def course_ids():
    ids = {}
    for i in range(1, 30):
        r = requests.get(f"{A}/api/courses/{i}")
        if r.status_code == 200:
            c = r.json().get("course", r.json())
            ids[c["name"]] = c["id"]
    return ids


cids = course_ids()
if "Score 45" not in cids:
    cids["Score 45"] = score.get("course", score).get("id") if isinstance(score, dict) else None
print("  course ids:", cids)
api("POST", "/api/classes", {"name": "Score-O", "course_id": cids.get("Score 45")}, expect=201)
api("POST", "/api/classes", {"name": "Relay", "course_id": cids.get("Short"), "kind": "relay",
                             "legs": 3, "restart": "12:30:00"}, expect=201)
CLS = class_ids()
print("  classes:", sorted(CLS))
for name, fee in (("M21E", 25), ("W21E", 25), ("M35", 20), ("W35", 20), ("Juniors", 8)):
    cls_row = re.search(rf'data-class-row data-id="{CLS[name]}"[^>]*data-course-id="(\d+)"',
                        page("/classes").text)
    api("PUT", f"/api/classes/{CLS[name]}", {"name": name, "course_id": cls_row.group(1),
                                              "fee": fee}, expect=200)
check(set(ASSIGN) | {"Score-O", "Relay"} <= set(CLS), "all classes exist", str(CLS))

# ---- Entries --------------------------------------------------------------
print("== Entries")
FIRST = ["Ada", "Ben", "Cleo", "Dan", "Eve", "Finn", "Gus", "Hana", "Ivo", "Jess", "Kai", "Lou",
         "Mia", "Ned", "Ola", "Pip", "Rae", "Sol", "Tia", "Uri", "Vik", "Wes", "Xan", "Yas", "Zoe"]
CLUBS = ["BO", "WOW", "KO", "SWOT", "LOST"]
csv_rows = ["name,club,class,card"]
people = []
card = 8100001
for cls in ("M21E", "W21E", "M35", "W35", "Juniors", "Score-O"):
    for i in range(5 if cls != "Score-O" else 4):
        name = f"{random.choice(FIRST)} {cls.replace('-', '')}{i}"
        people.append({"name": name, "club": CLUBS[(card + i) % 5], "class": cls, "card": card})
        csv_rows.append(f"{name},{CLUBS[(card + i) % 5]},{cls},{card}")
        card += 1
out = api("POST", "/api/import/startlist", files={"file": ("entries.csv", "\n".join(csv_rows))},
          expect=200)
check(out.get("created") == len(people), "CSV entries all created", str(out))

# Online entry on the public port (free here: no PayPal set up, so the fees
# go to zero for these two and back after).
api("POST", "/api/settings", {"target": "event", "values": {"fee_senior": 0, "fee_junior": 0}},
    expect=200)
r = requests.post(P + "/api/online-entry/order", json={"entries": [
    {"name": "Online Olga", "club": "WOW", "card": "8190001", "classId": CLS["W35"]},
    {"name": "Online Oscar", "club": "KO", "card": "8190002", "classId": CLS["M35"]}]})
check(r.status_code == 200, "online entry order", r.text[:200])
api("POST", "/api/settings", {"target": "event", "values": {"fee_senior": 15, "fee_junior": 8}},
    expect=200)
people += [{"name": "Online Olga", "club": "WOW", "class": "W35", "card": 8190001},
           {"name": "Online Oscar", "club": "KO", "class": "M35", "card": 8190002}]

# On the day: a hire-card runner with no card yet.
hire = api("POST", "/api/competitors", {"name": "Hire Harriet", "club": "LOST",
                                        "class_id": CLS["Juniors"]}, expect=201)
hire_id = hire.get("competitor", hire).get("id")

# Relay: two teams of three.
relay_people = []
for team_no, club in ((1, "BO"), (2, "KO")):
    team = api("POST", "/api/teams", {"name": f"{club} Relay {team_no}", "club": club,
                                      "class_id": CLS["Relay"]}, expect=201)["team"]
    for leg in (1, 2, 3):
        c = api("POST", "/api/competitors", {
            "name": f"{club} Leg{leg}", "club": club, "class_id": CLS["Relay"],
            "card_number": 8180000 + team_no * 10 + leg, "team_id": team["id"], "leg": leg},
            expect=201)
        relay_people.append({"name": f"{club} Leg{leg}", "team": team_no, "leg": leg,
                             "card": 8180000 + team_no * 10 + leg})
check(len(api("GET", "/api/teams").get("teams", api("GET", "/api/teams"))) >= 2
      if isinstance(api("GET", "/api/teams"), dict) else True, "teams listed")

# ---- Draw -----------------------------------------------------------------
print("== Draw")
indiv = [CLS[c] for c in ("M21E", "W21E", "M35", "W35", "Juniors", "Score-O")]
out = api("POST", "/api/draw", {"class_ids": indiv, "first_start": "10:00:00",
                                "interval_seconds": 60, "method": "club", "vacants": 1},
          expect=200)
print("  draw:", {k: v for k, v in out.items() if k != "classes"} if isinstance(out, dict) else out)
api("POST", "/api/bibs/assign", {"start": 100}, expect=200)
starters = api("GET", "/api/starters")
check(len(starters.get("starters", [])) >= len(people), "starter list has everyone",
      str(len(starters.get("starters", []))))
page("/draw"); page("/starter"); page("/export/startlist.pdf"); page("/export/bibs.pdf")

# Who starts when (from the API).
comps = {}
for m in re.finditer(r'class="entry-row" data-id="(\d+)"', page("/competitors").text):
    cid = int(m.group(1))
    comps[cid] = api("GET", f"/api/competitors/{cid}")["competitor"]
by_card = {c["card_number"]: c for c in comps.values() if c.get("card_number")}
check(all(p["card"] in by_card for p in people), "every entry has a competitor", str(len(by_card)))
check(all(by_card[p["card"]]["start"] for p in people if p["card"] in by_card),
      "every individual entry got a start time")
vacants = [c for c in comps.values() if c.get("vacant")]
check(len(vacants) >= 5, "vacant slots drawn", str(len(vacants)))

# ---- The race ---------------------------------------------------------------
print("== Race")
expected = {}          # card -> (status, seconds)
reads = 0


def read(card_no, start, finish, punches, expect_ok=True):
    global reads
    body = {"card_number": card_no, "start": clock(start) if start else None,
            "finish": clock(finish) if finish else None,
            "punches": [{"code": c, "time": clock(tm)} for c, tm in punches]}
    r = requests.post(A + "/api/reader/simulate", json=body)
    reads += 1
    if expect_ok:
        check(r.status_code == 200, f"read {card_no}", r.text[:200])
    return r.json()


plan = {}
for p in people:
    comp = by_card.get(p["card"])
    if not comp:
        continue
    st = t(comp["start"])
    codes = COURSES.get(ASSIGN.get(p["class"])) if p["class"] != "Score-O" else [51, 52, 53, 54]
    pace = random.randint(240, 420)
    tm, punches = st, []
    for code in codes:
        tm += timedelta(seconds=pace + random.randint(-60, 90))
        punches.append((code, tm))
    fin = tm + timedelta(seconds=random.randint(40, 90))
    plan[p["card"]] = [st, fin, punches, p]

cards = list(plan)
mp_card, mp33_card, dnf_card, dns_card, dsq_card = cards[1], cards[2], cards[6], cards[11], cards[16]
plan[mp_card][2] = [x for x in plan[mp_card][2] if x[0] != 34]      # misses 34
plan[mp33_card][2] = [x for x in plan[mp33_card][2] if x[0] != 33]  # misses 33 (broken later)
plan[dnf_card][1] = None                                            # retired, no finish
score_cards = [p["card"] for p in people if p["class"] == "Score-O" and p["card"] in plan]
# Score runner 1 is 6 minutes over the 45: 30 points of penalty.
s0 = plan[score_cards[0]]
s0[1] = s0[0] + timedelta(minutes=51)
# Score runner 2 skips the 40-pointer.
plan[score_cards[1]][2] = [x for x in plan[score_cards[1]][2] if x[0] != 54]

# Radio punches at 35 for Long runners, before they finish.
radio = 0
for c, (st, fin, punches, p) in plan.items():
    for code, tm in punches:
        if code == 35 and p["class"] in ("M21E", "W21E"):
            r = requests.post(A + "/api/radio/punch", json={"card_number": c, "code": 35,
                                                            "time": clock(tm)})
            check(r.status_code == 200, "radio punch", r.text[:120])
            radio += 1
page("/speaker")

for c, (st, fin, punches, p) in plan.items():
    if c == dns_card:
        continue
    read(c, st, fin, punches)

# Re-read of the same card (a runner reads out twice): nothing may change.
def results():
    return requests.get(f"{P}/public/{slug}/results.json").json()["classes"]


def result_of(card_no):
    name = by_card[card_no]["name"]
    for cls in results():
        for r in cls["results"]:
            if r["name"] == name:
                return r
    return None


before = result_of(cards[0])
read(cards[0], *plan[cards[0]][:3])
after = result_of(cards[0])
check(before == after, "re-read changes nothing", f"{before} -> {after}")

# A card nobody entered: the hire card Harriet ran with.
st = t("11:30:00")
hire_punches = [(41, st + timedelta(minutes=4)), (42, st + timedelta(minutes=9)),
                (43, st + timedelta(minutes=13)), (44, st + timedelta(minutes=18))]
out = read(8199999, st, st + timedelta(minutes=20), hire_punches, expect_ok=False)
check(not out.get("ok") and out.get("read_id"), "unknown card kept", str(out)[:200])
if out.get("read_id"):
    r = requests.post(f"{A}/api/card-reads/{out['read_id']}/assign", json={"competitor_id": hire_id})
    check(r.status_code == 200, "kept read given to the hire-card runner", r.text[:200])

# A walk-up nobody entered at all: quick entry from the read.
st = t("11:40:00")
out = read(8199998, st, st + timedelta(minutes=25),
           [(31, st + timedelta(minutes=5)), (33, st + timedelta(minutes=11)),
            (35, st + timedelta(minutes=16)), (37, st + timedelta(minutes=20)),
            (39, st + timedelta(minutes=23))], expect_ok=False)
if out.get("read_id"):
    r = requests.post(f"{A}/api/card-reads/{out['read_id']}/enter",
                      json={"name": "Walkup Wally", "club": "SWOT", "class_id": CLS["M35"]})
    check(r.status_code == 200, "walk-up entered from the read", r.text[:200])

# Relay legs: leg 1 mass start 11:00, each leg starts at the previous finish.
for team_no in (1, 2):
    leg_start = t("11:00:00")
    for leg in (1, 2, 3):
        cardno = 8180000 + team_no * 10 + leg
        tm, punches = leg_start, []
        for code in COURSES["Short"]:
            tm += timedelta(seconds=random.randint(200, 320))
            punches.append((code, tm))
        fin = tm + timedelta(seconds=60)
        read(cardno, leg_start, fin, punches)
        leg_start = fin

# ---- Check the results --------------------------------------------------------
print("== Results")
res = results()
by_class = {c["name"]: c for c in res}


def row_for(card_no):
    return result_of(card_no)


for c, (st, fin, punches, p) in plan.items():
    r = row_for(c)
    if c == dns_card:
        continue
    if r is None:
        check(False, "runner on results", p["name"])
        continue
    if p["class"] == "Score-O":
        continue
    want = "mp" if c in (mp_card, mp33_card) else "dnf" if c == dnf_card else "ok"
    got = str(r.get("status", "")).lower()
    check(got == want, f"status {p['name']}", f"want {want} got {got}")
    if want == "ok":
        secs = int((fin - st).total_seconds())
        m, s = divmod(secs, 60)
        check(r.get("seconds") == secs, f"time {p['name']}", f"want {secs} got {r.get('seconds')}")

# Places in M21E follow the times.
m21e = [r for r in by_class.get("M21E", {}).get("results", []) if str(r.get("status")).lower() == "ok"]
print("  M21E:", [(r["place"], r["name"], r["time"]) for r in m21e])


def secs_of(txt):
    parts = [int(x) for x in str(txt).split(":")]
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


ok_sorted = sorted(m21e, key=lambda r: r["seconds"])
places = [r["place"] for r in ok_sorted]
check(places == sorted(places) and places[:1] == [1], "M21E places follow times", str(places))

# Score-O: over-time penalty and the missing 40-pointer.
sc = {r["name"]: r for r in by_class.get("Score-O", {}).get("results", [])}
p0 = plan[score_cards[0]][3]["name"]
p1 = plan[score_cards[1]][3]["name"]
print("  Score-O:", {n: (r.get("points"), r.get("time"), r.get("status")) for n, r in sc.items()})
check(sc.get(p0, {}).get("points") == 100 - 30, "score penalty 6 min x 5", str(sc.get(p0)))
check(sc.get(p1, {}).get("points") == 60, "score without the 40", str(sc.get(p1)))

# Relay: each runner placed against their own leg only; teams on the results page.
relay = by_class.get("Relay", {}).get("results", [])
leg_of = {rp["name"]: rp["leg"] for rp in relay_people}
for leg in (1, 2, 3):
    places = sorted(r["place"] for r in relay if leg_of.get(r["name"]) == leg and r["place"])
    check(places == [1, 2], f"relay leg {leg} places", str(places))
html = page("/results").text
check("BO Relay 1" in html and "KO Relay 2" in html, "relay teams on the results page")

# Hire-card runner and walk-up.
jun = {r["name"]: r for r in by_class.get("Juniors", {}).get("results", [])}
check(str(jun.get("Hire Harriet", {}).get("status", "")).lower() == "ok", "hire-card runner OK",
      str(jun.get("Hire Harriet")))
m35 = {r["name"]: r for r in by_class.get("M35", {}).get("results", [])}
check("Walkup Wally" in m35, "walk-up on results")

# ---- Control 33 broken ----------------------------------------------------------
print("== Broken control")
r = requests.post(A + "/api/controls/33", json={"status": "bad"})
check(r.status_code == 200, "mark 33 bad", r.text[:200])
res = results()
r33 = row_for(mp33_card)
check(str(r33.get("status")).lower() == "ok", "missing the broken 33 is OK now", str(r33))
r34 = row_for(mp_card)
check(str(r34.get("status")).lower() == "mp", "missing 34 still MP", str(r34))
page("/controls"); page("/audit")
check("33" in page("/audit").text, "broken control in the change log")

# ---- Protest: DSQ ---------------------------------------------------------------
r = requests.put(f"{A}/api/competitors/{by_card[dsq_card]['id']}", json={"manual_status": "dsq"})
check(r.status_code == 200, "DSQ saved", r.text[:200])
res = results()
check(str(row_for(dsq_card).get("status")).lower() in ("dsq", "dq"), "DSQ on results",
      str(row_for(dsq_card)))

# ---- Money ----------------------------------------------------------------------
print("== Economy")
paid = 0
for c in cards[:8]:
    r = requests.post(f"{A}/api/competitors/{by_card[c]['id']}/payment", json={"method": "cash"})
    check(r.status_code == 200, "payment", r.text[:200])
    paid += 1
page("/economy"); page("/export/invoices.pdf")

# ---- Close-out ------------------------------------------------------------------
print("== Close-out")
setup = page("/setup").text
r = requests.post(A + "/api/close-out/remaining", json={"status": "dns"})
check(r.status_code == 200 and r.json().get("count", 0) >= 1, "close-out marks the no-show DNS",
      r.text[:200])
res = results()
dns_row = row_for(dns_card)
check(dns_row is None or str(dns_row.get("status")).lower() == "dns", "no-show is DNS", str(dns_row))

# ---- Printing -------------------------------------------------------------------
requests.post(A + "/api/print/test")
time.sleep(2)
slips = glob.glob(os.path.join(PRINTED, "*.png")) if PRINTED else []
status = requests.get(A + "/api/print/status").json()
print(f"  {len(slips)} slips printed for {reads} reads; last: {status.get('last')}")
if PRINTED:
    check(len(slips) >= reads - 2, "a slip for every read", f"{len(slips)} of {reads}")
check(not status.get("failed"), "no failed prints", str(status.get("failed")))

# ---- Every page, the public side, exports --------------------------------------
print("== Pages and exports")
for path in ("/", "/competitors", "/classes", "/courses", "/draw", "/download", "/results",
             "/splits", "/live", "/speaker", "/teams", "/prizes", "/season", "/stages", "/clubs",
             "/economy", "/audit", "/controls", "/entries", "/eventor", "/setup", "/tools",
             "/config", "/readout", "/starter", "/export/results.pdf", "/export/prizes.pdf",
             f"/slip/{by_card[cards[0]]['id']}", f"/slip/{by_card[cards[0]]['id']}.pdf",
             f"/slip/{by_card[cards[0]]['id']}.png"):
    page(path)
xml_text = page("/export/results.xml").text
root = ET.fromstring(xml_text)
prs = root.findall(f".//{{{NS}}}PersonResult")
check(len(prs) >= len(people), "IOF results has everyone", str(len(prs)))
splits = root.findall(f".//{{{NS}}}SplitTime")
check(len(splits) > 100, "IOF results carry splits", str(len(splits)))
page(f"/public/{slug}", base=P)
pub = requests.get(f"{P}/public/{slug}/results.json").json()
check(pub.get("classes"), "public results.json")
page(f"/public/{slug}/runner/{by_card[cards[0]]['id']}", base=P)
page("/enter", base=P)
teams = requests.get(A + "/teams").text
check("BO Relay 1" in teams and "KO Relay 2" in teams, "relay teams on the teams page")
prizes = page("/prizes").text
check("M21E" in prizes, "prize list has M21E")
relay_prizes = prizes[prizes.index(">Relay<"):] if ">Relay<" in prizes else ""
check("BO Relay 1" in relay_prizes and "BO Leg1" not in relay_prizes, "relay prizes go to teams")
economy = page("/economy").text
check(economy.count("data-unpay>Undo") == paid, "Undo only where a payment was recorded",
      f"{economy.count('data-unpay>Undo')} vs {paid}")

# ---- Day 2: chase start, then season ----------------------------------------------
print("== Day 2")
ev_files = sorted(re.findall(r'value="([^"]+\.bmeos)"', page("/stages").text))
print("  event files:", ev_files)
ev2 = api("POST", "/api/events/new", {"name": "Sim Champs Day 2", "date": DAY,
                                      "first_start": "13:00:00"}, expect=201)
api("POST", "/api/settings", {"target": "event", "values": {"season_name": "Sim Series 2026"}},
    expect=200)
out = api("POST", "/api/import/courses", files={"file": ("courses.xml", "".join(xml))}, expect=200)
csv2 = ["name,club,class,card"] + [f"{p['name']},{p['club']},{p['class']},{p['card']}"
                                    for p in people if p["class"] in ("M21E", "W21E")]
api("POST", "/api/import/startlist", files={"file": ("e.csv", "\n".join(csv2))}, expect=200)
day1 = [f for f in ev_files if "day-1" in f] or ev_files[:1]
r = requests.post(A + "/api/stages/chase", json={"stages": day1, "first_start": "13:00:00"})
check(r.status_code == 200 and r.json().get("assigned", 0) >= 8, "chase starts from day 1",
      r.text[:200])
comps2 = {}
for m in re.finditer(r'class="entry-row" data-id="(\d+)"', page("/competitors").text):
    cid = int(m.group(1))
    comps2[cid] = api("GET", f"/api/competitors/{cid}")["competitor"]
starts = sorted((c["start"], c["name"]) for c in comps2.values() if c.get("start"))
print("  chase starts:", starts[:4])
winner = sorted(m21e, key=lambda r: r["seconds"])[0]["name"] if m21e else None
m21e_starts = sorted((c["start"], c["name"]) for c in comps2.values()
                     if c.get("start") and c.get("class_name") == "M21E")
check(winner is None or (m21e_starts and m21e_starts[0][1] == winner),
      "day 1 M21E winner starts first in the chase", f"{winner} vs {m21e_starts[:1]}")
for c in comps2.values():
    if not c.get("start") or not c.get("card_number"):
        continue
    st = t(c["start"])
    tm, punches = st, []
    for code in COURSES["Long"]:
        tm += timedelta(seconds=random.randint(240, 360))
        punches.append((code, tm))
    read(c["card_number"], st, tm + timedelta(seconds=50), punches)
stages_html = page("/stages").text
season_html = page("/season").text
check("Sim Champs Day 1" in stages_html, "stages page lists day 1")
check("Sim Series 2026" in season_html, "season standings for the series")

print()
print(f"{passes} checks passed, {len(problems)} problems")
for p in problems:
    print(" -", p)
sys.exit(1 if problems else 0)
