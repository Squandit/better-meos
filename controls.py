"""
The Controls page: every control the event's courses use, what happened at it,
and its status (MeOS-style: OK, bad, optional, no timing, plus alternate codes).

The numbers are there to spot trouble while the event runs: a control nearly
everyone misses is usually missing, moved or broken; first and last punch
times out of line with the start window point at a unit with a wrong clock;
and codes that were punched but aren't on any course are often a replacement
unit that should be added as an alternate.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median

import config
import store
from results import CONTROL_STATUSES, aligned_splits, parse_control_config

STATUS_LABELS = {"ok": "OK", "bad": "Bad (not needed)", "optional": "Optional",
                 "no_timing": "No timing (leg not timed)"}


def _clock(dt) -> str:
    return dt.strftime("%H:%M:%S") if dt else ""


def report() -> dict:
    """``{controls: [...], unknown: [...]}`` for the Controls page."""
    evaluated, _ = store.evaluate()
    rules = store.control_config()
    info: dict[int, dict] = {}

    def entry(code):
        return info.setdefault(code, {
            "code": code, "courses": [], "punched": 0, "missed": 0, "needed": 0,
            "first": None, "last": None, "legs": []})

    for course in sorted(store._courses.values(), key=lambda c: c["name"].lower()):
        codes = ([c["code"] for c in course["controls"]] if course["type"] == "score"
                 else list(course["controls"]))
        for n, code in enumerate(codes, start=1):
            label = course["name"] if course["type"] == "score" else f"{course['name']} #{n}"
            entry(code)["courses"].append(label)

    seen_codes: dict[int, int] = defaultdict(int)
    for e in evaluated:
        for r in e["results"]:
            if r["status"] == "pending":
                continue
            course = store.get_course(r.get("course_id")) or e["course"]
            for code, t in r.get("punches", []):
                seen_codes[code] += 1
                if code in info:
                    item = info[code]
                    item["punched"] += 1
                    item["first"] = t if item["first"] is None or t < item["first"] else item["first"]
                    item["last"] = t if item["last"] is None or t > item["last"] else item["last"]
            if course["type"] != "linear" or r.get("start") is None or r.get("finish") is None:
                continue
            required = set(store.engine_course(course)["required"])
            for cell in aligned_splits(r["start"], r.get("punches", []), r["finish"],
                                       store.split_controls(course)[0])[:-1]:
                item = info.get(cell["control"])
                if item is None:
                    continue
                if cell["control"] in required:
                    item["needed"] += 1
                    if cell["missing"]:
                        item["missed"] += 1
                if not cell["missing"] and cell["leg_seconds"] is not None:
                    item["legs"].append(cell["leg_seconds"])

    alternates = {alt for rule in rules.values() for alt in rule.get("alternates", [])}
    rows = []
    for code in sorted(info):
        item = info[code]
        rule = rules.get(code, {})
        rows.append({
            "code": code, "courses": item["courses"],
            "status": rule.get("status", "ok"), "alternates": rule.get("alternates", []),
            "punched": item["punched"], "needed": item["needed"], "missed": item["missed"],
            "missed_pct": round(100 * item["missed"] / item["needed"]) if item["needed"] else 0,
            "first": _clock(item["first"]), "last": _clock(item["last"]),
            "median_leg": round(median(item["legs"])) if item["legs"] else None,
        })
    unknown = [{"code": code, "punches": n} for code, n in sorted(seen_codes.items())
               if code not in info and code not in alternates]
    return {"controls": rows, "unknown": unknown, "statuses": CONTROL_STATUSES,
            "labels": STATUS_LABELS}


def update(code: int, status: str, alternates) -> dict:
    """Set one control's status and alternate codes (saved with the event)."""
    if status not in CONTROL_STATUSES:
        raise store.StoreError("Unknown control status")
    alts = []
    for part in (alternates if isinstance(alternates, list)
                 else str(alternates or "").replace(";", ",").split(",")):
        part = str(part).strip()
        if not part:
            continue
        if not part.isdigit():
            raise store.StoreError(f"Alternate codes are numbers ({part!r} isn't)")
        if int(part) != code and int(part) not in alts:
            alts.append(int(part))
    current = {str(k): v for k, v in parse_control_config(config.get("control_config")).items()}
    if status == "ok" and not alts:
        current.pop(str(code), None)
    else:
        current[str(code)] = {"status": status, "alternates": alts}
    before = store.control_config().get(code, {})
    config.save({"control_config": current}, target="event")
    store._audit("control changed", f"control {code}",
                 f"{before.get('status', 'ok')} -> {status}"
                 + (f", alternates {', '.join(map(str, alts))}" if alts else ""))
    return current.get(str(code), {"status": "ok", "alternates": []})
