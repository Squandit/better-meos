"""
Performance analytics (pure statistics, no external API).

Built on the engine's positional split alignment (``results.aligned_splits``),
so it handles butterfly/repeated controls and mispunches the same way the splits
table does. Everything here is deterministic and unit-tested; the AI layer
(:mod:`ai`) consumes these numbers and adds natural-language commentary on top.

Provides:
* :func:`class_leg_stats` -- per-leg field average / best for a class.
* :func:`competitor_legs` -- one competitor's legs vs the class average, with a
  weakness flag for legs well off the field.
* :func:`split_trends` -- a person's placing/time trend across events.
* :func:`shared_legs` -- consecutive control pairs shared between courses.
* :func:`course_checks` -- rule-based course-setting validations.
"""

from __future__ import annotations

from results import aligned_splits, format_split

# A leg is "weak" when the competitor is at least this fraction slower than the
# class average for that leg.
WEAK_THRESHOLD = 0.25


def _ranked_aligned(results: list[dict], controls: list[int]) -> list[tuple[dict, list[dict]]]:
    """Pair each finished result with its positionally aligned splits."""
    out = []
    for r in results:
        if r.get("start") is None or r.get("finish") is None:
            continue
        out.append((r, aligned_splits(r["start"], r.get("punches", []),
                                      r.get("finish"), controls)))
    return out


def class_leg_stats(results: list[dict], controls: list[int]) -> list[dict]:
    """
    Per-leg field statistics for a class.

    Returns one entry per control (plus Finish): ``{control, count, avg_seconds,
    best_seconds}`` over the competitors who have a time for that leg.
    """
    aligned = _ranked_aligned(results, controls)
    legs = [{"control": c} for c in controls] + [{"control": "F"}]
    stats = []
    for i, leg in enumerate(legs):
        times = [a[i]["leg_seconds"] for _, a in aligned if not a[i]["missing"]]
        stats.append({
            "control": leg["control"],
            "count": len(times),
            "avg_seconds": round(sum(times) / len(times)) if times else None,
            "best_seconds": min(times) if times else None,
        })
    return stats


def competitor_legs(results: list[dict], controls: list[int],
                    competitor_id: int) -> dict:
    """
    One competitor's legs measured against the class average.

    Returns ``{name, legs: [{control, seconds, avg_seconds, pct_vs_avg, weak}],
    weak_legs: [...]}``. ``pct_vs_avg`` is the fraction slower than the field
    average (negative = faster); ``weak`` marks legs past :data:`WEAK_THRESHOLD`.
    """
    stats = {s["control"]: s for s in class_leg_stats(results, controls)}
    legs_order = controls + ["F"]
    target = next((r for r in results if r.get("id") == competitor_id), None)
    if target is None or target.get("start") is None or target.get("finish") is None:
        return {"name": target["name"] if target else "", "legs": [], "weak_legs": []}

    aligned = aligned_splits(target["start"], target.get("punches", []),
                             target.get("finish"), controls)
    legs = []
    for i, control in enumerate(legs_order):
        cell = aligned[i]
        avg = stats[control]["avg_seconds"]
        if cell["missing"] or not avg:
            legs.append({"control": control, "seconds": None, "avg_seconds": avg,
                         "pct_vs_avg": None, "weak": False})
            continue
        secs = cell["leg_seconds"]
        pct = (secs - avg) / avg
        legs.append({
            "control": control, "seconds": secs, "avg_seconds": avg,
            "pct_vs_avg": pct, "weak": pct >= WEAK_THRESHOLD,
        })
    weak = sorted((l for l in legs if l["weak"]), key=lambda l: -l["pct_vs_avg"])
    return {"name": target["name"], "legs": legs, "weak_legs": weak}


def split_trends(profile: dict) -> list[dict]:
    """A person's placing/time across events, oldest first (for trend display)."""
    rows = []
    for entry in profile.get("results", []):
        r = entry["result"]
        rows.append({
            "event": entry["event"]["name"],
            "date": entry["event"]["date_iso"],
            "class": entry["class"],
            "position": r.get("position"),
            "time": format_split(r["total_seconds"]) if r.get("total_seconds") is not None else "",
            "status": r["status"],
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def shared_legs(courses: list[dict]) -> list[dict]:
    """
    Consecutive control pairs (legs) used by more than one course.

    ``courses`` are store course dicts. Only linear courses contribute (a leg is
    an ordered pair of adjacent controls). Returns
    ``[{"leg": (a, b), "courses": [name, ...]}]`` for legs shared by 2+ courses --
    a course-setting concern (shared legs let runners follow each other).
    """
    legs: dict = {}
    for course in courses:
        if course["type"] != "linear":
            continue
        codes = course["controls"]
        for a, b in zip(codes, codes[1:]):
            legs.setdefault((a, b), set()).add(course["name"])
    return [{"leg": leg, "courses": sorted(names)}
            for leg, names in sorted(legs.items()) if len(names) > 1]


def course_checks(course: dict) -> list[dict]:
    """
    Rule-based course-setting validations for one course.

    Returns ``[{"level": "warn"|"error", "message": str}]``. Geometric/IOF
    distance ratios need control coordinates, which the model doesn't carry, so
    the checks here are structural (length, back-to-back controls, score points).
    """
    findings = []
    if course["type"] == "linear":
        codes = course["controls"]
        if len(codes) < 2:
            findings.append({"level": "warn",
                             "message": "Very short course (fewer than 2 controls)."})
        for a, b in zip(codes, codes[1:]):
            if a == b:
                findings.append({"level": "error",
                                 "message": f"Control {a} is visited twice back-to-back."})
    else:
        total = sum(c["points"] for c in course["controls"])
        if total == 0:
            findings.append({"level": "warn",
                             "message": "Score course has no points assigned."})
        if course.get("time_limit_minutes") is None:
            findings.append({"level": "warn",
                             "message": "Score course has no time limit."})
    return findings
