"""
Cross-event season standings + competitor profiles.

This re-implements the multi-event block from ``order.txt`` (event series points
and per-competitor history) in a way that suits the file-per-event model: it
**scans the events folder** and aggregates over the ``.bmeos`` files, rather than
storing series data anywhere. Each file is loaded read-only via
``db.load_event_file`` and evaluated with the shared ``store._evaluate_model``,
so the open event is never disturbed.

Runners are matched across events by SI card, else name+club -- the same identity
key the prize ledger uses (so a profile link from /prizes lines up).

Series points per event default to ``base - (position - 1) * step`` for OK
results (configurable via ``config`` ``series_points_base`` / ``series_points_step``).
"""

from __future__ import annotations

import config
import db
import store
from results import format_duration


def _key(name: str, club: str, card) -> str:
    if card:
        return f"card:{card}"
    return "name:" + (name or "").strip().lower() + "|" + (club or "").strip().lower()


def _scheme() -> tuple[int, int]:
    base = int(config.get("series_points_base") or 100)
    step = int(config.get("series_points_step") or 2)
    return base, step


def _points(position, base: int, step: int) -> int:
    if not position:
        return 0
    return max(0, base - (position - 1) * step)


def _events(folder: str | None):
    """Yield (meta, evaluated-model) for each readable event file in the folder."""
    for e in store.events_in_folder(folder):
        data = db.load_event_file(e["path"])
        if not data:
            continue
        model, _ = store._evaluate_model(
            data["courses"], data["classes"], data["competitors"])
        yield data["meta"], model


def standings(folder: str | None = None) -> list[dict]:
    """
    Season points per class across every event file in the folder.

    Returns ``[{class, people:[{name, club, key, total, rank,
    events:[{event, date, position, points}]}]}]`` sorted by class name, people
    by descending total points.
    """
    base, step = _scheme()
    by_class: dict[str, dict] = {}
    for meta, model in _events(folder):
        for entry in model:
            cls_name = entry["class"]["name"]
            people = by_class.setdefault(cls_name, {})
            for r in entry["results"]:
                pts = _points(r.get("position"), base, step) if r["status"] == "ok" else 0
                key = _key(r["name"], r.get("club"), r.get("card_number"))
                person = people.setdefault(key, {
                    "name": r["name"], "club": r.get("club") or "", "key": key,
                    "total": 0, "events": []})
                person["total"] += pts
                person["events"].append({
                    "event": meta["name"], "date": meta["date_iso"],
                    "position": r.get("position"), "points": pts})

    out = []
    for cls_name in sorted(by_class):
        people = sorted(by_class[cls_name].values(),
                        key=lambda p: (-p["total"], p["name"].lower()))
        for i, p in enumerate(people):
            p["rank"] = i + 1
        out.append({"class": cls_name, "people": people})
    return out


def profile(key: str, folder: str | None = None) -> dict:
    """One person's results across every event file in the folder (newest first)."""
    person = None
    results = []
    for meta, model in _events(folder):
        for entry in model:
            for r in entry["results"]:
                if _key(r["name"], r.get("club"), r.get("card_number")) != key:
                    continue
                if person is None:
                    person = {"name": r["name"], "club": r.get("club") or "", "key": key}
                results.append({
                    "event": meta["name"], "date": meta["date_iso"],
                    "class": entry["class"]["name"], "position": r.get("position"),
                    "status": r["status"],
                    "time": format_duration(r["total_seconds"]) if r["total_seconds"] is not None else "",
                    "points": r.get("points"),
                })
    results.sort(key=lambda x: x["date"], reverse=True)
    return {"person": person, "results": results}
