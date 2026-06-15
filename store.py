"""
In-memory event store: the single source of truth the operator console edits.

The read pages (overview, results, ...) and the operator's create/edit/delete
actions all go through this module. It holds a normalised model -- courses,
classes and competitors as separate records joined by id -- seeded from the
mock roster in ``results.py`` so the app has something to show without the SI
reader. Result calculation is delegated to the ``results`` engine unchanged;
this module only owns the editable state and the validation around mutating it.

State lives in module-level dicts (no database yet -- it resets on restart),
guarded by a lock so concurrent dev-server requests can't corrupt a list
mid-update. Every mutator validates its input and raises ``StoreError`` with a
human-readable message that the API layer turns into a 400.
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime

import db
import rules
from results import (
    build_result,
    mock_classes,
    rank_results,
)


# ---------------------------------------------------------------------------
# Event configuration
# ---------------------------------------------------------------------------

# The calendar day the OPEN event runs on. Operators type wall-clock times
# (HH:MM:SS) which are pinned to this date for the engine. Defaults to today
# until an event file is opened (each event file carries its own date).
EVENT_DATE = date.today()

# The open event's display info (mirrors its row in the open event file). Each
# event is its own SQLite file (MeOS-style); within a file the event row is id 1.
# ``open`` is False until :func:`open_event` / :func:`new_event` loads a file.
EVENT = {
    "id": 1,
    "name": "",
    "date": "",
    "date_iso": "",
    "reader_port": os.environ.get("BMEOS_READER_PORT", "COM5"),
    "reader_enabled": bool(os.environ.get("BMEOS_READER")),
    "slug": "",
    "first_start": "",
    "type": "linear",
    "open": False,
}

_active_event_id = 1            # the event row id within the open file
_current_path: str | None = None  # path of the open event file (None = none open)


def active_event_id() -> int:
    return _active_event_id


def has_open_event() -> bool:
    return _current_path is not None


def current_event_path() -> str | None:
    return _current_path

# Statuses an operator may force on a competitor. "" / None means "automatic":
# let the engine decide. These mirror results.STATUS_* values.
MANUAL_STATUSES = ("ok", "mp", "dns", "dnf", "dsq", "oot")

COURSE_TYPES = ("linear", "score")


class StoreError(Exception):
    """A mutation was rejected; the message is safe to show the operator."""


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_lock = threading.RLock()

_courses: dict[int, dict] = {}
_classes: dict[int, dict] = {}
_competitors: dict[int, dict] = {}
_teams: dict[int, dict] = {}

_counters = {"course": 0, "class": 0, "competitor": 0, "team": 0}


def _next_id(kind: str) -> int:
    _counters[kind] += 1
    return _counters[kind]


# ---------------------------------------------------------------------------
# Time helpers (wall-clock string <-> datetime on EVENT_DATE)
# ---------------------------------------------------------------------------

def parse_clock(value, field: str = "time") -> datetime | None:
    """
    Parse a 'HH:MM:SS' (or 'HH:MM') wall-clock string into a datetime on the
    event date. Blank / None yields None. Raises StoreError on a bad format.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(text, fmt).time()
        except ValueError:
            continue
        return datetime.combine(EVENT_DATE, t)
    raise StoreError(f"{field} must be a time like 09:27:08 (got {value!r})")


def format_clock(dt: datetime | None) -> str:
    """Render a datetime as 'HH:MM:SS', or '' when missing."""
    return dt.strftime("%H:%M:%S") if dt else ""


# ---------------------------------------------------------------------------
# Coercion / validation helpers
# ---------------------------------------------------------------------------

def _as_int(value, field: str, *, minimum: int | None = None, allow_blank=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_blank:
            return None
        raise StoreError(f"{field} is required")
    try:
        out = int(str(value).strip())
    except (TypeError, ValueError):
        raise StoreError(f"{field} must be a whole number (got {value!r})")
    if minimum is not None and out < minimum:
        raise StoreError(f"{field} must be {minimum} or greater")
    return out


def _as_float(value, field: str, *, default: float = 0.0) -> float:
    text = str(value if value is not None else "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        raise StoreError(f"{field} must be a number (got {value!r})")


def _clean_str(value, field: str, *, required=False) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise StoreError(f"{field} is required")
    return text


def _coerce_punches(raw) -> list[dict]:
    """Validate an incoming punch list -> [{'code': int, 'time': datetime}]."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise StoreError("punches must be a list")
    punches = []
    for i, p in enumerate(raw, start=1):
        if not isinstance(p, dict):
            raise StoreError(f"punch {i} is malformed")
        code = _as_int(p.get("code"), f"punch {i} control code", minimum=1)
        time = parse_clock(p.get("time"), f"punch {i} time")
        if time is None:
            raise StoreError(f"punch {i} needs a time")
        punches.append({"code": code, "time": time})
    return punches


def _coerce_score_controls(raw) -> list[dict]:
    """Validate score-course controls -> [{'code': int, 'points': int}]."""
    if not isinstance(raw, list) or not raw:
        raise StoreError("a score course needs at least one control")
    controls = []
    seen = set()
    for i, c in enumerate(raw, start=1):
        if not isinstance(c, dict):
            raise StoreError(f"control {i} is malformed")
        code = _as_int(c.get("code"), f"control {i} code", minimum=1)
        if code in seen:
            raise StoreError(f"control {code} is listed twice")
        seen.add(code)
        points = _as_int(c.get("points"), f"control {code} points", minimum=0)
        controls.append({"code": code, "points": points})
    return controls


def _coerce_linear_controls(raw) -> list[int]:
    """Validate linear-course controls -> ordered [int, ...]."""
    if not isinstance(raw, list) or not raw:
        raise StoreError("a linear course needs at least one control")
    controls = []
    for i, code in enumerate(raw, start=1):
        controls.append(_as_int(code, f"control {i}", minimum=1))
    return controls


# ---------------------------------------------------------------------------
# Engine adapters: turn store records into the shapes results.py expects
# ---------------------------------------------------------------------------

def engine_course(course: dict) -> dict:
    """Convert a stored course into the dict the results engine consumes."""
    if course["type"] == "score":
        return {
            "type": "score",
            "controls": {c["code"]: c["points"] for c in course["controls"]},
            "time_limit_minutes": course["time_limit_minutes"],
            "penalty_per_minute": course["penalty_per_minute"],
            "score_formula": course.get("score_formula") or None,
        }
    return {
        "type": "linear",
        "controls": list(course["controls"]),
        "start_mode": course.get("start_mode", "clock"),
        "start_control": course.get("start_control"),
        # Mass start is stored as a wall-clock string; pin it to the event date
        # here so the pure engine receives a ready datetime like every other time.
        "mass_start": parse_clock(course.get("mass_start"), "Mass start"),
        "leg_lengths": course.get("leg_lengths") or [],
    }


def _engine_card(comp: dict, classes: dict | None = None) -> dict:
    """Convert a stored competitor into the card dict the engine consumes.

    ``classes`` lets the same adapter serve any event's model (the profile /
    series code evaluates non-active events loaded straight from the database);
    it defaults to the live in-memory classes.
    """
    classes = _classes if classes is None else classes
    return {
        "id": comp["id"],
        "name": comp["name"],
        "class": classes[comp["class_id"]]["name"],
        "club": comp["club"],
        "card_number": comp["card_number"],
        "start": comp["start"],
        "finish": comp["finish"],
        "punches": [(p["code"], p["time"]) for p in comp["punches"]],
        "manual_status": comp["manual_status"] or None,
    }


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def seed_demo() -> None:
    """Populate the OPEN event with the mock roster (idempotent: clears first).

    Used by tests and demos only -- the app never seeds; new events start empty.
    """
    _courses.clear()
    _classes.clear()
    _competitors.clear()
    _teams.clear()
    _counters.update(course=0, **{"class": 0}, competitor=0, team=0)

    roster = mock_classes()

    # The mock shares course dicts between classes by identity; preserve that so
    # two classes pointing at the same course stay sharing one course record.
    course_ids: dict[int, int] = {}

    def course_for(raw_course: dict, class_name: str) -> int:
        key = id(raw_course)
        if key in course_ids:
            return course_ids[key]
        if raw_course["type"] == "score":
            controls = [
                {"code": code, "points": pts}
                for code, pts in raw_course["controls"].items()
            ]
            cid = _insert_course(
                name=f"{class_name} course",
                ctype="score",
                controls=controls,
                time_limit_minutes=raw_course.get("time_limit_minutes"),
                penalty_per_minute=raw_course.get("penalty_per_minute", 0),
            )
        else:
            cid = _insert_course(
                name=f"{class_name} course",
                ctype="linear",
                controls=list(raw_course["controls"]),
            )
        course_ids[key] = cid
        return cid

    for entry in roster:
        course_id = course_for(entry["course"], entry["name"])
        class_id = _insert_class(name=entry["name"], course_id=course_id)
        for card in entry["cards"]:
            _insert_competitor(
                name=card["name"],
                club=card.get("club", ""),
                class_id=class_id,
                card_number=card.get("card_number"),
                start=card.get("start"),
                finish=card.get("finish"),
                punches=[{"code": c, "time": t} for c, t in card.get("punches", [])],
                manual_status=card.get("manual_status", "") or "",
            )


# Low-level inserts (no locking/validation -- used by _seed and the validated
# public creators below).

def _insert_course(*, name, ctype, controls, time_limit_minutes=None,
                   penalty_per_minute=0, start_mode="clock", start_control=None,
                   length_m=None, leg_lengths=None, mass_start=None,
                   score_formula=None) -> int:
    cid = _next_id("course")
    _courses[cid] = {
        "id": cid,
        "name": name,
        "type": ctype,
        "controls": controls,
        "time_limit_minutes": time_limit_minutes if ctype == "score" else None,
        "penalty_per_minute": penalty_per_minute if ctype == "score" else 0,
        "start_mode": start_mode,
        "start_control": start_control,
        "length_m": length_m,
        "mass_start": mass_start,
        "score_formula": score_formula if ctype == "score" else None,
        "leg_lengths": leg_lengths or [],
    }
    db.save_course(_active_event_id, _courses[cid])
    return cid


def _insert_class(*, name, course_id, kind="individual", legs=1, fee=0) -> int:
    cid = _next_id("class")
    _classes[cid] = {"id": cid, "name": name, "course_id": course_id,
                     "kind": kind, "legs": legs, "fee": fee}
    db.save_class(_active_event_id, _classes[cid])
    return cid


def _insert_competitor(*, name, club, class_id, card_number, start, finish,
                       punches, manual_status, bib=None, hired=False,
                       team_id=None, leg=None) -> int:
    cid = _next_id("competitor")
    _competitors[cid] = {
        "id": cid,
        "name": name,
        "club": club,
        "class_id": class_id,
        "card_number": card_number,
        "start": start,
        "finish": finish,
        "punches": punches,
        "manual_status": manual_status,
        "bib": bib,
        "hired": hired,
        "team_id": team_id,
        "leg": leg,
    }
    db.save_competitor(_active_event_id, _competitors[cid])
    return cid


# ---------------------------------------------------------------------------
# Course display helpers (shared with the templates via app.py)
# ---------------------------------------------------------------------------

def course_total_points(course: dict) -> int:
    if course["type"] != "score":
        return 0
    return sum(c["points"] for c in course["controls"])


def course_meta(course: dict) -> str:
    """One-line course summary used in class headers."""
    if course["type"] == "score":
        limit = course["time_limit_minutes"]
        limit_txt = f"{limit} min" if limit is not None else "no limit"
        return f"Score · {limit_txt} · {course_total_points(course)} pts"
    return f"Linear · {len(course['controls'])} controls"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def _classes_sorted() -> list[dict]:
    return sorted(_classes.values(), key=lambda c: c["name"].lower())


def _courses_sorted() -> list[dict]:
    return sorted(_courses.values(), key=lambda c: c["id"])


def _competitors_in_class(class_id: int) -> list[dict]:
    return [c for c in _competitors.values() if c["class_id"] == class_id]


def class_options() -> list[dict]:
    """[{id, name}] for class pickers, alphabetical."""
    return [{"id": c["id"], "name": c["name"]} for c in _classes_sorted()]


def course_options() -> list[dict]:
    """[{id, name, type}] for course pickers, in creation order."""
    return [
        {"id": c["id"], "name": c["name"], "type": c["type"]}
        for c in _courses_sorted()
    ]


def courses_with_classes() -> list[dict]:
    """[{course, classes:[name, ...]}] in creation order, for the courses page."""
    users: dict[int, list[str]] = {}
    for cls in _classes.values():
        users.setdefault(cls["course_id"], []).append(cls["name"])
    return [
        {"course": course, "classes": sorted(users.get(course["id"], []))}
        for course in _courses_sorted()
    ]


def get_course(course_id: int) -> dict | None:
    return _courses.get(course_id)


def get_class(class_id: int) -> dict | None:
    return _classes.get(class_id)


def get_competitor(comp_id: int) -> dict | None:
    return _competitors.get(comp_id)


def _evaluate_model(courses: dict, classes: dict,
                    competitors: dict) -> tuple[list[dict], dict[int, dict]]:
    """Run a (courses, classes, competitors) model through the engine. Shared by
    the live :func:`evaluate` and the cross-event :func:`evaluate_event`."""
    out = []
    by_id: dict[int, dict] = {}
    for cls in sorted(classes.values(), key=lambda c: c["name"].lower()):
        course = courses[cls["course_id"]]
        ecourse = engine_course(course)
        members = [c for c in competitors.values() if c["class_id"] == cls["id"]]
        results_in = [build_result(_engine_card(c, classes), ecourse) for c in members]
        ranked = rank_results(results_in).get(cls["name"], [])
        for r in ranked:
            by_id[r["id"]] = r
        out.append({"class": cls, "course": course, "results": ranked})
    return out, by_id


def evaluate() -> tuple[list[dict], dict[int, dict]]:
    """
    Run every class of the active event through the engine.

    Returns ``(classes, by_id)`` where ``classes`` is a list of
    ``{class, course, results}`` (results already ranked for that class) and
    ``by_id`` maps competitor id -> its result, for quick lookups.
    """
    with _lock:
        return _evaluate_model(_courses, _classes, _competitors)


def evaluate_event(event_id: int) -> tuple[list[dict], dict[int, dict]]:
    """Evaluate any event (the active one in memory, or another loaded from
    the database) without disturbing the active in-memory model."""
    with _lock:
        if event_id == _active_event_id:
            return _evaluate_model(_courses, _classes, _competitors)
        data = db.load_event(event_id)
        return _evaluate_model(data["courses"], data["classes"], data["competitors"])


def result_for(comp_id: int) -> dict | None:
    """The ranked result for one competitor (or None if they don't exist)."""
    _, by_id = evaluate()
    return by_id.get(comp_id)


# ---------------------------------------------------------------------------
# Teams / relay
# ---------------------------------------------------------------------------

def teams_in_class(class_id: int) -> list[dict]:
    return [t for t in _teams.values() if t["class_id"] == class_id]


def get_team(team_id: int) -> dict | None:
    return _teams.get(team_id)


def create_team(data: dict) -> dict:
    with _lock:
        name = _clean_str(data.get("name"), "Team name", required=True)
        class_id = _as_int(data.get("class_id"), "Class")
        cls = _classes.get(class_id)
        if cls is None:
            raise StoreError("That class no longer exists")
        if cls.get("kind") not in ("relay", "patrol"):
            raise StoreError("Teams can only be added to a relay or patrol class")
        tid = _next_id("team")
        _teams[tid] = {
            "id": tid, "class_id": class_id, "name": name,
            "club": _clean_str(data.get("club"), "Club"),
            "bib": _as_int(data.get("bib"), "Bib", minimum=1, allow_blank=True),
            "start": parse_clock(data.get("start"), "Start time"),
        }
        db.save_team(_active_event_id, _teams[tid])
        return dict(_teams[tid])


def delete_team(team_id: int) -> None:
    with _lock:
        if team_id not in _teams:
            raise StoreError("That team no longer exists")
        # Detach members from the team (they remain as competitors).
        for c in _competitors.values():
            if c.get("team_id") == team_id:
                c["team_id"] = None
                c["leg"] = None
                db.save_competitor(_active_event_id, c)
        del _teams[team_id]
        db.delete_team(team_id)


def _relay_team(team: dict, members: list[dict], by_id: dict) -> dict:
    """A relay team's result: legs run in sequence, time is their sum, valid only
    when every leg is OK."""
    legs = []
    total = 0
    ok = bool(members)
    for m in members:
        res = by_id.get(m["id"])
        leg_ok = res is not None and res["status"] == "ok" \
            and res["total_seconds"] is not None
        legs.append({"name": m["name"], "leg": m.get("leg"),
                     "seconds": res["total_seconds"] if res else None,
                     "status": res["status"] if res else "dns"})
        if leg_ok:
            total += res["total_seconds"]
        else:
            ok = False
    return {"team": team, "legs": legs,
            "total_seconds": total if ok else None, "ok": ok}


def _patrol_team(team: dict, members: list[dict], course: dict) -> dict:
    """A patrol team's result: the group runs one course *together*, so combine
    every member's punches into one run (earliest start to latest finish) and
    evaluate it against the course as a single entry."""
    starts = [m["start"] for m in members if m["start"] is not None]
    finishes = [m["finish"] for m in members if m["finish"] is not None]
    merged = sorted(
        ((p["code"], p["time"]) for m in members for p in m["punches"]),
        key=lambda cp: cp[1])
    card = {
        "id": team["id"], "name": team["name"], "class": "",
        "club": team.get("club"), "card_number": None,
        "start": min(starts) if starts else (team.get("start")),
        "finish": max(finishes) if finishes else None,
        "punches": merged, "manual_status": None,
    }
    res = build_result(card, engine_course(course))
    legs = [{"name": m["name"], "leg": m.get("leg"),
             "seconds": None, "status": ""} for m in members]
    ok = res["status"] == "ok" and res["total_seconds"] is not None
    return {"team": team, "legs": legs,
            "total_seconds": res["total_seconds"] if ok else None,
            "ok": ok, "status": res["status"]}


def team_results() -> list[dict]:
    """
    Team standings for relay and patrol classes, ranked by total time.

    Relay: a team's members run in ``leg`` order and the team time is the sum of
    their runs (valid only when every leg is OK). Patrol: the members run one
    course together, so their punches are combined into a single run. Teams that
    aren't complete/OK are listed unranked after the ranked ones.
    """
    with _lock:
        _, by_id = evaluate()
        out = []
        for cls in _classes_sorted():
            kind = cls.get("kind")
            if kind not in ("relay", "patrol"):
                continue
            course = _courses.get(cls["course_id"])
            teams = []
            for team in teams_in_class(cls["id"]):
                members = sorted(
                    (c for c in _competitors.values() if c.get("team_id") == team["id"]),
                    key=lambda c: (c.get("leg") or 0))
                if kind == "patrol" and course is not None:
                    teams.append(_patrol_team(team, members, course))
                else:
                    teams.append(_relay_team(team, members, by_id))
            ranked = sorted((t for t in teams if t["ok"]),
                            key=lambda t: t["total_seconds"])
            for i, t in enumerate(ranked):
                t["position"] = i + 1
            for t in teams:
                if not t["ok"]:
                    t["position"] = None
            out.append({"class": cls,
                        "teams": ranked + [t for t in teams if not t["ok"]]})
        return out


# ---------------------------------------------------------------------------
# Economy (entry fees + hire cards) and bib numbers
# ---------------------------------------------------------------------------

def economy_summary() -> dict:
    """Fee totals per class plus hire-card counts for the active event."""
    with _lock:
        rows = []
        grand_fee = 0.0
        hire_total = 0
        for cls in _classes_sorted():
            members = _competitors_in_class(cls["id"])
            fee = cls.get("fee", 0) or 0
            hired = sum(1 for c in members if c.get("hired"))
            subtotal = fee * len(members)
            grand_fee += subtotal
            hire_total += hired
            rows.append({"class": cls["name"], "entries": len(members),
                         "fee": fee, "subtotal": subtotal, "hired": hired})
        return {"rows": rows, "total_fees": grand_fee, "hire_cards": hire_total}


def assign_bibs(start: int = 1) -> int:
    """Number every competitor sequentially (by start time, then name)."""
    with _lock:
        ordered = sorted(
            _competitors.values(),
            key=lambda c: (c["start"] or datetime.max, c["name"].lower()))
        n = start
        for c in ordered:
            c["bib"] = n
            db.save_competitor(_active_event_id, c)
            n += 1
        return n - start


# ---------------------------------------------------------------------------
# Serialisation for the JSON editor
# ---------------------------------------------------------------------------

def competitor_json(comp: dict) -> dict:
    """Editable view of a competitor (times as clock strings)."""
    cls = _classes.get(comp["class_id"])
    return {
        "id": comp["id"],
        "name": comp["name"],
        "club": comp["club"],
        "class_id": comp["class_id"],
        "class_name": cls["name"] if cls else "",
        "card_number": comp["card_number"],
        "start": format_clock(comp["start"]),
        "finish": format_clock(comp["finish"]),
        "manual_status": comp["manual_status"] or "",
        "bib": comp.get("bib"),
        "hired": bool(comp.get("hired")),
        "team_id": comp.get("team_id"),
        "leg": comp.get("leg"),
        "punches": [
            {"code": p["code"], "time": format_clock(p["time"])}
            for p in comp["punches"]
        ],
    }


def course_json(course: dict) -> dict:
    return {
        "id": course["id"],
        "name": course["name"],
        "type": course["type"],
        "controls": (
            list(course["controls"])
            if course["type"] == "linear"
            else [dict(c) for c in course["controls"]]
        ),
        "time_limit_minutes": course["time_limit_minutes"],
        "penalty_per_minute": course["penalty_per_minute"],
        "start_mode": course.get("start_mode", "clock"),
        "start_control": course.get("start_control"),
        "mass_start": course.get("mass_start") or "",
        "score_formula": course.get("score_formula") or "",
        "length_m": course.get("length_m"),
    }


# ---------------------------------------------------------------------------
# Competitor mutations
# ---------------------------------------------------------------------------

def _validated_competitor_fields(data: dict, *, partial=False, current=None) -> dict:
    """
    Validate a competitor payload into stored field values.

    With ``partial`` only keys present in ``data`` are validated/returned, so an
    edit can patch a subset. ``current`` (the existing record) is used to
    cross-check start/finish ordering when only one side is supplied.
    """
    out: dict = {}
    has = lambda k: (k in data) if partial else True

    if has("name"):
        out["name"] = _clean_str(data.get("name"), "Name", required=True)
    if has("club"):
        out["club"] = _clean_str(data.get("club"), "Club")
    if has("class_id"):
        class_id = _as_int(data.get("class_id"), "Class")
        if class_id not in _classes:
            raise StoreError("That class no longer exists")
        out["class_id"] = class_id
    if has("card_number"):
        out["card_number"] = _as_int(
            data.get("card_number"), "SI card number", minimum=1, allow_blank=True
        )
    if has("start"):
        out["start"] = parse_clock(data.get("start"), "Start time")
    if has("finish"):
        out["finish"] = parse_clock(data.get("finish"), "Finish time")
    if has("manual_status"):
        status = _clean_str(data.get("manual_status"), "Status").lower()
        if status and status not in MANUAL_STATUSES:
            raise StoreError(f"Unknown status {status!r}")
        out["manual_status"] = status
    if has("punches"):
        out["punches"] = _coerce_punches(data.get("punches"))
    if has("bib"):
        out["bib"] = _as_int(data.get("bib"), "Bib", minimum=1, allow_blank=True)
    if has("hired"):
        out["hired"] = bool(data.get("hired"))
    if has("team_id"):
        team_id = _as_int(data.get("team_id"), "Team", minimum=1, allow_blank=True)
        if team_id is not None and team_id not in _teams:
            raise StoreError("That team no longer exists")
        out["team_id"] = team_id
    if has("leg"):
        out["leg"] = _as_int(data.get("leg"), "Leg", minimum=1, allow_blank=True)

    # Cross-field: finish must not precede start.
    start = out.get("start", current["start"] if current else None)
    finish = out.get("finish", current["finish"] if current else None)
    if start is not None and finish is not None and finish < start:
        raise StoreError("Finish time is before the start time")

    return out


def _check_card_unique(card_number, *, ignore: int | None = None) -> None:
    """A non-blank SI card may belong to only one competitor in the event --
    otherwise a finish download (matched by card) would be ambiguous."""
    if card_number is None:
        return
    for c in _competitors.values():
        if c["id"] != ignore and c["card_number"] == card_number:
            raise StoreError(
                f"SI card {card_number} is already assigned to {c['name']}")


def create_competitor(data: dict) -> dict:
    with _lock:
        fields = _validated_competitor_fields(data, partial=False)
        _check_card_unique(fields.get("card_number"))
        cid = _insert_competitor(
            name=fields["name"],
            club=fields.get("club", ""),
            class_id=fields["class_id"],
            card_number=fields.get("card_number"),
            start=fields.get("start"),
            finish=fields.get("finish"),
            punches=fields.get("punches", []),
            manual_status=fields.get("manual_status", ""),
            bib=fields.get("bib"),
            hired=fields.get("hired", False),
            team_id=fields.get("team_id"),
            leg=fields.get("leg"),
        )
        return competitor_json(_competitors[cid])


def update_competitor(comp_id: int, data: dict) -> dict:
    with _lock:
        comp = _competitors.get(comp_id)
        if comp is None:
            raise StoreError("That competitor no longer exists")
        fields = _validated_competitor_fields(data, partial=True, current=comp)
        if "card_number" in fields:
            _check_card_unique(fields["card_number"], ignore=comp_id)
        comp.update(fields)
        db.save_competitor(_active_event_id, comp)
        return competitor_json(comp)


def delete_competitor(comp_id: int) -> None:
    with _lock:
        if comp_id not in _competitors:
            raise StoreError("That competitor no longer exists")
        del _competitors[comp_id]
        db.delete_competitor(comp_id)


# ---------------------------------------------------------------------------
# Class mutations
# ---------------------------------------------------------------------------

def _check_unique_class_name(name: str, *, ignore: int | None = None) -> None:
    for c in _classes.values():
        if c["id"] != ignore and c["name"].lower() == name.lower():
            raise StoreError(f"A class named {name!r} already exists")


def _class_kind(value) -> str:
    kind = _clean_str(value, "Class kind").lower() or "individual"
    if kind not in ("individual", "relay", "patrol"):
        raise StoreError("Class kind must be 'individual', 'relay' or 'patrol'")
    return kind


def create_class(data: dict) -> dict:
    with _lock:
        name = _clean_str(data.get("name"), "Class name", required=True)
        _check_unique_class_name(name)
        course_id = _as_int(data.get("course_id"), "Course")
        if course_id not in _courses:
            raise StoreError("That course no longer exists")
        kind = _class_kind(data.get("kind"))
        legs = _as_int(data.get("legs"), "Legs", minimum=1, allow_blank=True) or 1
        fee = _as_float(data.get("fee"), "Fee")
        cid = _insert_class(name=name, course_id=course_id, kind=kind, legs=legs, fee=fee)
        return dict(_classes[cid])


def update_class(class_id: int, data: dict) -> dict:
    with _lock:
        cls = _classes.get(class_id)
        if cls is None:
            raise StoreError("That class no longer exists")
        if "name" in data:
            name = _clean_str(data.get("name"), "Class name", required=True)
            _check_unique_class_name(name, ignore=class_id)
            cls["name"] = name
        if "course_id" in data:
            course_id = _as_int(data.get("course_id"), "Course")
            if course_id not in _courses:
                raise StoreError("That course no longer exists")
            cls["course_id"] = course_id
        if "kind" in data:
            cls["kind"] = _class_kind(data.get("kind"))
        if "legs" in data:
            cls["legs"] = _as_int(data.get("legs"), "Legs", minimum=1, allow_blank=True) or 1
        if "fee" in data:
            cls["fee"] = _as_float(data.get("fee"), "Fee")
        db.save_class(_active_event_id, cls)
        return dict(cls)


def delete_class(class_id: int) -> None:
    with _lock:
        cls = _classes.get(class_id)
        if cls is None:
            raise StoreError("That class no longer exists")
        members = _competitors_in_class(class_id)
        if members:
            raise StoreError(
                f"{cls['name']} still has {len(members)} "
                f"competitor{'s' if len(members) != 1 else ''}; move or remove them first"
            )
        del _classes[class_id]
        db.delete_class(class_id)


# ---------------------------------------------------------------------------
# Course mutations
# ---------------------------------------------------------------------------

def _validated_course_fields(data: dict) -> dict:
    name = _clean_str(data.get("name"), "Course name", required=True)
    ctype = _clean_str(data.get("type"), "Course type", required=True).lower()
    if ctype not in COURSE_TYPES:
        raise StoreError("Course type must be 'linear' or 'score'")

    if ctype == "score":
        controls = _coerce_score_controls(data.get("controls"))
        limit = _as_int(
            data.get("time_limit_minutes"), "Time limit", minimum=1, allow_blank=True
        )
        penalty = _as_int(
            data.get("penalty_per_minute"), "Penalty", minimum=0, allow_blank=True
        ) or 0
        formula = _clean_str(data.get("score_formula"), "Scoring formula")
        if formula:
            try:
                formula = rules.validate_formula(formula)
            except rules.RuleError as err:
                raise StoreError(f"Scoring formula: {err}")
        return {
            "name": name, "type": "score", "controls": controls,
            "time_limit_minutes": limit, "penalty_per_minute": penalty,
            "score_formula": formula or None,
        }

    controls = _coerce_linear_controls(data.get("controls"))
    start_mode = _clean_str(data.get("start_mode"), "Start mode").lower() or "clock"
    if start_mode not in ("clock", "punch", "mass", "chase"):
        raise StoreError("Start mode must be 'clock', 'punch', 'mass' or 'chase'")
    start_control = _as_int(data.get("start_control"), "Start control",
                            minimum=1, allow_blank=True)
    if start_mode == "punch" and start_control is None:
        raise StoreError("A punch-start course needs a start control code")
    mass_start = _clean_str(data.get("mass_start"), "Mass start")
    if start_mode == "mass":
        if not mass_start:
            raise StoreError("A mass-start course needs a mass-start time")
        parse_clock(mass_start, "Mass start")  # validate HH:MM:SS; raises on bad
    else:
        mass_start = ""
    length_m = _as_int(data.get("length_m"), "Course length", minimum=0, allow_blank=True)
    out = {
        "name": name, "type": "linear", "controls": controls,
        "time_limit_minutes": None, "penalty_per_minute": 0,
        "start_mode": start_mode, "start_control": start_control,
        "mass_start": mass_start or None,
        "length_m": length_m,
    }
    # Only carry leg_lengths when explicitly supplied (the IOF importer sends
    # them; the course editor doesn't). update_course preserves the existing
    # value when absent, so editing a course can't wipe imported leg lengths.
    if isinstance(data.get("leg_lengths"), list):
        out["leg_lengths"] = data["leg_lengths"]
    return out


def create_course(data: dict) -> dict:
    with _lock:
        fields = _validated_course_fields(data)
        cid = _insert_course(
            name=fields["name"], ctype=fields["type"], controls=fields["controls"],
            time_limit_minutes=fields["time_limit_minutes"],
            penalty_per_minute=fields["penalty_per_minute"],
            start_mode=fields.get("start_mode", "clock"),
            start_control=fields.get("start_control"),
            length_m=fields.get("length_m"),
            mass_start=fields.get("mass_start"),
            score_formula=fields.get("score_formula"),
            leg_lengths=fields.get("leg_lengths") or [],
        )
        return course_json(_courses[cid])


def update_course(course_id: int, data: dict) -> dict:
    with _lock:
        course = _courses.get(course_id)
        if course is None:
            raise StoreError("That course no longer exists")
        fields = _validated_course_fields(data)
        course.update(fields)
        db.save_course(_active_event_id, course)
        return course_json(course)


def delete_course(course_id: int) -> None:
    with _lock:
        course = _courses.get(course_id)
        if course is None:
            raise StoreError("That course no longer exists")
        users = [c["name"] for c in _classes.values() if c["course_id"] == course_id]
        if users:
            raise StoreError(
                "This course is used by " + ", ".join(sorted(users))
                + "; reassign those classes first"
            )
        del _courses[course_id]
        db.delete_course(course_id)


# ---------------------------------------------------------------------------
# Live preview (evaluate an unsaved card without storing it)
# ---------------------------------------------------------------------------

def preview(data: dict) -> dict:
    """
    Evaluate a competitor payload against its class's course without saving.

    Returns the engine result (status/time/points/splits/missed_control). Used
    by the editor to show the operator the effect of their edits live.
    """
    with _lock:
        class_id = _as_int(data.get("class_id"), "Class")
        cls = _classes.get(class_id)
        if cls is None:
            raise StoreError("That class no longer exists")
        ecourse = engine_course(_courses[cls["course_id"]])

        card = {
            "id": data.get("id"),
            "name": _clean_str(data.get("name"), "Name") or "—",
            "class": cls["name"],
            "club": _clean_str(data.get("club"), "Club"),
            "card_number": _as_int(
                data.get("card_number"), "SI card number", minimum=1, allow_blank=True
            ),
            "start": parse_clock(data.get("start"), "Start time"),
            "finish": parse_clock(data.get("finish"), "Finish time"),
            "punches": [(p["code"], p["time"]) for p in _coerce_punches(data.get("punches"))],
            "manual_status": (_clean_str(data.get("manual_status"), "Status").lower() or None),
        }
        return build_result(card, ecourse)


# ---------------------------------------------------------------------------
# SI card reads (the download station / simulator feeds these)
# ---------------------------------------------------------------------------

def coerce_card(data: dict) -> dict:
    """
    Validate a JSON reader payload into a card dict the read path consumes.

    Reuses the same punch/time/number validation as the editor (``_coerce_punches``,
    ``parse_clock``, ``_as_int``) so the simulator and the editor accept exactly
    the same input, and produces ``punches`` as ``[(code, datetime), ...]`` --
    the shape ``apply_card_read`` and the real reader emit.

    Held under ``_lock`` because ``parse_clock`` pins times to the module-global
    ``EVENT_DATE``; the lock keeps a concurrent event switch from re-pinning the
    date mid-parse.
    """
    with _lock:
        punches = [(p["code"], p["time"]) for p in _coerce_punches(data.get("punches"))]
        return {
            "card_number": _as_int(data.get("card_number"), "SI card number", minimum=1),
            "start": parse_clock(data.get("start"), "Start time"),
            "finish": parse_clock(data.get("finish"), "Finish time"),
            "punches": punches,
            "station_id": data.get("station_id"),
        }


def find_by_card(card_number: int) -> dict | None:
    """The competitor registered to an SI card number, or None."""
    for c in _competitors.values():
        if c["card_number"] == card_number:
            return c
    return None


def add_radio_punch(card_number: int, code: int, time: datetime,
                    station_id: str | None = None) -> dict:
    """
    Record a single live punch from a radio / online control.

    Unlike a finish download (which replaces the whole card), a radio control
    streams one intermediate punch at a time. The punch is inserted into the
    competitor's list in time order so the splits stay correct as the run
    progresses. Raises StoreError if no competitor is registered for the card.
    """
    with _lock:
        comp = find_by_card(card_number)
        if comp is None:
            raise StoreError(f"No competitor registered for SI card {card_number}")
        comp["punches"].append({"code": code, "time": time, "station_id": station_id})
        comp["punches"].sort(key=lambda p: p["time"])
        db.save_competitor(_active_event_id, comp)
        return comp


def apply_card_read(card: dict) -> dict:
    """
    Record a downloaded SI card against the competitor registered to it.

    ``card`` has the MOCK_CARD_DATA shape: ``card_number``, ``punches`` as
    ``[(code, datetime), ...]``, and optional ``start`` / ``finish`` datetimes
    plus an optional ``station_id``. The matching competitor's start, finish and
    raw punches are overwritten from the card (a re-read replaces the previous
    download); a manual status override, if any, is left untouched.

    Returns the competitor's editable JSON. Raises StoreError if no competitor is
    registered for the card number -- on-the-day entries must be created first.
    """
    with _lock:
        number = card.get("card_number")
        comp = find_by_card(number) if number is not None else None
        if comp is None:
            raise StoreError(f"No competitor registered for SI card {number}")
        station = card.get("station_id")
        comp["punches"] = [
            {"code": code, "time": time, "station_id": station}
            for code, time in card.get("punches", [])
        ]
        if card.get("start") is not None:
            comp["start"] = card["start"]
        if card.get("finish") is not None:
            comp["finish"] = card["finish"]
        db.save_competitor(_active_event_id, comp)
        return competitor_json(comp)


def auto_create_from_card(card: dict) -> dict:
    """
    Register an unknown card by building a course + class from its punches.

    This is MeOS's interactive setup: read a card the system has never seen and
    it creates the course (from the punched control sequence), a class on that
    course, and the competitor -- then records the run. An existing linear course
    with the same control order is reused, as is any class already on it, so a
    whole field reading out the same loop lands in one auto class. The runner DB
    supplies the name/club when the card is known, else it's "Card <n>".

    If the card is already registered this is just :func:`apply_card_read`.
    """
    with _lock:
        number = card.get("card_number")
        if number is None:
            raise StoreError("A card needs a number to auto-create an entry")
        if find_by_card(number) is not None:
            return apply_card_read(card)

        codes = [code for code, _ in card.get("punches", [])]
        if not codes:
            raise StoreError("Card has no punches to build a course from")

        course_id = next(
            (c["id"] for c in _courses.values()
             if c["type"] == "linear" and list(c["controls"]) == codes), None)
        if course_id is None:
            course_id = _insert_course(
                name=f"Auto course {len(_courses) + 1}", ctype="linear",
                controls=codes)

        class_id = next(
            (cl["id"] for cl in _classes.values() if cl["course_id"] == course_id),
            None)
        if class_id is None:
            class_id = _insert_class(
                name=f"Auto {len(_classes) + 1}", course_id=course_id)

        name, club = f"Card {number}", ""
        import runners  # local import: runners owns a separate DB; avoid any cycle
        known = runners.lookup(number)
        if known:
            name = known.get("name") or name
            club = known.get("club") or ""

        station = card.get("station_id")
        _insert_competitor(
            name=name, club=club, class_id=class_id, card_number=number,
            start=card.get("start"), finish=card.get("finish"),
            punches=[{"code": c, "time": t, "station_id": station}
                     for c, t in card.get("punches", [])],
            manual_status="")
        return competitor_json(find_by_card(number))


# ---------------------------------------------------------------------------
# Events: one SQLite file per event (MeOS-style), opened from the start page
# ---------------------------------------------------------------------------

def _apply_event(row: dict) -> None:
    """Point EVENT (templates) + EVENT_DATE (parse_clock) at an open event row."""
    global EVENT_DATE
    EVENT_DATE = date.fromisoformat(row["date_iso"])
    EVENT.update({
        "id": row["id"],
        "name": row["name"],
        "date_iso": row["date_iso"],
        "date": EVENT_DATE.strftime("%d %B %Y").lstrip("0"),
        "reader_port": row["reader_port"] or os.environ.get("BMEOS_READER_PORT", "COM5"),
        "reader_enabled": bool(row["reader_enabled"]),
        "slug": row["slug"] or "",
        "first_start": row["first_start"] or "",
        "type": row["type"] or "linear",
        "open": True,
    })


def _slugify(name: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "event"


def events_dir() -> str:
    """Folder the event files live in (env BMEOS_EVENTS_DIR, default ./events)."""
    folder = os.environ.get("BMEOS_EVENTS_DIR", "events")
    os.makedirs(folder, exist_ok=True)
    return folder


def events_in_folder(folder: str | None = None) -> list[dict]:
    """List openable event files (``*.bmeos``) in the folder, newest first."""
    folder = folder or events_dir()
    out = []
    if os.path.isdir(folder):
        for fn in os.listdir(folder):
            if not fn.endswith(".bmeos"):
                continue
            path = os.path.join(folder, fn)
            meta = db.read_event_meta(path)
            if meta:
                out.append({
                    "path": path, "filename": fn, "name": meta["name"],
                    "date_iso": meta["date_iso"], "type": meta.get("type", "linear"),
                    "open": path == _current_path,
                })
    out.sort(key=lambda e: (e["date_iso"], e["name"]), reverse=True)
    return out


def _load_active() -> None:
    """Replace the in-memory model with the open event file's data."""
    data = db.load_event(_active_event_id)
    _courses.clear(); _courses.update(data["courses"])
    _classes.clear(); _classes.update(data["classes"])
    _competitors.clear(); _competitors.update(data["competitors"])
    _teams.clear(); _teams.update(data.get("teams", {}))
    _counters.update(data["counters"])


def open_event(path: str) -> dict:
    """Open an existing event file as the current event."""
    global _current_path
    with _lock:
        if not os.path.exists(path):
            raise StoreError("That event file no longer exists")
        # Validate on a throwaway connection FIRST, so a foreign/corrupt file
        # never swaps the live connection (which would route saves to it).
        if db.read_event_meta(path) is None:
            raise StoreError("That file isn't a better-meos event")
        db.connect(path)
        _load_active()
        row = db.get_event(_active_event_id)
        _apply_event(row)
        _current_path = path
        return dict(row)


def new_event(meta: dict, folder: str | None = None) -> dict:
    """
    Create a new empty event file from ``meta`` (name, date, first_start, type)
    and open it. Filename derives from the name; never overwrites an existing one.
    """
    global _current_path
    with _lock:
        name = _clean_str(meta.get("name"), "Event name", required=True)
        date_iso = _clean_str(meta.get("date"), "Event date", required=True)
        try:
            date.fromisoformat(date_iso)
        except ValueError:
            raise StoreError("Event date must be YYYY-MM-DD")
        etype = _clean_str(meta.get("type"), "Type").lower() or "linear"
        if etype not in ("linear", "score", "relay"):
            raise StoreError("Type must be linear, score or relay")
        first_start = meta.get("first_start") or ""
        if first_start:
            parse_clock(first_start, "First start")  # validate; raises on bad

        slug = _slugify(name)
        folder = folder or events_dir()
        path = os.path.join(folder, f"{slug}.bmeos")
        i = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{slug}-{i}.bmeos")
            i += 1

        db.connect(path)  # creates the file + schema
        _courses.clear(); _classes.clear(); _competitors.clear(); _teams.clear()
        _counters.update(course=0, **{"class": 0}, competitor=0, team=0)
        row = {
            "id": _active_event_id, "name": name, "date_iso": date_iso,
            "reader_port": os.environ.get("BMEOS_READER_PORT", "COM5"),
            "reader_enabled": bool(os.environ.get("BMEOS_READER")),
            "slug": slug, "first_start": first_start or None, "type": etype,
        }
        db.save_event(row)
        _apply_event(row)
        _current_path = path
        return {**row, "path": path}


def close_event() -> None:
    """Close the current event (back to the start page)."""
    global _current_path
    with _lock:
        if _current_path is not None:
            db.close()
        _courses.clear(); _classes.clear(); _competitors.clear(); _teams.clear()
        EVENT.update({"name": "", "date": "", "date_iso": "", "slug": "", "open": False})
        _current_path = None


def reload() -> None:
    """Reload the open event from disk (used after a restore)."""
    with _lock:
        _load_active()


def restore(backup_path: str) -> None:
    """Replace the open event file's data from a backup, then reload memory."""
    import sqlite3
    with _lock:
        try:
            db.restore_from(backup_path)
        except (ValueError, sqlite3.Error) as err:
            raise StoreError(f"Could not restore backup: {err}")
        _load_active()


# No event is opened on import: the app starts at the event-selection page and
# opens/creates an event file from there (see app.py / start.html).
