"""
A championship-sized event: 2,000 runners in 40 classes. Import, draw, read
every card, time every page cold and warm, put 40 phones on the public
results while cards keep coming in, and hold live-update streams open.
"""

from __future__ import annotations

import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import runs
from harness import YESTERDAY, App, Check, Client, at

N_RUNNERS = 2000
N_CLASSES = 40


def timed(fn):
    t0 = time.time()
    out = fn()
    return out, time.time() - t0


def run(check: Check) -> None:
    rng = random.Random(66)
    day = YESTERDAY
    with App("load") as app:
        c = Client(app, check)
        check.part("build")
        ev = c.new_event("Big Champs", day, first_start="09:00:00")
        c.setting(results_unplaced="all")
        courses = {}
        for i in range(10):
            codes = rng.sample(range(31, 120), 15)
            courses[i] = c.post("/api/courses", {"name": f"Course {i + 1}", "type": "linear",
                                                 "controls": codes}, expect=201)["course"]
        classes = {}
        for i in range(N_CLASSES):
            course = courses[i % 10]
            classes[f"Class {i + 1:02d}"] = (c.post("/api/classes", {
                "name": f"Class {i + 1:02d}", "course_id": course["id"]}, expect=201)["class"]["id"],
                course["controls"])
        names = runs.names(rng, N_RUNNERS)
        rows = ["name,club,class,card"]
        people = []
        for i, n in enumerate(names):
            cls = f"Class {i % N_CLASSES + 1:02d}"
            people.append((n, cls, 8000001 + i))
            rows.append(f'"{n}",{rng.choice(runs.CLUBS)},{cls},{8000001 + i}')
        out, t = timed(lambda: c.upload("/api/import/startlist", "big.csv", "\n".join(rows)))
        print(f"     import {N_RUNNERS} runners: {t:.1f}s")
        check.equal(out.get("created"), N_RUNNERS, "everyone imported")
        check(t < 30, "import is quick", f"{t:.1f}s")
        _, t = timed(lambda: c.post("/api/draw", {"class_ids": [v[0] for v in classes.values()],
                                                  "first_start": "09:00:00", "interval_seconds": 30,
                                                  "method": "club", "vacants": 2}, expect=200))
        print(f"     draw: {t:.1f}s")
        check(t < 30, "draw is quick", f"{t:.1f}s")
        _, t = timed(lambda: c.post("/api/bibs/assign", {"start": 1}, expect=200))
        check(t < 20, "bib numbers are quick", f"{t:.1f}s")
        comps = {x["name"]: x for x in c.competitors()}

        check.part("reads")
        plans = []
        for n, cls, card in people:
            start = at(day, comps[n]["start"])
            plan = runs.linear_run(rng, classes[cls][1], start,
                                   rng.choice(["clean"] * 8 + ["skip", "dnf"]))
            plans.append((card, plan))
        rng.shuffle(plans)
        times = []
        t0 = time.time()
        for card, plan in plans:
            _, t = timed(lambda: c.read(card, None, plan["finish"], plan["punches"]))
            times.append(t)
        total = time.time() - t0
        times.sort()
        p50, p95, worst = statistics.median(times), times[int(len(times) * .95)], times[-1]
        print(f"     {len(times)} reads one after another: {total:.0f}s, p50 {p50 * 1000:.0f}ms, "
              f"p95 {p95 * 1000:.0f}ms, worst {worst * 1000:.0f}ms")
        check(p95 < 0.5, "a card read takes under half a second (p95)", f"{p95:.3f}s")

        check.part("pages")
        pages = ["/", "/results", "/splits", "/live", "/competitors", "/download", "/speaker",
                 "/clubs", "/economy", "/prizes", "/controls", "/audit", "/teams", "/setup",
                 "/export/results.xml", "/export/results.pdf", "/export/startlist.pdf",
                 f"/slip/{comps[names[0]]['id']}"]
        slow = []
        for path in pages:
            c.post("/api/competitors/1/payment", {"method": "Cash", "amount": 1})  # bust caches
            r, cold = timed(lambda: c.s.get(app.admin + path, timeout=120))
            r2, warm = timed(lambda: c.s.get(app.admin + path, timeout=120))
            check(r.status_code == 200, f"{path} loads", r.status_code)
            print(f"     {path:<28} cold {cold * 1000:6.0f}ms   warm {warm * 1000:6.0f}ms")
            if cold > 8:
                slow.append((path, round(cold, 1)))
        check(not slow, "no operator page takes over 8 s even at 2,000 runners", slow)
        for path in (f"/public/{ev['slug']}", f"/public/{ev['slug']}/results.json", "/get-results"):
            r, t = timed(lambda: c.s.get(app.public + path, timeout=120))
            print(f"     public {path:<30} {t * 1000:6.0f}ms")
            check(r.status_code == 200 and t < 8, f"public {path}", f"{t:.1f}s")

        for label, pause, read_every, lat_limit, read_limit in (
                ("a realistic crowd: 60 phones reloading every 2-4 s, a card a second",
                 (2, 4), 1.0, 2.0, 1.0),
                ("an extreme crowd: 40 phones reloading non-stop, 5 cards a second",
                 None, 0.2, 5.0, 5.0)):
            check.part(label)
            stop = threading.Event()
            lat, errs = [], []

            def phone(pause=pause):
                s = requests.Session()
                paths = [f"/public/{ev['slug']}", "/results",
                         f"/public/{ev['slug']}/results.json", "/get-results", "/live"]
                while not stop.is_set():
                    t0 = time.time()
                    try:
                        r = s.get(app.public + rng.choice(paths), timeout=60)
                        if r.status_code != 200:
                            errs.append(r.status_code)
                    except Exception as err:  # noqa: BLE001
                        errs.append(str(err)[:60])
                    lat.append(time.time() - t0)
                    if pause:
                        stop.wait(rng.uniform(*pause))

            phones = [threading.Thread(target=phone, daemon=True)
                      for _ in range(60 if pause else 40)]
            for p in phones:
                p.start()
            read_lat = []
            t_end = time.time() + 20
            i = 0
            while time.time() < t_end:
                card, plan = plans[i % len(plans)]
                _, t = timed(lambda: c.read(card, None, plan["finish"], plan["punches"]))
                read_lat.append(t)
                i += 1
                time.sleep(max(0, read_every - t))
            stop.set()
            for p in phones:
                p.join(timeout=60)
            lat.sort()
            read_lat.sort()
            lp95 = lat[int(len(lat) * .95)]
            rp95 = read_lat[int(len(read_lat) * .95)]
            print(f"     {len(lat)} phone loads ({len(lat) / 20:.0f}/s), p95 {lp95:.2f}s; "
                  f"{len(read_lat)} reads, p95 {rp95:.2f}s; errors {len(errs)}")
            check(not errs, "no phone got an error", errs[:5])
            check(lp95 < lat_limit, f"phones get results within {lat_limit} s (p95)", f"{lp95:.2f}s")
            check(rp95 < read_limit, f"card reads stay under {read_limit} s (p95)", f"{rp95:.2f}s")

        check.part("live-update streams")
        got = []

        def listen(i):
            try:
                with requests.get(app.public + "/api/stream", stream=True, timeout=15) as r:
                    if r.status_code != 200:
                        got.append(("refused", r.status_code))
                        return
                    for line in r.iter_lines(decode_unicode=True):
                        if line and line.startswith("data:") and "card_read" in line:
                            got.append(("event", i))
                            return
            except Exception as err:  # noqa: BLE001
                got.append(("error", str(err)[:50]))

        listeners = [threading.Thread(target=listen, args=(i,), daemon=True) for i in range(10)]
        for t in listeners:
            t.start()
        time.sleep(2)
        card, plan = plans[0]
        c.read(card, None, plan["finish"], plan["punches"])
        for t in listeners:
            t.join(timeout=20)
        events = [g for g in got if g[0] == "event"]
        print(f"     streams: {len(events)} of 10 saw the read; others: "
              f"{[g for g in got if g[0] != 'event'][:3]}")
        check(len(events) >= 9, "live screens hear about a new read", got)
        c.page("/results")
