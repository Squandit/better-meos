"""
Results as people see them.

The engine (results.py) decides statuses, times and places. This module turns
them into what a page shows, following the event's display settings: the time
format, which runners are listed, the class order, results by class or by
course, and time behind the winner. Every results view builds its rows here
(the console pages, the live screen, the public and published pages, the PDFs),
so one setting changes all of them together.

Nothing here writes; everything reads ``store.evaluate()`` (cached per db
revision) and the settings.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

import config
import store
from results import build_splits_matrix, format_duration, format_split, rank_results

# Result status codes -> short display labels, and the order to list them in.
STATUS_LABELS = {
    "ok": "OK",
    "mp": "MP",
    "dns": "DNS",
    "dnf": "DNF",
    "dsq": "DSQ",
    "oot": "OOT",
    "nc": "NC",
    "pending": "Not read",
}
STATUS_ORDER = ["ok", "nc", "oot", "mp", "dnf", "dns", "dsq", "pending"]
FLAGGED = ("mp", "dnf", "dns", "dsq")

TIME_FORMATS = ("auto", "minutes", "hms")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _minutes(seconds: int) -> str:
    """65:09 style: whole minutes, however long the run."""
    sign = "-" if seconds < 0 else ""
    minutes, secs = divmod(abs(int(seconds)), 60)
    return f"{sign}{minutes}:{secs:02d}"


_FORMATTERS: dict[str, Callable[[int], str]] = {
    "auto": format_split,       # 45:09, 1:05:09
    "minutes": _minutes,        # 65:09
    "hms": format_duration,     # 01:05:09
}


def time_formatter(style: str | None = None) -> Callable[[int | None], str | None]:
    """A seconds -> text function in the event's time format (None stays None)."""
    fn = _FORMATTERS.get(style or config.get_str("time_format"), format_split)
    return lambda seconds: None if seconds is None else fn(seconds)


def clock(dt) -> str | None:
    """Wall-clock time string, or None if the punch is missing."""
    return dt.strftime("%H:%M:%S") if dt else None


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status, status.upper() if status else "")


def pending_label(start, now: datetime) -> str:
    """What a runner with no card read yet is doing."""
    if start is None:
        return "Not read"
    return "On course" if start <= now else "Not started"


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def view_row(result: dict, fmt=None, now: datetime | None = None) -> dict:
    """Shape one engine result into the fields the list/table templates need."""
    fmt = fmt or time_formatter()
    status = result["status"]
    label = status_label(status)
    if status == "pending":
        label = pending_label(result.get("start"), now or store.event_now())
    return {
        "id": result["id"],
        "position": result.get("position"),   # an unsaved preview has no place
        "name": result["name"],
        "club": result["club"],
        "class": result["class"],
        "si": result.get("card_number"),
        "status": status,
        "status_label": label,
        "is_ok": status == "ok",
        "is_pending": status == "pending",
        "manual": result.get("manual", False),
        "time": fmt(result["total_seconds"]),
        "seconds": result["total_seconds"],
        "start": clock(result.get("start")),
        "finish": clock(result.get("finish")),
        "points": result["points"],
        "missed_control": result.get("missed_control"),
        "ignored": result.get("ignored_punches", 0),
        "splits": [{"control": s["control"], "leg": fmt(s["leg_seconds"]),
                    "cumulative": fmt(s["cumulative_seconds"])}
                   for s in result["splits"]],
    }


def result_view(result: dict) -> dict:
    """Compact evaluation summary used by the editor's live preview / detail."""
    row = view_row(result)
    return {
        "status": result["status"],
        "status_label": row["status_label"],
        "auto_status": result.get("auto_status"),
        "auto_status_label": status_label(result.get("auto_status")),
        "manual": result.get("manual", False),
        "is_ok": result["status"] == "ok",
        "course_type": result.get("course_type"),
        "time": row["time"],
        "points": result["points"],
        "missed_control": result.get("missed_control"),
        "position": result.get("position"),
        "splits": row["splits"],
    }


# ---------------------------------------------------------------------------
# Class order
# ---------------------------------------------------------------------------

def _natural(name: str) -> list:
    """Sort key that puts W8 before W10 (numbers compared as numbers)."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name or "")]


def custom_order() -> list[str]:
    """The 'Class order' setting as a list of lower-cased names."""
    raw = config.get_str("class_order")
    return [p.strip().lower() for p in re.split(r"[,\n;]+", raw) if p.strip()]


def order_key(order: list[str] | None = None) -> Callable[[str], tuple]:
    """Sort key for class names: the listed ones first, in the order given,
    then the rest in natural order."""
    order = custom_order() if order is None else order
    rank = {name: i for i, name in enumerate(order)}
    return lambda name: (rank.get((name or "").lower(), len(rank)), _natural(name))


def ordered(items: list, name: Callable = lambda x: x["name"]) -> list:
    key = order_key()
    return sorted(items, key=lambda x: key(name(x)))


# ---------------------------------------------------------------------------
# Whole-event views
# ---------------------------------------------------------------------------

def console_data() -> list[dict]:
    """Every class with all its ranked, display-ready rows (operator views)."""
    classes, _ = store.evaluate()
    fmt, now = time_formatter(), store.event_now()
    view = []
    for entry in classes:
        course = entry["course"]
        view.append({
            "id": entry["class"]["id"],
            "name": entry["class"]["name"],
            "course": course,
            "course_id": course["id"],
            "course_name": course["name"],
            "type": course["type"],
            "is_score": course["type"] == "score",
            "meta": store.course_meta(course),
            "rows": [view_row(r, fmt, now) for r in entry["results"]],
        })
    return ordered(view)


def results_mode(cls: dict) -> str:
    """A class's results option: normal, no_times (names only) or hidden."""
    return cls.get("results_mode") or "normal"


def _class_group(e: dict) -> dict:
    return {"key": e["class"]["id"], "name": e["class"]["name"],
            "meta": store.course_meta(e["course"]),
            "is_score": e["course"]["type"] == "score", "by_course": False,
            "relay": e["class"].get("kind") == "relay",
            "mode": results_mode(e["class"]), "results": e["results"]}


def _groups(evaluated: list[dict], public: bool = True) -> list[dict]:
    """``[{key, name, meta, is_score, mode, results}]`` per class, or per course
    when the event lists results by course (every class on a course ranked
    together; a forked runner counts on the course they ran). Public views
    leave out classes whose results are hidden; names-only classes always
    keep their own block, unranked."""
    if public:
        evaluated = [e for e in evaluated if results_mode(e["class"]) != "hidden"]
    if config.get_str("results_group_by") != "course":
        return ordered([_class_group(e) for e in evaluated])
    own = [_class_group(e) for e in evaluated if results_mode(e["class"]) != "normal"]
    pooled: dict[int, list] = {}
    for e in evaluated:
        if results_mode(e["class"]) != "normal":
            continue
        for r in e["results"]:
            cid = r.get("course_id", e["course"]["id"])
            # rank_results groups by "class": rank on the course instead,
            # keeping the runner's real class to show beside their name.
            pooled.setdefault(cid, []).append(dict(r, real_class=r["class"],
                                                   **{"class": cid}))
    groups = []
    for cid, rows in pooled.items():
        course = store.get_course(cid)
        if course is None:
            continue
        ranked = rank_results(rows).get(cid, [])
        groups.append({"key": cid, "name": course["name"], "meta": store.course_meta(course),
                       "is_score": course["type"] == "score", "by_course": True,
                       "mode": "normal",
                       "results": [dict(r, **{"class": r["real_class"]}) for r in ranked]})
    return sorted(groups, key=lambda g: _natural(g["name"])) + ordered(own)


def _behind(rows: list[dict], is_score: bool, fmt) -> None:
    """Add ``behind`` (time or points behind the winner) to placed rows."""
    placed = [r for r in rows if r["position"] is not None]
    if not placed:
        return
    best = placed[0]
    for r in placed:
        if r is best or r["position"] == 1:
            r["behind"] = ""
        elif is_score:
            gap = (best["points"] or 0) - (r["points"] or 0)
            r["behind"] = f"-{gap} pts" if gap else "+" + (fmt(r["seconds"] - best["seconds"]) or "")
        else:
            r["behind"] = "+" + fmt(r["seconds"] - best["seconds"])


def _names_only(row: dict) -> dict:
    """A row for a class whose results show names only: no time or place."""
    return dict(row, position=None, time=None, seconds=None, points=None, splits=[],
                missed_control=None)


def results_view(*, on_course: bool | None = None, public: bool = True) -> list[dict]:
    """
    The results as the event wants them shown: blocks (a class, or a course)
    in the configured order, each with the listed ``rows`` (placed first, then
    unplaced runners per the 'Unplaced runners' setting), ``behind`` on placed
    rows, and ``on_course``: runners with no card read yet whose start has
    passed (only when the event shows them; ``on_course=False`` forces off,
    e.g. for PDFs). ``public`` leaves out classes whose results are hidden.
    Names-only classes list everyone who ran, alphabetically, without times.
    """
    evaluated, _ = store.evaluate()
    fmt, now = time_formatter(), store.event_now()
    unplaced = config.get_str("results_unplaced") or "no_dns"
    show_out = config.get("results_show_on_course") if on_course is None else on_course
    show_behind = config.get("results_show_behind")

    blocks = []
    for g in _groups(evaluated, public):
        if g["mode"] == "no_times":
            ran = [_names_only(view_row(r, fmt, now)) for r in g["results"]
                   if r["status"] not in ("pending", "dns")]
            ran.sort(key=lambda x: x["name"].lower())
            blocks.append({"id": g["key"], "name": g["name"], "meta": g["meta"],
                           "is_score": False, "by_course": g["by_course"], "rows": ran,
                           "on_course": [], "entries": len(g["results"]),
                           "mode": g["mode"]})
            continue
        rows, out = [], []
        for r in g["results"]:
            if r["status"] == "pending":
                if show_out and r.get("start") is not None and r["start"] <= now:
                    out.append(view_row(r, fmt, now))
                continue
            if unplaced == "placed" and r["position"] is None:
                continue
            if unplaced == "no_dns" and r["status"] == "dns":
                continue
            rows.append(dict(view_row(r, fmt, now), leg=r.get("leg")))
        if show_behind:
            # Relay runners are compared with the same leg of the other teams.
            legs = sorted({r["leg"] or 0 for r in rows}) if g.get("relay") else [None]
            for leg in legs:
                _behind([r for r in rows if leg is None or (r["leg"] or 0) == leg],
                        g["is_score"], fmt)
        out.sort(key=lambda r: r["start"] or "")
        blocks.append({"id": g["key"], "name": g["name"], "meta": g["meta"],
                       "is_score": g["is_score"], "by_course": g["by_course"],
                       "rows": rows, "on_course": out,
                       "teams": _team_rows(g["key"], fmt) if g.get("relay") else None,
                       "entries": len(g["results"]), "mode": g["mode"]})
    return blocks


def _team_rows(class_id: int, fmt) -> list[dict]:
    """A relay class's team standings for the results pages."""
    entry = next((e for e in store.team_results() if e["class"]["id"] == class_id), None)
    if entry is None:
        return []
    best = next((t["total_seconds"] for t in entry["teams"] if t["ok"]), None)
    rows = []
    for t in entry["teams"]:
        rows.append({
            "position": t.get("position"), "name": t["team"]["name"],
            "club": t["team"].get("club") or "",
            "time": fmt(t["total_seconds"]) if t["ok"] else None,
            "seconds": t["total_seconds"] if t["ok"] else None,
            "behind": ("+" + fmt(t["total_seconds"] - best)) if t["ok"] and best is not None
                      and t["total_seconds"] != best else "",
            "status": "ok" if t["ok"] else "",
            "legs": [{"leg": leg["leg"], "name": leg["name"], "place": leg.get("place"),
                      "time": fmt(leg["seconds"]) if leg["seconds"] is not None else None,
                      "seconds": leg["seconds"], "status": leg["status"]}
                     for leg in t["legs"]],
        })
    return rows


def public_evaluated() -> list[dict]:
    """The evaluated classes as the public may see them (published IOF XML):
    hidden classes left out, names-only classes without times or places."""
    evaluated, _ = store.evaluate()
    out = []
    for e in evaluated:
        mode = results_mode(e["class"])
        if mode == "hidden":
            continue
        if mode == "no_times":
            e = dict(e, results=[dict(r, total_seconds=None, position=None, splits=[],
                                      punches=[], points=None) for r in e["results"]])
        out.append(e)
    return out


def _read_at(result: dict):
    comp = store.get_competitor(result["id"])
    return comp.get("read_at") if comp else None


def latest_finishers(limit: int = 10, fresh_seconds: int = 120) -> list[dict]:
    """The most recent card reads with a result, newest first. ``fresh`` marks
    the ones read in the last ``fresh_seconds`` (the live screen lights them up)."""
    evaluated, _ = store.evaluate()
    fmt, now = time_formatter(), store.event_now()
    done = [(r, _read_at(r)) for e in evaluated for r in e["results"]
            if r.get("finish") is not None and r["status"] != "pending"]
    done.sort(key=lambda x: (x[1] or x[0]["finish"]), reverse=True)
    wall = datetime.now()
    out = []
    for r, read_at in done[:limit]:
        row = view_row(r, fmt, now)
        row["fresh"] = read_at is not None and (wall - read_at).total_seconds() < fresh_seconds
        out.append(row)
    return out


def slip_view(comp_id: int) -> dict | None:
    """
    Everything a split slip shows: the runner's row, their place and time
    behind in the class, and for a linear course one line per course control
    (in course order, a missed one marked) with the leg time, the place on that
    leg within the class and the running time.
    """
    classes, by_id = store.evaluate()
    result = by_id.get(comp_id)
    if result is None:
        return None
    fmt = time_formatter()
    row = view_row(result, fmt)
    entry = next((e for e in classes
                  if any(r["id"] == comp_id for r in e["results"])), None)
    view = {"row": row, "place": None, "of": None, "behind": "", "legs": None,
            "footer": config.get_str("slip_footer"),
            "show_place": bool(config.get("slip_show_place")),
            "leg_places": bool(config.get("slip_leg_places"))}
    if entry is None:
        return view
    placed = [r for r in entry["results"] if r["position"] is not None]
    view["of"] = len(placed)
    view["place"] = result["position"]
    if result["position"] and result["position"] > 1 and result["total_seconds"] is not None:
        best = placed[0]
        if entry["course"]["type"] == "score":
            gap = (best["points"] or 0) - (result["points"] or 0)
            view["behind"] = f"-{gap} pts" if gap else "+" + fmt(result["total_seconds"] - best["total_seconds"])
        else:
            view["behind"] = "+" + fmt(result["total_seconds"] - best["total_seconds"])
    course = store.get_course(result.get("course_id")) or entry["course"]
    if course["type"] == "linear" and result.get("start") is not None:
        same = [r for r in entry["results"]
                if r.get("course_id", entry["course"]["id"]) == course["id"]]
        codes, lengths = store.split_controls(course)
        matrix = build_splits_matrix(same, codes, lengths, fmt=lambda x: fmt(x) or "")
        mine = next((r for r in matrix["rows"] if r["id"] == comp_id), None)
        if mine is not None:
            view["legs"] = [{"n": i + 1 if leg["code"] != "F" else "F",
                             "code": leg["code"] if leg["code"] != "F" else "",
                             **cell}
                            for i, (leg, cell) in enumerate(zip(matrix["legs"], mine["cells"]))]
    return view


def _median(values: list[float]) -> float:
    ordered_values = sorted(values)
    mid = len(ordered_values) // 2
    if len(ordered_values) % 2:
        return ordered_values[mid]
    return (ordered_values[mid - 1] + ordered_values[mid]) / 2


def runner_analysis(comp_id: int, *, public: bool = True) -> dict | None:
    """
    A runner's race leg by leg, WinSplits style: each leg's time, place and
    gap to the fastest, and an estimated time loss. The loss compares each leg
    with the fastest runners' time on it (the mean of the best three), scaled
    by this runner's own typical pace (their median ratio to that reference),
    so a steady runner shows their mistakes, not just being slower overall.
    Also the gap to the fastest running time at every control, for the graph.
    None for unknown runners, and for classes whose results the public
    doesn't see.
    """
    classes, by_id = store.evaluate()
    result = by_id.get(comp_id)
    if result is None:
        return None
    entry = next(e for e in classes if any(r["id"] == comp_id for r in e["results"]))
    if public and results_mode(entry["class"]) != "normal":
        return None
    fmt = time_formatter()
    view = {"row": view_row(result, fmt), "class": entry["class"]["name"],
            "legs": [], "lost": None, "graph": None, "place": result["position"],
            "of": sum(1 for r in entry["results"] if r["position"] is not None)}
    course = store.get_course(result.get("course_id")) or entry["course"]
    if course["type"] != "linear" or result.get("start") is None:
        return view
    same = [r for r in entry["results"] if r.get("course_id", entry["course"]["id"]) == course["id"]
            and r.get("start") is not None]
    codes, lengths = store.split_controls(course)
    matrix = build_splits_matrix(same, codes, lengths, fmt=lambda x: fmt(x) or "")
    rows = {r["id"]: r for r in matrix["rows"]}
    mine = rows.get(comp_id)
    if mine is None:
        return view

    # Raw seconds per leg for everyone, to build the reference times.
    from results import aligned_splits
    legs_by_runner = {}
    for r in same:
        aligned = aligned_splits(r["start"], r.get("punches", []), r.get("finish"), codes)
        legs_by_runner[r["id"]] = aligned
    reference = []
    for i in range(len(matrix["legs"])):
        times = sorted(a[i]["leg_seconds"] for a in legs_by_runner.values()
                       if not a[i]["missing"] and a[i]["leg_seconds"] and a[i]["leg_seconds"] > 0)
        reference.append(sum(times[:3]) / len(times[:3]) if times else None)
    my_legs = legs_by_runner[comp_id]
    ratios = [my_legs[i]["leg_seconds"] / reference[i] for i in range(len(reference))
              if reference[i] and not my_legs[i]["missing"] and my_legs[i]["leg_seconds"]]
    pace = _median(ratios) if ratios else None

    total_lost = 0
    for i, (leg, cell) in enumerate(zip(matrix["legs"], mine["cells"])):
        item = {"n": i + 1 if leg["code"] != "F" else "F", "code": leg["code"] if leg["code"] != "F" else "",
                **cell, "lost": None, "lost_text": "", "bar": 0}
        secs = my_legs[i]["leg_seconds"]
        if not cell.get("missing") and pace and reference[i] and secs is not None:
            expected = reference[i] * pace
            lost = max(0, round(secs - expected))
            item["lost"] = lost
            item["lost_text"] = fmt(lost) if lost >= 5 else ""
            item["big"] = lost >= 30 and lost > 0.15 * expected
            total_lost += lost
            item["bar"] = min(100, round(100 * reference[i] / secs)) if secs else 0
        view["legs"].append(item)
    view["lost"] = fmt(total_lost) if pace else None
    view["pace"] = round(100 * pace) if pace else None

    # Gap to the fastest running time at each control (the graph).
    best_cum = []
    for i in range(len(matrix["legs"])):
        cums = [a[i]["cumulative_seconds"] for a in legs_by_runner.values() if not a[i]["missing"]]
        best_cum.append(min(cums) if cums else None)
    points = []
    for i, cell in enumerate(my_legs):
        if not cell["missing"] and best_cum[i] is not None:
            points.append({"n": i + 1, "label": matrix["legs"][i]["label"],
                           "behind": cell["cumulative_seconds"] - best_cum[i]})
    if points:
        view["graph"] = {"points": points, "max": max(p["behind"] for p in points) or 1,
                         "legs": len(matrix["legs"])}
    return view


def public_results_json() -> dict:
    """The public results as JSON, for club websites and scripts."""
    blocks = results_view(on_course=False)
    return {
        "event": {"name": store.EVENT["name"], "date": store.EVENT["date_iso"]},
        "classes": [{
            "name": b["name"], "course": b["meta"], "by_course": b["by_course"],
            "names_only": b["mode"] == "no_times",
            "results": [{"place": r["position"], "name": r["name"], "club": r["club"],
                         "class": r["class"], "status": r["status"],
                         "time": r["time"], "seconds": r["seconds"], "points": r["points"],
                         "behind": r.get("behind") or None} for r in b["rows"]],
            # Relay classes: the team standings, each with its legs.
            **({"teams": [{"place": t["position"], "name": t["name"], "club": t["club"],
                           "time": t["time"], "seconds": t["seconds"],
                           "legs": t["legs"]} for t in b["teams"]]} if b.get("teams") else {}),
        } for b in blocks],
    }
