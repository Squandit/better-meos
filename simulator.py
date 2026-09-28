"""
Download simulator (hardware-free testing).

Each call to :func:`simulate_one` pretends a different person walked up and
downloaded their SI card -- exactly what the brick would feed in, but with no
real data. It picks someone who hasn't downloaded yet: an entered runner with
a card and a start time first (so a drawn start list, like the sample event's,
comes in runner by runner), else someone from a built-in pool (then made-up
extras once the pool has all been read), entered on the day. It generates a
plausible run for their class's course from their start, and pushes it through the same path a real download uses
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

# Fabricated people (no real data), each with their own card number.
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


# Extra made-up people once everyone in POOL has downloaded.
FIRST = ["Maya", "Noah", "Olive", "Piet", "Quinn", "Rosa", "Sami", "Tove", "Uma", "Viggo"]
LAST = ["Aske", "Birk", "Crane", "Dahl", "Eide", "Frost", "Gill", "Holm", "Ivers", "Juhl"]
EXTRA_CARD = 8000100


def _next_entrant() -> dict | None:
    """An entered, individual runner with a card and a start time, not yet read."""
    waiting = [c for c in store._competitors.values()
               if c.get("card_number") and c.get("start") and not c.get("vacant")
               and c.get("read_at") is None
               and (store.get_class(c["class_id"]) or {}).get("kind", "individual") == "individual"]
    if not waiting:
        return None
    comp = random.choice(waiting)
    return {"name": comp["name"], "club": comp.get("club") or "", "card": comp["card_number"]}


def _next_person() -> dict:
    """Someone whose card hasn't been read yet.

    Only people who haven't downloaded are picked. Picking an already-read card
    and inventing a new random run for it would replace their real one (that is
    how a clean run could turn into a mispunch, or the other way round). A real
    re-read of the same card gives the same punches, so it never changes a result.
    """
    def unread(card):
        comp = store.find_by_card(card)
        return comp is None or comp.get("read_at") is None

    fresh = [p for p in POOL if unread(p["card"])]
    if fresh:
        return random.choice(fresh)
    card = EXTRA_CARD
    while not unread(card):
        card += 1
    n = card - EXTRA_CARD
    return {"name": f"{FIRST[n % len(FIRST)]} {LAST[(n // len(FIRST)) % len(LAST)]}",
            "club": POOL[n % len(POOL)]["club"], "card": card}


def _base_start() -> datetime:
    first = store.parse_clock(store.EVENT.get("first_start") or "10:00:00")
    return first or datetime.combine(store.EVENT_DATE, time(10, 0))


def _leg_seconds(course: dict, i: int, pace: float) -> int:
    """One leg: from its length at the runner's pace (s/m) when the course has
    leg lengths, now and then with a mistake; else 1 to 5 minutes."""
    lengths = course.get("leg_lengths") or []
    if i < len(lengths) and lengths[i]:
        mistake = random.uniform(1.5, 3) if random.random() < 0.08 else 1
        return max(8, round(lengths[i] * pace * random.uniform(0.85, 1.2) * mistake))
    return random.randint(60, 300)


def _generate_run(course: dict, start: datetime | None = None) -> tuple:
    """Plausible (start, finish, punches) for one run; mostly clean, some MP."""
    start = start or _base_start() + timedelta(minutes=random.randint(0, 90))
    pace = random.uniform(0.24, 0.45)        # 4:00 to 7:30 per km
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
        missed = (random.randrange(len(codes))
                  if len(codes) > 1 and random.random() < 0.15 else None)
        for i, code in enumerate(codes):
            t += timedelta(seconds=_leg_seconds(course, i, pace))
            if i != missed:
                punches.append((code, t))
    if course["type"] != "score" and course.get("leg_lengths"):
        finish = t + timedelta(seconds=_leg_seconds(course, len(course["controls"]), pace))
    else:
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

        person = _next_entrant() or _next_person()
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
        start, finish, punches = _generate_run(course, (existing or {}).get("start"))
        outcome = si_reader.process_card(
            {"card_number": person["card"], "start": start, "finish": finish,
             "punches": punches}, station_id="finish")

    runners.record(person["card"], person["name"], person["club"], class_name)
    return {"ok": bool(outcome.get("ok")), "name": person["name"],
            "card": person["card"], "class": class_name}
