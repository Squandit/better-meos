"""
Start-list draw: give each class's runners start times.

What MeOS-style draws need, and what this does:

* **Random order** (``method="random"``), or **random with club separation**
  (``"club"``, the default): runners from the same club are kept apart
  wherever the numbers allow, so clubmates don't start back to back and follow
  each other. ``"alpha"`` keeps the old alphabetical order.
* **Vacant slots**: ``vacants`` empty start times per class, spread through the
  field, for late entries on the day (they show as "Vacant" on the start list
  and are filled by editing them; see store.VACANT_NAME).
* **Classes sharing a course never start together**: classes on the same
  course are interleaved (class A at :00, class B at :01, A at :02, ...), so
  two runners on one course are never sent out on the same minute.

Pure ordering helpers are separate from :func:`draw_classes`, which writes the
start times through the store in one batch.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta

import store
from store import StoreError

METHODS = ("club", "random", "alpha")


def club_separated(runners: list[dict], rng: random.Random) -> list[dict]:
    """
    A random order with clubmates kept apart as far as possible.

    Shuffle each club, then repeatedly take the next runner from the club with
    the most runners left that isn't the club just placed (random tie-break).
    Picking the largest club first is what keeps the order feasible to the end:
    if one club is more than half the field, adjacency is unavoidable and the
    leftovers go at the back.
    """
    by_club: dict[str, list[dict]] = defaultdict(list)
    for r in runners:
        by_club[(r.get("club") or "").strip().lower()].append(r)
    for members in by_club.values():
        rng.shuffle(members)
    order: list[dict] = []
    last = None
    while by_club:
        choices = [c for c in by_club if c != last or len(by_club) == 1]
        biggest = max(len(by_club[c]) for c in choices)
        club = rng.choice([c for c in choices if len(by_club[c]) == biggest])
        order.append(by_club[club].pop())
        if not by_club[club]:
            del by_club[club]
        last = club
    return order


def ordered(runners: list[dict], method: str, rng: random.Random) -> list[dict]:
    if method == "alpha":
        return sorted(runners, key=lambda r: r["name"].lower())
    if method == "random":
        out = list(runners)
        rng.shuffle(out)
        return out
    return club_separated(runners, rng)


def with_vacants(order: list, vacants: int, rng: random.Random) -> list:
    """Spread ``vacants`` None placeholders through the order at random."""
    out = list(order)
    for _ in range(max(0, vacants)):
        out.insert(rng.randint(0, len(out)), None)
    return out


def draw_classes(class_ids: list[int], *, first_start: str, interval_seconds: int,
                 method: str = "club", vacants: int = 0, keep_existing: bool = False,
                 stagger_shared_courses: bool = True, seed: int | None = None) -> dict:
    """
    Draw start times for the given classes.

    ``keep_existing`` draws only runners without a start time and places them
    after the class's last existing start. Otherwise the class is redrawn from
    scratch (old vacant slots are removed first). Returns a per-class summary.
    """
    if method not in METHODS:
        raise StoreError(f"Draw method must be one of {', '.join(METHODS)}")
    first = store.parse_clock(first_start, "First start")
    if first is None:
        raise StoreError("First start time is required")
    if interval_seconds is None or int(interval_seconds) < 1:
        raise StoreError("Interval must be at least 1 second")
    step = timedelta(seconds=int(interval_seconds))
    vacants = max(0, int(vacants or 0))
    rng = random.Random(seed)

    with store.batch():
        classes, teams = [], []
        for cid in class_ids:
            cls = store.get_class(cid)
            if cls is None:
                raise StoreError("One of the chosen classes no longer exists")
            # Relays and patrols start as teams (team start, mass start, the
            # changeover): a drawn time per runner would override all of that.
            (teams if cls.get("kind") in ("relay", "patrol") else classes).append(cls)

        # Check before changing anything: a draw that stopped halfway would
        # leave a start list nobody chose.
        done = []
        for cls in classes:
            for comp in store._competitors_in_class(cls["id"]):
                redrawn = comp["start"] is None if keep_existing else not comp.get("vacant")
                if redrawn and (comp.get("finish") is not None or comp.get("read_at") is not None):
                    done.append(cls["name"])
                    break
        if done:
            raise StoreError(
                f"{', '.join(done)} already {'has' if len(done) == 1 else 'have'} runners who've "
                "finished: redrawing would change their start times. Leave "
                f"{'it' if len(done) == 1 else 'them'} out, or tick 'Only runners with no start "
                "time' to draw late entries.")

        # Classes sharing a course take turns: each gets a slot offset within
        # a combined cycle, and its own runners go out once per cycle.
        lanes: dict[int, list[dict]] = defaultdict(list)
        for cls in sorted(classes, key=lambda c: c["name"].lower()):
            lanes[cls["course_id"] if stagger_shared_courses else -cls["id"]].append(cls)

        summary = []
        for lane in lanes.values():
            cycle = step * len(lane)
            for offset, cls in enumerate(lane):
                summary.append(_draw_one(cls, first + step * offset, cycle, method,
                                         vacants, keep_existing, rng))
        store._audit("start list drawn", ", ".join(s["class"] for s in summary),
                     f"{method}, first {first_start}, every {interval_seconds}s"
                     + (f", {vacants} vacant per class" if vacants else "")
                     + (", late entries only" if keep_existing else ""))
    summary += [{"class": cls["name"], "drawn": 0, "vacants": 0, "first": "", "last": "",
                 "skipped": "teams start together"} for cls in teams]
    return {"classes": sorted(summary, key=lambda s: s["class"].lower()),
            "clashes": course_clashes()}


def course_clashes() -> list[dict]:
    """
    Start times where runners of *different* classes on the same course go at
    the same moment, which happens when classes sharing a course are drawn
    separately. ``[{course, classes, count, first}]``, one per course and
    group of classes. (Runners of one class never clash: the draw spaces them.)
    """
    slots: dict = defaultdict(list)
    for comp in store._competitors.values():
        cls = store._classes.get(comp["class_id"])
        if comp["start"] is None or cls is None:
            continue
        course = comp.get("course_id") if comp.get("course_id") in store._courses \
            else cls["course_id"]
        slots[(course, comp["start"])].append(cls["name"])
    groups: dict = {}
    for (course, when), names in slots.items():
        classes = tuple(sorted(set(names), key=str.lower))
        if len(classes) < 2:
            continue
        g = groups.setdefault((course, classes), {"count": 0, "first": when})
        g["count"] += 1
        g["first"] = min(g["first"], when)
    return [{"course": store._courses[course]["name"], "classes": list(classes),
             "count": g["count"], "first": store.format_clock(g["first"])}
            for (course, classes), g in sorted(groups.items(),
                                               key=lambda kv: kv[1]["first"])]


def _draw_one(cls: dict, first: datetime, step: timedelta, method: str,
              vacants: int, keep_existing: bool, rng: random.Random) -> dict:
    members = store._competitors_in_class(cls["id"])
    if not keep_existing:
        for comp in [c for c in members if c.get("vacant")]:
            store.delete_competitor(comp["id"])
        members = [c for c in members if not c.get("vacant")]
        to_draw, start = members, first
    else:
        to_draw = [c for c in members if c["start"] is None and not c.get("vacant")]
        taken = [c["start"] for c in members if c["start"] is not None]
        start = max(first, max(taken) + step) if taken else first

    order = with_vacants(ordered(to_draw, method, rng), vacants, rng)
    first_used = start
    placed = 0
    for slot in order:
        when = store.format_clock(start)
        if slot is None:
            store._insert_competitor(
                name=store.VACANT_NAME, club="", class_id=cls["id"], card_number=None,
                start=start, finish=None, punches=[], manual_status="", vacant=True)
        else:
            store.update_competitor(slot["id"], {"start": when})
            placed += 1
        start += step
    return {"class": cls["name"], "drawn": placed,
            "vacants": sum(1 for s in order if s is None),
            "first": store.format_clock(first_used) if order else "",
            "last": store.format_clock(start - step) if order else ""}
