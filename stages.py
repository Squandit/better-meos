"""
Multi-day / multi-stage events.

Each stage is its own ``.bmeos`` file (the file-per-event model). This module
combines several stage files into an overall standing -- summing each
competitor's stage times -- and computes chase (handicap / Gundersson) start
times for a later stage from the deficit built up so far.

It is read-only over the stage files (loaded on throwaway connections via
``db.load_event_file``) and never disturbs the open event, except
:func:`apply_chase_starts`, which writes the computed start times onto the
*open* event's competitors.

Competitors are matched across stages by their own SI card number or by
name + club: either one is enough, so a runner on a hire card one day (or
with their name typed differently) is still one person. Hire card numbers
never link people, since the same hire card goes to someone else next day.
"""

from __future__ import annotations

from datetime import timedelta

import db
import store
from results import format_duration, parse_control_config


def _keys(comp: dict) -> list[tuple]:
    """Everything that identifies a runner: name + club, and their own card."""
    keys = [("name", " ".join((comp.get("name") or "").lower().split()),
             " ".join((comp.get("club") or "").lower().split()))]
    if comp.get("card_number") and not comp.get("hired"):
        keys.append(("card", comp["card_number"]))
    return keys


class _People:
    """Groups the keys that belong to one person (a small union-find)."""

    def __init__(self):
        self.parent: dict[tuple, tuple] = {}

    def find(self, key: tuple) -> tuple:
        self.parent.setdefault(key, key)
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def link(self, keys: list[tuple]) -> None:
        first = self.find(keys[0])
        for key in keys[1:]:
            self.parent[self.find(key)] = first

    def of(self, comp: dict) -> tuple | None:
        """The person a competitor is, if any of their keys has been seen."""
        for key in _keys(comp):
            if key in self.parent:
                return self.find(key)
        return None


def _evaluate_file(path: str) -> dict | None:
    """Load + evaluate one stage file. Returns ``{meta, classes, competitors,
    by_id}`` or None if the file isn't readable."""
    data = db.load_event_file(path)
    if data is None:
        return None
    _, by_id = store._evaluate_model(
        data["courses"], data["classes"], data["competitors"],
        parse_control_config(db.read_event_settings(path).get("control_config")),
        teams=data.get("teams"))
    return {"meta": data["meta"], "classes": data["classes"],
            "competitors": data["competitors"], "by_id": by_id}


def combined_results(paths: list[str]) -> list[dict]:
    """
    Combine the given stage files into per-class overall standings.

    Returns ``[{class, stages:[name, ...],
    rows:[{name, club, stage_seconds:[...], stage_times:[str|''],
    total_seconds, total, complete, position}]}]``. A runner is *complete* (and
    rankable) only with an OK result in every stage; otherwise they are listed
    after the ranked runners with the stages they did finish.
    """
    return _combine(paths)[0]


def _combine(paths: list[str]) -> tuple[list[dict], "_People", dict]:
    """combined_results, plus who is who and each person's row."""
    stages = [s for s in (_evaluate_file(p) for p in paths) if s is not None]
    n = len(stages)
    stage_names = [s["meta"]["name"] for s in stages]
    who = _People()
    for stage in stages:
        for comp in stage["competitors"].values():
            if not comp.get("vacant"):
                who.link(_keys(comp))

    # person -> {name, club, class, times: [seconds|None per stage]}
    people: dict[tuple, dict] = {}
    for i, stage in enumerate(stages):
        for comp in stage["competitors"].values():
            if comp.get("vacant"):
                continue
            key = who.of(comp)
            cls = stage["classes"].get(comp["class_id"])
            person = people.setdefault(key, {
                "name": comp["name"], "club": comp.get("club", ""),
                "class": cls["name"] if cls else "", "times": [None] * n})
            # Prefer a non-empty name/club/class as we see more stages.
            person["name"] = person["name"] or comp["name"]
            person["club"] = person["club"] or comp.get("club", "")
            if not person["class"] and cls:
                person["class"] = cls["name"]
            res = stage["by_id"].get(comp["id"])
            if res and res["status"] == "ok" and res["total_seconds"] is not None:
                person["times"][i] = res["total_seconds"]

    # Group by class, then rank the complete runners by summed time.
    by_class: dict[str, list[dict]] = {}
    row_of: dict[tuple, dict] = {}
    for key, person in people.items():
        complete = n > 0 and all(t is not None for t in person["times"])
        total = sum(person["times"]) if complete else None
        row = {
            "name": person["name"], "club": person["club"],
            "stage_seconds": list(person["times"]),
            "stage_times": [format_duration(t) if t is not None else ""
                            for t in person["times"]],
            "total_seconds": total,
            "total": format_duration(total) if total is not None else "",
            "complete": complete, "position": None, "class": person["class"],
        }
        row_of[key] = row
        by_class.setdefault(person["class"], []).append(row)

    out = []
    for class_name in sorted(by_class):
        rows = by_class[class_name]
        ranked = sorted((r for r in rows if r["complete"]),
                        key=lambda r: r["total_seconds"])
        for idx, r in enumerate(ranked):
            if idx > 0 and r["total_seconds"] == ranked[idx - 1]["total_seconds"]:
                r["position"] = ranked[idx - 1]["position"]
            else:
                r["position"] = idx + 1
        unranked = [r for r in rows if not r["complete"]]
        out.append({"class": class_name, "stages": stage_names,
                    "rows": ranked + unranked})
    return out, who, row_of


def apply_chase_starts(prior_paths: list[str], first_start: str) -> int:
    """
    Set chase (handicap) start times on the OPEN event from earlier stages.

    Every complete runner starts behind the class leader by exactly their
    accumulated deficit: the leader goes at ``first_start`` and a runner who is
    five minutes down starts five minutes later. Runners with no combined time
    (an incomplete prior record) are left untouched for a manual start. Returns
    how many competitors were assigned a start time. Matches the open event's
    competitors to the earlier stages by their own card or by name + club.
    """
    base = store.parse_clock(first_start, "First start")
    if base is None:
        raise store.StoreError("A chase start needs a first-start time")

    # Each complete runner's deficit to their class leader, by person.
    classes, who, row_of = _combine(prior_paths)
    lead = {cls["class"]: next((r["total_seconds"] for r in cls["rows"] if r["position"] == 1),
                               None) for cls in classes}
    deficit: dict[tuple, int] = {}
    for key, row in row_of.items():
        if row["complete"] and lead.get(row["class"]) is not None:
            deficit[key] = row["total_seconds"] - lead[row["class"]]

    assigned = 0
    with store.batch():
        for comp in list(store._competitors.values()):
            key = who.of(comp)
            if key in deficit:
                start = base + timedelta(seconds=deficit[key])
                store.update_competitor(comp["id"], {"start": store.format_clock(start)})
                assigned += 1
        store._audit("chase starts set", f"{assigned} runners",
                     f"first start {first_start}, from {len(prior_paths)} stage(s)")
    return assigned
