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
from results import format_duration, format_split, rank_results

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
        "position": result["position"],
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


def _groups(evaluated: list[dict]) -> list[dict]:
    """``[{key, name, meta, is_score, results}]`` per class, or per course when
    the event lists results by course (every class on a course ranked
    together; a forked runner counts on the course they ran)."""
    if config.get_str("results_group_by") != "course":
        return ordered([{"key": e["class"]["id"], "name": e["class"]["name"],
                         "meta": store.course_meta(e["course"]),
                         "is_score": e["course"]["type"] == "score",
                         "by_course": False, "results": e["results"]}
                        for e in evaluated])
    pooled: dict[int, list] = {}
    for e in evaluated:
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
                       "results": [dict(r, **{"class": r["real_class"]}) for r in ranked]})
    return sorted(groups, key=lambda g: _natural(g["name"]))


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


def results_view(*, on_course: bool | None = None) -> list[dict]:
    """
    The results as the event wants them shown: blocks (a class, or a course)
    in the configured order, each with the listed ``rows`` (placed first, then
    unplaced runners per the 'Unplaced runners' setting), ``behind`` on placed
    rows, and ``on_course``: runners with no card read yet whose start has
    passed (only when the event shows them; ``on_course=False`` forces off,
    e.g. for PDFs).
    """
    evaluated, _ = store.evaluate()
    fmt, now = time_formatter(), store.event_now()
    unplaced = config.get_str("results_unplaced") or "no_dns"
    show_out = config.get("results_show_on_course") if on_course is None else on_course
    show_behind = config.get("results_show_behind")

    blocks = []
    for g in _groups(evaluated):
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
            rows.append(view_row(r, fmt, now))
        if show_behind:
            _behind(rows, g["is_score"], fmt)
        out.sort(key=lambda r: r["start"] or "")
        blocks.append({"id": g["key"], "name": g["name"], "meta": g["meta"],
                       "is_score": g["is_score"], "by_course": g["by_course"],
                       "rows": rows, "on_course": out,
                       "entries": len(g["results"])})
    return blocks


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
