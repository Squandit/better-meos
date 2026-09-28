"""
What a run's result should be, worked out independently of the app's engine
(results.py), straight from the competition rules. The stress scenarios plan
runs, work out the answer here, and compare it with what the app publishes.

Rules (as the app promises them, see HANDOFF.md):
* A replacement unit's code counts as the control it stands for.
* Punch start: the start is the start-control punch. Mass start: everyone who
  ran starts at the gun.
* A time more than 12 h before the start is after midnight.
* A check punch after the start is ignored; otherwise punches before the check
  are from an earlier run. Punches outside start..finish don't count.
* No card read and no finish: pending. No start: DNS. No finish: DNF.
* Linear: every required control in order (extra punches are fine) or MP at
  the first one missed. Legs to a no-timing control come off the time. Over
  the max time: OOT.
* Score: each control's points once; any part of a minute over the limit is a
  whole minute of penalty; over the limit is OOT but still placed.
* A manual status replaces the verdict, keeping the time.
"""

from __future__ import annotations

from datetime import datetime, timedelta

WRAP = timedelta(hours=12)


def _wrap(start, t):
    if start is not None and t is not None and (start - t) > WRAP:
        return t + timedelta(days=1)
    return t


def course_rules(controls, statuses=None):
    """``statuses``: {code: "bad"|"optional"|"no_timing"}."""
    statuses = statuses or {}
    return {
        "required": [c for c in controls if statuses.get(c) not in ("bad", "optional")],
        "timed": [c for c in controls if statuses.get(c) != "bad"],
        "no_timing": {c for c in controls if statuses.get(c) == "no_timing"},
    }


def result(run: dict, course: dict, *, statuses=None, alternates=None) -> dict:
    """
    ``run``: start, finish, punches [(code, dt)], check, read (bool), manual.
    ``course``: type linear|score, controls (list, or {code: points}),
    time_limit_minutes, penalty_per_minute, start_mode, start_control,
    mass_start.
    Returns {status, seconds, points, missed}.
    """
    start, finish = run.get("start"), run.get("finish")
    punches = list(run.get("punches") or [])
    alternates = alternates or {}
    punches = [(alternates.get(c, c), t) for c, t in punches]

    mode = course.get("start_mode") or "clock"
    if mode == "punch":
        sc = course.get("start_control")
        for i, (c, t) in enumerate(punches):
            if sc is None or c == sc:
                start = t
                del punches[i]
                break
    elif mode == "mass" and course.get("mass_start") is not None and (finish or punches):
        start = course["mass_start"]

    if start is not None:
        finish = _wrap(start, finish)
        punches = [(c, _wrap(start, t)) for c, t in punches]
    check = run.get("check")
    if start is not None:
        check = _wrap(start, check)
    ref = start if start is not None else finish
    if check is not None and ref is not None and check > ref:
        check = None
    if check is not None:
        punches = [(c, t) for c, t in punches if t >= check]
    if start is not None:
        punches = [(c, t) for c, t in punches if start <= t and (finish is None or t <= finish)]

    out = {"status": None, "seconds": None, "points": None, "missed": None}
    if finish is None and not run.get("read", True):
        out["status"] = "pending"
    elif start is None:
        out["status"] = "dns"
    elif finish is None:
        out["status"] = "dnf"
    else:
        secs = int((finish - start).total_seconds())
        if course["type"] == "linear":
            rules = course_rules(course["controls"], statuses)
            codes = [c for c, _ in punches]
            i = 0
            for c in codes:
                if i < len(rules["required"]) and c == rules["required"][i]:
                    i += 1
            if i < len(rules["required"]):
                out["status"], out["missed"] = "mp", rules["required"][i]
            else:
                out["status"] = "ok"
            if rules["no_timing"]:
                secs -= _untimed(start, punches, rules["timed"], rules["no_timing"])
            limit = course.get("time_limit_minutes")
            if out["status"] == "ok" and limit and secs > limit * 60:
                out["status"] = "oot"
        else:
            values = course["controls"]
            got = set()
            pts = 0
            for c, _ in punches:
                if c in values and c not in got:
                    got.add(c)
                    pts += values[c]
            limit = course.get("time_limit_minutes")
            over = 0
            if limit is not None and secs > limit * 60:
                over = -(-(secs - limit * 60) // 60)      # ceil
            pts -= over * course.get("penalty_per_minute", 0)
            out["points"] = max(0, pts)
            out["status"] = "oot" if over else "ok"
        out["seconds"] = secs
    if run.get("manual"):
        out["status"] = run["manual"]
    return out


def _untimed(start, punches, timed, no_timing) -> int:
    """Seconds spent on legs that end at a no-timing control (matched in
    course order, as a runner's splits would be)."""
    total, last, j = 0, start, 0
    for code in timed:
        k = j
        while k < len(punches) and punches[k][0] != code:
            k += 1
        if k == len(punches):
            continue                     # missed: the next leg runs from the last one found
        t = punches[k][1]
        if code in no_timing:
            total += int((t - last).total_seconds())
        last, j = t, k + 1
    return total


def places(results: dict, score=False) -> dict:
    """{name: place or None} from {name: result}; ties share a place."""
    ranked_status = ("ok", "oot") if score else ("ok",)
    rows = [(n, r) for n, r in results.items()
            if r["status"] in ranked_status and r["seconds"] is not None
            and (not score or r["points"] is not None)]
    key = (lambda nr: (-nr[1]["points"], nr[1]["seconds"])) if score else \
        (lambda nr: nr[1]["seconds"])
    rows.sort(key=key)
    out = {n: None for n in results}
    place, prev = 0, None
    for i, (n, r) in enumerate(rows):
        k = key((n, r))
        if k != prev:
            place, prev = i + 1, k
        out[n] = place
    return out


def splits(start: datetime, punches, finish, controls) -> list:
    """Cumulative seconds at each course control in order (None = missed),
    matched forwards so repeated codes (butterflies) each get their own visit."""
    out, j = [], 0
    for code in controls:
        k = j
        while k < len(punches) and punches[k][0] != code:
            k += 1
        if k == len(punches):
            out.append(None)
        else:
            out.append(int((punches[k][1] - start).total_seconds()))
            j = k + 1
    return out
