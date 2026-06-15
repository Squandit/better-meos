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

Competitors are matched across stages by SI card number, falling back to
name+club for hire-card runs that have no number.
"""

from __future__ import annotations

from datetime import timedelta

import db
import store
from results import format_duration


def _identity(name: str, club: str, card) -> tuple:
    """A stable key matching one runner across stages."""
    if card:
        return ("card", card)
    return ("name", (name or "").strip().lower(), (club or "").strip().lower())


def _evaluate_file(path: str) -> dict | None:
    """Load + evaluate one stage file. Returns ``{meta, classes, competitors,
    by_id}`` or None if the file isn't readable."""
    data = db.load_event_file(path)
    if data is None:
        return None
    _, by_id = store._evaluate_model(
        data["courses"], data["classes"], data["competitors"])
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
    stages = [s for s in (_evaluate_file(p) for p in paths) if s is not None]
    n = len(stages)
    stage_names = [s["meta"]["name"] for s in stages]

    # identity -> {name, club, class, times: [seconds|None per stage]}
    people: dict[tuple, dict] = {}
    for i, stage in enumerate(stages):
        for comp in stage["competitors"].values():
            key = _identity(comp["name"], comp.get("club", ""), comp.get("card_number"))
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
    for person in people.values():
        complete = n > 0 and all(t is not None for t in person["times"])
        total = sum(person["times"]) if complete else None
        row = {
            "name": person["name"], "club": person["club"],
            "stage_seconds": list(person["times"]),
            "stage_times": [format_duration(t) if t is not None else ""
                            for t in person["times"]],
            "total_seconds": total,
            "total": format_duration(total) if total is not None else "",
            "complete": complete, "position": None,
        }
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
    return out


def apply_chase_starts(prior_paths: list[str], first_start: str) -> int:
    """
    Set chase (handicap) start times on the OPEN event from earlier stages.

    Every complete runner starts behind the class leader by exactly their
    accumulated deficit: the leader goes at ``first_start`` and a runner who is
    five minutes down starts five minutes later. Runners with no combined time
    (an incomplete prior record) are left untouched for a manual start. Returns
    how many competitors were assigned a start time. Matches the open event's
    competitors to the combined standings by card number, then name+club.
    """
    base = store.parse_clock(first_start, "First start")
    if base is None:
        raise store.StoreError("A chase start needs a first-start time")

    # leader total per class, and each runner's deficit, keyed by identity.
    deficit: dict[tuple, int] = {}
    for cls in combined_results(prior_paths):
        leader = next((r for r in cls["rows"] if r["position"] == 1), None)
        if leader is None:
            continue
        lead_total = leader["total_seconds"]
        for r in cls["rows"]:
            if r["complete"]:
                key = _identity(r["name"], r["club"], None)
                deficit[key] = r["total_seconds"] - lead_total

    # The combined rows don't carry the card number, so also key by card via a
    # second pass over the prior stages -- a card maps to its name/club identity.
    card_to_name: dict = {}
    for stage in (s for s in (_evaluate_file(p) for p in prior_paths) if s):
        for comp in stage["competitors"].values():
            if comp.get("card_number"):
                card_to_name[comp["card_number"]] = _identity(
                    comp["name"], comp.get("club", ""), None)

    assigned = 0
    with store._lock:
        for comp in list(store._competitors.values()):
            key = card_to_name.get(comp.get("card_number"))
            if key is None:
                key = _identity(comp["name"], comp.get("club", ""), None)
            if key in deficit:
                comp["start"] = base + timedelta(seconds=deficit[key])
                db.save_competitor(store._active_event_id, comp)
                assigned += 1
    return assigned
