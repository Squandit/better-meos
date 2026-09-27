"""
The customisable home screen (Overview): a grid of widgets.

Each widget is a :class:`Widget` (id, title, what it's for, default size) with
a ``data(ctx)`` function; its template is ``templates/widgets/<id>.html``. The
layout, which widgets are shown, in what order and at what size, is a
per-computer setting (``dashboard_layout``), so the finish desk and the
secretariat can each have their own. Only widgets on screen are computed.

``ctx`` is built once per page view by app.py: the evaluated classes and the
display rows (so widgets share one evaluation).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import backups
import config
import display
import online_entry
import publish
import si_reader
import store
from results import aligned_splits

SIZES = ("small", "wide", "full")


@dataclass(frozen=True)
class Widget:
    id: str
    title: str
    about: str
    size: str
    data: Callable[[dict], dict]


def _now() -> datetime:
    return store.event_now()


# ---------------------------------------------------------------------------
# Widget data
# ---------------------------------------------------------------------------

def _stats(ctx) -> dict:
    rows = ctx["rows"]
    started = [r for r in rows if r["start"]]
    finished = [r for r in rows if r["finish"]]
    return {"competitors": len(rows), "classes": len(ctx["classes"]),
            "started": len(started), "finished": len(finished),
            "out": sum(1 for r in started if not r["finish"]),
            "flagged": sum(1 for r in rows if r["status"] in ("mp", "dnf", "dsq")),
            "unmatched": len(store.unmatched_reads())}


def _readout(ctx) -> dict:
    return {"reads": si_reader.recent_reads()[:10], "reader_enabled": si_reader.reader_enabled()}


def overdue_minutes(course: dict) -> int | None:
    """When a runner still out counts as overdue: the course's max time, else
    the 'Overdue after' setting (0 = never)."""
    if course.get("type") == "linear" and course.get("time_limit_minutes"):
        return course["time_limit_minutes"]
    if course.get("type") == "score" and course.get("time_limit_minutes"):
        return course["time_limit_minutes"] + 15      # score: limit + a grace period
    minutes = config.get("overdue_after_minutes") or 0
    return minutes or None


def _out_on_course(ctx) -> dict:
    now = _now()
    rows = []
    for entry in ctx["evaluated"]:
        limit = overdue_minutes(entry["course"])
        for r in entry["results"]:
            if r.get("start") is None or r.get("finish") is not None:
                continue
            if r["start"] > now:
                continue                                   # not started yet
            running = int((now - r["start"]).total_seconds())
            rows.append({"id": r["id"], "name": r["name"], "class": entry["class"]["name"],
                         "start": r["start"].strftime("%H:%M:%S"), "running": running,
                         "overdue": limit is not None and running > limit * 60})
    rows.sort(key=lambda x: (not x["overdue"], -x["running"]))
    return {"rows": rows[:15], "total": len(rows),
            "overdue": sum(1 for r in rows if r["overdue"])}


def _alerts(ctx) -> dict:
    """Everything that needs a human, each with where to fix it."""
    items = []
    unmatched = len(store.unmatched_reads())
    if unmatched:
        items.append(("bad", f"{unmatched} unmatched card read{'s' if unmatched != 1 else ''}",
                      "/download", "Assign them"))
    out = _out_on_course(ctx)
    if out["overdue"]:
        items.append(("bad", f"{out['overdue']} runner{'s' if out['overdue'] != 1 else ''} overdue on course",
                      "/speaker", "See who"))
    attention = [o for o in online_entry.list_orders() if o["needs_attention"]]
    if attention:
        items.append(("bad", f"{len(attention)} online payment{'s' if len(attention) != 1 else ''} need attention",
                      "/entries", "Review"))
    for rd in si_reader.reader_status():
        if rd["state"] != "running" and si_reader.reader_enabled():
            items.append(("bad", f"SI reader {rd['station']} ({rd['port']}) is {rd['state']}"
                          + (f": {rd['error']}" if rd["error"] else ""), "/download", "Check"))
    if si_reader.reader_enabled() and not si_reader.reader_status():
        items.append(("bad", "SI reader is on but not started", "/config", "Settings"))
    b = backups.status()
    if b["error"]:
        items.append(("bad", f"Backup failing: {b['error']}", "/setup", "Fix"))
    p = publish.status()
    if p["error"]:
        items.append(("warn", f"Online results failing: {p['error']}", "/setup", "Fix"))
    hire = [h for h in store.economy_summary()["outstanding"] if h["finished"]]
    if hire:
        items.append(("warn", f"{len(hire)} hire card{'s' if len(hire) != 1 else ''} not returned after finishing",
                      "/economy", "Chase up"))
    undrawn = [c for c in store._classes.values()
               if any(m["start"] is None and not m.get("vacant")
                      for m in store._competitors_in_class(c["id"]))
               and store._courses.get(c["course_id"], {}).get("start_mode", "clock") == "clock"]
    if undrawn:
        items.append(("warn", f"{len(undrawn)} class{'es' if len(undrawn) != 1 else ''} with runners but no start time",
                      "/draw", "Draw"))
    no_card = sum(1 for c in store._competitors.values()
                  if not c.get("card_number") and not c.get("vacant") and not c.get("finish"))
    if no_card:
        items.append(("warn", f"{no_card} runner{'s' if no_card != 1 else ''} without an SI card number",
                      "/competitors", "Fill in"))
    return {"items": [{"level": l, "text": t, "href": h, "action": a} for l, t, h, a in items]}


def _class_progress(ctx) -> dict:
    rows = []
    for c in ctx["classes"]:
        total = len(c["rows"])
        started = sum(1 for r in c["rows"] if r["start"])
        finished = sum(1 for r in c["rows"] if r["finish"])
        ok = sum(1 for r in c["rows"] if r["status"] == "ok")
        rows.append({"name": c["name"], "total": total, "started": started,
                     "finished": finished, "ok": ok,
                     "pct": round(100 * finished / total) if total else 0})
    return {"rows": rows}


def _leaders(ctx) -> dict:
    out = []
    for c in ctx["classes"]:
        top = next((r for r in c["rows"] if r["position"] == 1), None)
        if top:
            out.append({"class": c["name"], "is_score": c["is_score"], "row": top})
    return {"leaders": out}


def _latest(ctx) -> dict:
    finished = [r for r in ctx["rows"] if r["finish"]]
    finished.sort(key=lambda r: r["finish"], reverse=True)
    return {"rows": finished[:8]}


def _status_breakdown(ctx) -> dict:
    counts = Counter(r["status"] for r in ctx["rows"])
    order = ("ok", "nc", "oot", "mp", "dnf", "dns", "dsq", "pending")
    return {"items": [{"status": s, "label": display.STATUS_LABELS[s], "count": counts[s]}
                      for s in order if counts.get(s)]}


def _next_starters(ctx) -> dict:
    now = _now()
    upcoming = sorted((c for c in store._competitors.values()
                       if c["start"] is not None and c["start"] >= now),
                      key=lambda c: c["start"])
    if not upcoming:
        return {"minute": None, "rows": [], "later": 0}
    first = upcoming[0]["start"]
    soon = [c for c in upcoming if c["start"] < first + timedelta(minutes=3)]
    return {"minute": first.strftime("%H:%M:%S"),
            "seconds": int((first - now).total_seconds()),
            "rows": [{"name": store.VACANT_NAME if c.get("vacant") else c["name"],
                      "class": store._classes[c["class_id"]]["name"],
                      "start": c["start"].strftime("%H:%M:%S"),
                      "vacant": bool(c.get("vacant"))} for c in soon[:10]],
            "later": len(upcoming) - min(len(soon), 10)}


def _quick_actions(ctx) -> dict:
    return {"publish_enabled": publish.enabled()}


def _notes(ctx) -> dict:
    return {"text": config.get_str("dashboard_notes")}


def _system(ctx) -> dict:
    import remote
    return {"readers": si_reader.reader_status(), "reader_enabled": si_reader.reader_enabled(),
            "backup": backups.status(), "publish": publish.status(), "remote": remote.url()}


def _changes(ctx) -> dict:
    return {"entries": store.audit_log(8)}


def mp_hotspots(evaluated: list[dict], limit: int = 8) -> list[dict]:
    """Controls most often missed by mispunched runners: many misses at one
    control usually means a missing, moved or broken unit."""
    missed: Counter = Counter()
    tried: Counter = Counter()
    for entry in evaluated:
        course = entry["course"]
        if course["type"] != "linear":
            continue
        codes = list(course["controls"])
        for r in entry["results"]:
            if r.get("start") is None or r.get("finish") is None:
                continue
            aligned = aligned_splits(r["start"], r.get("punches", []), r.get("finish"), codes)
            for cell in aligned[:-1]:
                tried[cell["control"]] += 1
                if cell["missing"]:
                    missed[cell["control"]] += 1
    rows = [{"code": code, "missed": n, "of": tried[code],
             "pct": round(100 * n / tried[code]) if tried[code] else 0}
            for code, n in missed.most_common(limit)]
    return rows


def _mp_hotspots(ctx) -> dict:
    return {"rows": mp_hotspots(ctx["evaluated"])}


def _finish_rate(ctx) -> dict:
    """Finishes per 10 minutes, for a small bar chart."""
    times = sorted(datetime.strptime(r["finish"], "%H:%M:%S") for r in ctx["rows"] if r["finish"])
    if not times:
        return {"bars": [], "peak": 0}
    start = times[0].replace(minute=times[0].minute - times[0].minute % 10, second=0)
    buckets: Counter = Counter()
    for t in times:
        buckets[int((t - start).total_seconds() // 600)] += 1
    n = max(buckets) + 1
    bars = [{"label": (start + timedelta(minutes=10 * i)).strftime("%H:%M"),
             "count": buckets.get(i, 0)} for i in range(n)][-18:]
    return {"bars": bars, "peak": max(b["count"] for b in bars)}


def _money(ctx) -> dict:
    orders = online_entry.list_orders()
    paid = [o for o in orders if o["status"] in ("completed", "needs_refund", "paid")]
    econ = store.economy_summary()
    return {"online_orders": len(paid),
            "online_total": sum(float(o["amount"]) for o in paid),
            "currency": config.get_str("currency"),
            "fees": econ["total_fees"], "hire_out": len(econ["outstanding"])}


WIDGETS: list[Widget] = [
    Widget("alerts", "Needs attention", "Unmatched cards, overdue runners, failing backups, "
           "payments to check, undrawn classes, each with a link to fix it.", "wide", _alerts),
    Widget("stats", "Key numbers", "Competitors, started, finished, out on course, flagged.",
           "full", _stats),
    Widget("readout", "Card readout", "Recent downloads and a simulate button.", "wide", _readout),
    Widget("out_on_course", "Out on course", "Who's still out, longest first; overdue "
           "runners (past the course's max time) on top.", "small", _out_on_course),
    Widget("class_progress", "Class progress", "Finished / started / entered per class.",
           "wide", _class_progress),
    Widget("next_starters", "Next starters", "The next start minute and who's in it.",
           "small", _next_starters),
    Widget("leaders", "Class leaders", "Current leader of each class.", "wide", _leaders),
    Widget("latest", "Latest finishes", "The most recent downloads with times.", "wide", _latest),
    Widget("status_breakdown", "Status breakdown", "How many OK, MP, DNF, DNS...", "small",
           _status_breakdown),
    Widget("mp_hotspots", "Controls most missed", "Controls mispunchers missed most: a "
           "cluster usually means a missing or broken unit.", "small", _mp_hotspots),
    Widget("finish_rate", "Finishes over time", "Downloads per 10 minutes.", "small",
           _finish_rate),
    Widget("quick_actions", "Quick actions", "One-click buttons for common jobs.", "small",
           _quick_actions),
    Widget("system", "System status", "SI readers, backups, online results, remote entry.",
           "small", _system),
    Widget("money", "Money", "Entry fees and online payments so far.", "small", _money),
    Widget("changes", "Recent changes", "The last few entries in the change log.", "small",
           _changes),
    Widget("notes", "Notes", "A notepad kept with this event (handover notes, radio "
           "channels, who's on which desk).", "small", _notes),
]
BY_ID = {w.id: w for w in WIDGETS}

DEFAULT_LAYOUT = [
    {"id": "alerts", "size": "wide"},
    {"id": "quick_actions", "size": "small"},
    {"id": "stats", "size": "full"},
    {"id": "readout", "size": "wide"},
    {"id": "out_on_course", "size": "small"},
    {"id": "class_progress", "size": "wide"},
    {"id": "next_starters", "size": "small"},
    {"id": "latest", "size": "wide"},
    {"id": "status_breakdown", "size": "small"},
    {"id": "leaders", "size": "full"},
]


def clean_layout(raw) -> list[dict]:
    """A layout from storage/the browser, minus unknown widgets and repeats."""
    out, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        wid = item.get("id")
        if wid not in BY_ID or wid in seen:
            continue
        size = item.get("size") if item.get("size") in SIZES else BY_ID[wid].size
        out.append({"id": wid, "size": size})
        seen.add(wid)
    return out


def layout() -> list[dict]:
    stored = config.get("dashboard_layout")
    cleaned = clean_layout(stored)
    return cleaned if stored not in (None, "") else [dict(x) for x in DEFAULT_LAYOUT]


def save_layout(raw) -> list[dict]:
    cleaned = clean_layout(raw)
    config.save({"dashboard_layout": cleaned}, target="computer")
    return cleaned


def reset_layout() -> None:
    config.save({}, target="computer", reset=["dashboard_layout"])


def build(ctx: dict) -> list[dict]:
    """The shown widgets with their data, in layout order."""
    out = []
    for item in layout():
        widget = BY_ID[item["id"]]
        out.append({"id": widget.id, "title": widget.title, "size": item["size"],
                    "data": widget.data(ctx)})
    return out


def catalogue() -> list[dict]:
    shown = {item["id"] for item in layout()}
    return [{"id": w.id, "title": w.title, "about": w.about, "size": w.size,
             "shown": w.id in shown} for w in WIDGETS]
