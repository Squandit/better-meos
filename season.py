"""
Seasons (series, leagues, cups): several events counted together.

An event joins a season through its "Season" setting: every event file in the
events folder with the same season name counts. From those events this builds

* **standings** per class: points per event (from a points table by place, or
  the winner's time divided by yours), only the best N counting, and a minimum
  number of events to be ranked;
* the **prize list** for one event under its prize rules: places per class, at
  most a share of the starters, which classes, and "one prize per season"
  (someone who already won a prize earlier in the season is passed over and the
  prize goes to the next runner).

Runners are matched across events by name, ignoring case and spacing: people
swap cards and clubs get typed differently from one event to the next.

Read-only over the other event files. The open event is read from memory, so
it's always current; other files are cached until they change on disk.
"""

from __future__ import annotations

import math
import os
import re
import threading

import config
import db
import display
import store
from results import parse_control_config

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def person_key(name: str) -> str:
    return " ".join((name or "").lower().split())


def _event_setting(settings: dict, key: str):
    """A setting as that event saw it: its own value, else this computer's."""
    if key in settings:
        return settings[key]
    return config.get(key, include_event=False)


# ---------------------------------------------------------------------------
# Loading events
# ---------------------------------------------------------------------------

def _classes_of(evaluated: list[dict]) -> list[dict]:
    """Only classes with times and places take part in prizes and standings."""
    return [{"name": e["class"]["name"], "is_score": e["course"]["type"] == "score",
             "results": e["results"]} for e in evaluated
            if display.results_mode(e["class"]) == "normal"]


def _open_event() -> dict:
    evaluated, _ = store.evaluate()
    return {"path": store.current_event_path(), "name": store.EVENT["name"],
            "date_iso": store.EVENT["date_iso"], "settings": db.event_settings(),
            "classes": _classes_of(evaluated), "open": True}


def load(path: str) -> dict | None:
    """One event, evaluated: ``{path, name, date_iso, settings, classes}``."""
    if store.has_open_event() and os.path.abspath(path) == os.path.abspath(
            store.current_event_path() or ""):
        return _open_event()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _cache_lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    data = db.load_event_file(path)
    if data is None:
        return None
    settings = db.read_event_settings(path)
    evaluated, _ = store._evaluate_model(
        data["courses"], data["classes"], data["competitors"],
        parse_control_config(settings.get("control_config")))
    event = {"path": path, "name": data["meta"]["name"], "date_iso": data["meta"]["date_iso"],
             "settings": settings, "classes": _classes_of(evaluated), "open": False}
    with _cache_lock:
        _cache[path] = (mtime, event)
    return event


def season_names() -> list[str]:
    """Every season name used by an event in the events folder."""
    names = set()
    for e in store.events_in_folder():
        name = (db.read_event_settings(e["path"]).get("season_name") or "").strip()
        if name:
            names.add(name)
    current = config.get_str("season_name").strip()
    if current:
        names.add(current)
    return sorted(names, key=str.lower)


def season_events(name: str) -> list[dict]:
    """The season's events, oldest first."""
    wanted = (name or "").strip().lower()
    if not wanted:
        return []
    out = []
    for e in store.events_in_folder():
        settings = db.read_event_settings(e["path"])
        if e["open"]:
            own = config.get_str("season_name")      # the open event's live value
        else:
            own = settings.get("season_name") or ""
        if own.strip().lower() == wanted:
            event = load(e["path"])
            if event is not None:
                out.append(event)
    out.sort(key=lambda ev: (ev["date_iso"], ev["name"].lower()))
    return out


# ---------------------------------------------------------------------------
# Prizes
# ---------------------------------------------------------------------------

def _listed(raw: str) -> set[str]:
    return {p.strip().lower() for p in re.split(r"[,\n;]+", raw or "") if p.strip()}


def prize_winners(event: dict, already: dict[str, str] | None = None) -> list[dict]:
    """
    The prize list for one event under its own prize rules.

    ``already`` maps person keys to the event where they won a prize earlier in
    the season; with "one prize per season" on, they're passed over. Returns
    per class ``{class, prizes, winners: [{place, position, name, club, time,
    points}], passed_over: [{name, won_at}]}``. Runners tied on the same place
    share a prize place.
    """
    settings = event["settings"]
    places = max(1, int(_event_setting(settings, "prize_places") or 3))
    share = int(_event_setting(settings, "prize_share") or 0)
    only = _listed(_event_setting(settings, "prize_classes") or "")
    once = bool(_event_setting(settings, "prize_one_per_season"))
    already = already if once and already else {}
    fmt = display.time_formatter()

    out = []
    for cls in display.ordered(event["classes"]):
        if only and cls["name"].lower() not in only:
            continue
        starters = sum(1 for r in cls["results"] if r["status"] not in ("dns", "pending"))
        prizes = places
        if share:
            prizes = min(places, math.ceil(starters * share / 100))
        winners, passed = [], []
        awarded, place, last_position = 0, 0, None
        for r in (r for r in cls["results"] if r["position"] is not None):
            key = person_key(r["name"])
            if key in already:
                passed.append({"name": r["name"], "won_at": already[key]})
                continue
            if r["position"] != last_position:
                place, last_position = awarded + 1, r["position"]
            if place > prizes:
                break
            winners.append({"place": place, "position": r["position"], "name": r["name"],
                            "club": r.get("club") or "", "time": fmt(r["total_seconds"]),
                            "points": r["points"]})
            awarded += 1
        out.append({"class": cls["name"], "is_score": cls["is_score"], "prizes": prizes,
                    "starters": starters, "winners": winners, "passed_over": passed})
    return out


def prizes_for_open_event() -> dict:
    """The open event's prize list, passing over earlier winners this season
    when the event says one prize per season."""
    event = _open_event()
    name = config.get_str("season_name").strip()
    already: dict[str, str] = {}
    earlier = []
    if name and config.get("prize_one_per_season"):
        for ev in season_events(name):
            if ev["open"] or ev["path"] == event["path"]:
                break
            earlier.append(ev["name"])
            # Each earlier event's own winners, under its own rules (and its own
            # one-per-season passing over), so the chain matches what was given.
            for cls in prize_winners(ev, dict(already)):
                for w in cls["winners"]:
                    already.setdefault(person_key(w["name"]), ev["name"])
    return {"classes": prize_winners(event, already), "season": name,
            "earlier": earlier, "one_per_season": bool(config.get("prize_one_per_season"))}


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

def _points_table() -> list[int]:
    table = []
    for part in re.split(r"[,\s;]+", config.get_str("season_points")):
        try:
            table.append(max(0, int(float(part))))
        except ValueError:
            continue
    return table


def _points(r: dict, winner: dict | None, rules: dict) -> int | None:
    """One runner's points at one event (None = no points, not even for turning up)."""
    status = r["status"]
    if status in ("pending", "dns", "dsq", "nc"):
        return None
    if status != "ok" and not (status == "oot" and r["position"] is not None):
        return rules["start_points"] or None
    if rules["scoring"] == "time_ratio":
        if winner is None:
            return rules["finish_points"] or None
        if r["points"] is not None and winner["points"]:          # score course
            return round(rules["max_points"] * r["points"] / winner["points"])
        if r["total_seconds"] and winner["total_seconds"]:
            return round(rules["max_points"] * winner["total_seconds"] / r["total_seconds"])
        return rules["finish_points"] or None
    table = rules["table"]
    if r["position"] is not None and r["position"] <= len(table):
        return table[r["position"] - 1]
    return rules["finish_points"]


def rules() -> dict:
    return {
        "scoring": config.get_str("season_scoring") or "points_table",
        "table": _points_table(),
        "max_points": int(config.get("season_max_points") or 100),
        "finish_points": int(config.get("season_finish_points") or 0),
        "start_points": int(config.get("season_start_points") or 0),
        "best_of": int(config.get("season_best_of") or 0),
        "min_events": int(config.get("season_min_events") or 0),
    }


def standings(name: str) -> dict:
    """
    Season standings per class: ``{events: [name], classes: [{class, rows:
    [{position, name, club, points: [int|None per event], counted: [bool],
    total, events_run}]}]}``. Ranked on the counted points (best N); runners
    short of the minimum number of events are listed after, unranked.
    """
    events = season_events(name)
    r = rules()
    by_class: dict[str, dict] = {}
    for i, ev in enumerate(events):
        for cls in ev["classes"]:
            entry = by_class.setdefault(cls["name"].lower(), {"class": cls["name"], "people": {}})
            placed = [x for x in cls["results"] if x["position"] is not None]
            winner = placed[0] if placed else None
            for res in cls["results"]:
                pts = _points(res, winner, r)
                if pts is None:
                    continue
                person = entry["people"].setdefault(person_key(res["name"]), {
                    "name": res["name"], "club": res.get("club") or "",
                    "points": [None] * len(events)})
                person["club"] = res.get("club") or person["club"]
                previous = person["points"][i]
                person["points"][i] = pts if previous is None else max(previous, pts)

    classes = []
    for entry in display.ordered(list(by_class.values()), name=lambda e: e["class"]):
        rows = []
        for person in entry["people"].values():
            scored = sorted(((p, i) for i, p in enumerate(person["points"]) if p is not None),
                            key=lambda x: -x[0])
            keep = scored[:r["best_of"]] if r["best_of"] else scored
            counted = [False] * len(events)
            for _, i in keep:
                counted[i] = True
            rows.append({"name": person["name"], "club": person["club"],
                         "points": person["points"], "counted": counted,
                         "total": sum(p for p, _ in keep), "events_run": len(scored),
                         "position": None})
        eligible = sorted((x for x in rows if x["events_run"] >= r["min_events"]),
                          key=lambda x: (-x["total"], x["name"].lower()))
        for n, row in enumerate(eligible):
            same = n and row["total"] == eligible[n - 1]["total"]
            row["position"] = eligible[n - 1]["position"] if same else n + 1
        short = sorted((x for x in rows if x["events_run"] < r["min_events"]),
                       key=lambda x: (-x["total"], x["name"].lower()))
        classes.append({"class": entry["class"], "rows": eligible + short})
    return {"name": name, "events": [{"name": ev["name"], "date_iso": ev["date_iso"]}
                                     for ev in events],
            "classes": classes, "rules": r}
