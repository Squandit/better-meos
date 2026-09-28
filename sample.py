"""
The sample event: a club sprint, ready to run.

Four courses with leg lengths, twelve classes with fees, about 130 entered
runners from six clubs with SI cards, a club-separated draw with a vacant per
class, and bib numbers. Nobody has downloaded yet, so it's a start list
waiting for the finish: Simulate download on the Download page brings the
runners in one at a time. Everyone in it is made up.

Built through the store like an operator would build it, so it's always a
current event file (a shipped file would go stale as the schema moves on).
"""

from __future__ import annotations

import random
from datetime import date

import draw
import store

NAME = "Sample Sprint"
FIRST_START = "10:00:00"

# name, total length (m), number of controls
COURSES = [("A", 3300, 22), ("B", 2700, 18), ("C", 2100, 14), ("D", 1500, 10)]

# name, course, fee, entrants
CLASSES = [
    ("M21E", "A", 18, 18), ("W21E", "B", 18, 14),
    ("M20", "A", 12, 8), ("W20", "B", 12, 6),
    ("M35", "B", 18, 14), ("W35", "C", 18, 12),
    ("M50", "C", 18, 12), ("W50", "C", 18, 10),
    ("M16", "C", 8, 10), ("W16", "D", 8, 8),
    ("12 and under", "D", 5, 10), ("Open", "C", 15, 8),
]

CLUBS = ["LOST", "BO", "WOW", "KO", "SWOT", "NFO"]

MEN = ["Aaron", "Ben", "Callum", "Declan", "Eli", "Felix", "Gus", "Harvey", "Isaac",
       "Jasper", "Kai", "Lachlan", "Max", "Nate", "Oscar", "Patrick", "Reuben", "Sam",
       "Theo", "Will", "Xavier", "Zac"]
WOMEN = ["Abby", "Bella", "Chloe", "Daisy", "Ella", "Freya", "Georgia", "Hannah",
         "Ivy", "Jess", "Kate", "Lucy", "Mia", "Nina", "Olivia", "Poppy", "Ruby",
         "Sophie", "Tess", "Violet", "Willow", "Zoe"]
SURNAMES = ["Abbott", "Barlow", "Carver", "Dunstan", "Ellery", "Fairley", "Garland",
            "Hollis", "Ingram", "Jardine", "Kelso", "Lister", "Marsden", "Norcott",
            "Ormond", "Pryce", "Quayle", "Redfern", "Stirling", "Tamsett", "Upton",
            "Vardy", "Wexley", "Yardley"]

FIRST_CARD = 7100001


def _leg_lengths(rng: random.Random, total: int, legs: int) -> list[int]:
    """Sprint legs, 60 to 350 m, scaled to add up to about ``total``."""
    raw = [rng.randint(60, 350) for _ in range(legs)]
    scale = total / sum(raw)
    return [max(30, round(x * scale / 5) * 5) for x in raw]


def create(folder: str | None = None) -> dict:
    """Create the sample event in the events folder (a new file each time)
    and open it. Returns the event, like :func:`store.new_event`."""
    rng = random.Random(2026)
    event = store.new_event({"name": NAME, "date": date.today().isoformat(),
                             "first_start": FIRST_START, "type": "linear"}, folder=folder)
    codes = list(range(31, 31 + 40))
    with store.batch():
        courses = {}
        for name, length, n in COURSES:
            controls = rng.sample(codes, n)
            courses[name] = store.create_course({
                "name": name, "type": "linear", "controls": controls, "length_m": length,
                "leg_lengths": _leg_lengths(rng, length, n + 1)})["id"]
        card = FIRST_CARD
        class_ids, used = [], set()
        for cls_name, course, fee, entrants in CLASSES:
            cls = store.create_class({"name": cls_name, "course_id": courses[course], "fee": fee})
            class_ids.append(cls["id"])
            mixed = not cls_name.startswith(("M", "W"))
            for _ in range(entrants):
                women = rng.random() < .5 if mixed else cls_name.startswith("W")
                name = next(n for n in iter(lambda: f"{rng.choice(WOMEN if women else MEN)} "
                                                    f"{rng.choice(SURNAMES)}", None)
                            if n not in used)
                used.add(name)
                store.create_competitor({
                    "name": name, "club": rng.choice(CLUBS),
                    "class_id": cls["id"], "card_number": card})
                card += 1
        draw.draw_classes(class_ids, first_start=FIRST_START, interval_seconds=60,
                          method="club", vacants=1, seed=2026)
        store.assign_bibs(101)
    return event
