"""
Download simulator (hardware-free testing).

Each call to :func:`simulate_one` pretends a different person walked up and
downloaded their SI card -- exactly what the brick would feed in, but with no
real data. It picks a random person from a built-in pool, makes sure they're
entered (creating an on-the-day entry if needed), generates a plausible run for
their class's course, and pushes it through the same path a real download uses
(``si_reader.process_card`` -> ``store.apply_card_read``), so results, the "still
out" count, the live screen and the runner database all update for real.

It only needs the operator to have created at least one class first (it never
injects demo data into a real event); then each click conjures a download.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

import runners
import si_reader
import store

# Fabricated people (no real data). Distinct card numbers so repeats land on the
# same competitor (a re-download), and different people arrive in any order.
POOL = [
    {"name": "Ada Lovelace", "club": "LOST", "card": 8000001},
    {"name": "Boris Bracken", "club": "BO", "card": 8000002},
    {"name": "Cleo Marsh", "club": "WOW", "card": 8000003},
    {"name": "Dane Forsythe", "club": "KO", "card": 8000004},
    {"name": "Esme Quill", "club": "SWOT", "card": 8000005},
    {"name": "Finn Halloran", "club": "LOST", "card": 8000006},
    {"name": "Greta Voss", "club": "BO", "card": 8000007},
    {"name": "Hugo Penrose", "club": "WOW", "card": 8000008},
    {"name": "Isla Reyes", "club": "KO", "card": 8000009},
    {"name": "Jonah Webb", "club": "SWOT", "card": 8000010},
    {"name": "Kira Nash", "club": "LOST", "card": 8000011},
    {"name": "Liam Ortega", "club": "BO", "card": 8000012},
]


def _base_start() -> datetime:
    first = store.parse_clock(store.EVENT.get("first_start") or "10:00:00")
    return first or datetime.combine(store.EVENT_DATE, time(10, 0))


def _generate_run(course: dict) -> tuple:
    """Plausible (start, finish, punches) for one run; mostly clean, some MP."""
    start = _base_start() + timedelta(minutes=random.randint(0, 90))
    t = start
    punches = []
    if course["type"] == "score":
        codes = [c["code"] for c in course["controls"]]
        random.shuffle(codes)
        for code in codes[: max(1, len(codes) - random.randint(0, 1))]:
            t += timedelta(seconds=random.randint(60, 300))
            punches.append((code, t))
    else:
        codes = list(course["controls"])
        # ~1 in 7 runs misses a control (a mispunch) for variety.
        if len(codes) > 1 and random.random() < 0.15:
            codes.pop(random.randrange(len(codes)))
        for code in codes:
            t += timedelta(seconds=random.randint(60, 300))
            punches.append((code, t))
    finish = t + timedelta(seconds=random.randint(60, 200))
    return start, finish, punches


def simulate_one() -> dict:
    """Simulate one card download. Returns ``{ok, name, card, class}``."""
    with store._lock:
        class_opts = store.class_options()
        if not class_opts:
            # Never inject demo data into a real event -- ask for a class first.
            return {"ok": False,
                    "error": "Add at least one class before simulating downloads."}

        person = random.choice(POOL)
        existing = store.find_by_card(person["card"])
        if existing is None:
            cls = random.choice(class_opts)
            created = store.create_competitor({
                "name": person["name"], "club": person["club"],
                "class_id": cls["id"], "card_number": person["card"],
            })
            class_id, class_name = cls["id"], created["class_name"]
        else:
            class_id = existing["class_id"]
            class_name = store.get_class(class_id)["name"]

        course = store.get_course(store.get_class(class_id)["course_id"])
        start, finish, punches = _generate_run(course)
        outcome = si_reader.process_card(
            {"card_number": person["card"], "start": start, "finish": finish,
             "punches": punches}, station_id="finish")

    runners.record(person["card"], person["name"], person["club"], class_name)
    return {"ok": bool(outcome.get("ok")), "name": person["name"],
            "card": person["card"], "class": class_name}
