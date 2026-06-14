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
from results import (
    build_result,
    mock_classes,
    rank_results,
)


# ---------------------------------------------------------------------------
# Event configuration
# ---------------------------------------------------------------------------

# The single calendar day the event runs on. Operators type wall-clock times
# (HH:MM:SS); they are pinned to this date to make real datetimes for the engine.
EVENT_DATE = date(2026, 5, 17)

# The active event. ``id`` ties every record to its event row in the database;
# ``reader_enabled`` gates the real SI hardware loop (off unless BMEOS_READER is
# set, so the app runs on mock/simulated data by default).
EVENT = {
    "id": 1,
    "name": "Jarrahdale Middle Distance",
    "date": EVENT_DATE.strftime("%d %B %Y").lstrip("0"),
    "date_iso": EVENT_DATE.isoformat(),
    "reader_port": os.environ.get("BMEOS_READER_PORT", "COM5"),
    "reader_enabled": bool(os.environ.get("BMEOS_READER")),
    "slug": "jarrahdale-middle",
}

# The event currently held in memory. Single-event today; the multi-event work
# (order.txt) flips this to switch which event the store is editing.
_active_event_id = EVENT["id"]


def active_event_id() -> int:
    return _active_event_id

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

_counters = {"course": 0, "class": 0, "competitor": 0}


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
        }
    return {"type": "linear", "controls": list(course["controls"])}


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

def _seed() -> None:
    """Populate the store from the mock roster (idempotent: clears first)."""
    _courses.clear()
    _classes.clear()
    _competitors.clear()
    _counters.update(course=0, **{"class": 0}, competitor=0)

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
                   penalty_per_minute=0) -> int:
    cid = _next_id("course")
    _courses[cid] = {
        "id": cid,
        "name": name,
        "type": ctype,
        "controls": controls,
        "time_limit_minutes": time_limit_minutes if ctype == "score" else None,
        "penalty_per_minute": penalty_per_minute if ctype == "score" else 0,
    }
    db.save_course(_active_event_id, _courses[cid])
    return cid


def _insert_class(*, name, course_id) -> int:
    cid = _next_id("class")
    _classes[cid] = {"id": cid, "name": name, "course_id": course_id}
    db.save_class(_active_event_id, _classes[cid])
    return cid


def _insert_competitor(*, name, club, class_id, card_number, start, finish,
                       punches, manual_status) -> int:
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


def create_class(data: dict) -> dict:
    with _lock:
        name = _clean_str(data.get("name"), "Class name", required=True)
        _check_unique_class_name(name)
        course_id = _as_int(data.get("course_id"), "Course")
        if course_id not in _courses:
            raise StoreError("That course no longer exists")
        cid = _insert_class(name=name, course_id=course_id)
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
        return {
            "name": name, "type": "score", "controls": controls,
            "time_limit_minutes": limit, "penalty_per_minute": penalty,
        }

    controls = _coerce_linear_controls(data.get("controls"))
    return {
        "name": name, "type": "linear", "controls": controls,
        "time_limit_minutes": None, "penalty_per_minute": 0,
    }


def create_course(data: dict) -> dict:
    with _lock:
        fields = _validated_course_fields(data)
        cid = _insert_course(
            name=fields["name"], ctype=fields["type"], controls=fields["controls"],
            time_limit_minutes=fields["time_limit_minutes"],
            penalty_per_minute=fields["penalty_per_minute"],
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


# ---------------------------------------------------------------------------
# Boot: connect the database, then either seed a fresh DB or load existing data
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Events & series (multi-event support)
# ---------------------------------------------------------------------------

def _apply_event(row: dict) -> None:
    """Point the store's display + time handling at an event row.

    Updates the ``EVENT`` dict the templates read, and the module-level
    ``EVENT_DATE`` that :func:`parse_clock` pins wall-clock times to (each event
    runs on its own day)."""
    global EVENT_DATE
    EVENT_DATE = date.fromisoformat(row["date_iso"])
    EVENT.update({
        "id": row["id"],
        "name": row["name"],
        "date_iso": row["date_iso"],
        "date": EVENT_DATE.strftime("%d %B %Y").lstrip("0"),
        "reader_port": row.get("reader_port") or "",
        "reader_enabled": bool(row.get("reader_enabled")),
        "slug": row.get("slug") or "",
        "series_id": row.get("series_id"),
    })


def _slugify(name: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "event"


def _unique_slug(base: str) -> str:
    existing = {e["slug"] for e in db.all_events()}
    slug, i = base, 2
    while slug in existing:
        slug, i = f"{base}-{i}", i + 1
    return slug


def list_events() -> list[dict]:
    """All events, each flagged whether it's the active one."""
    return [{**e, "active": e["id"] == _active_event_id} for e in db.all_events()]


def create_event(data: dict) -> dict:
    """Create a new (empty) event. Does not switch to it."""
    with _lock:
        name = _clean_str(data.get("name"), "Event name", required=True)
        date_iso = _clean_str(data.get("date"), "Event date", required=True)
        try:
            date.fromisoformat(date_iso)
        except ValueError:
            raise StoreError("Event date must be YYYY-MM-DD")
        row = {
            "id": db.next_event_id(),
            "name": name,
            "date_iso": date_iso,
            "reader_port": _clean_str(data.get("reader_port"), "Reader port") or "COM5",
            "reader_enabled": False,
            "slug": _unique_slug(_slugify(name)),
            "series_id": _as_int(data.get("series_id"), "Series", allow_blank=True),
        }
        db.save_event(row)
        return row


def set_active_event(event_id: int) -> dict:
    """Switch the in-memory model to another event and return its row."""
    global _active_event_id
    with _lock:
        row = db.get_event(event_id)
        if row is None:
            raise StoreError("That event no longer exists")
        # Load into locals first; only swap live state once it can't fail, so a
        # bad load never leaves the store pointing at a half-cleared model.
        data = db.load_event(event_id)
        _active_event_id = event_id
        _courses.clear(); _courses.update(data["courses"])
        _classes.clear(); _classes.update(data["classes"])
        _competitors.clear(); _competitors.update(data["competitors"])
        _counters.update(data["counters"])
        _apply_event(row)
        return row


def list_series() -> list[dict]:
    return db.all_series()


def create_series(data: dict) -> dict:
    with _lock:
        name = _clean_str(data.get("name"), "Series name", required=True)
        series = {"id": db.next_series_id(), "name": name}
        db.save_series(series)
        return series


def set_event_series(event_id: int, series_id) -> dict:
    """Assign an event to a series (or None to remove it from one)."""
    with _lock:
        row = db.get_event(event_id)
        if row is None:
            raise StoreError("That event no longer exists")
        if series_id is not None and db.get_series(series_id) is None:
            raise StoreError("That series no longer exists")
        row = dict(row)
        row["series_id"] = series_id
        db.save_event(row)
        if event_id == _active_event_id:
            EVENT["series_id"] = series_id
        return row


def _series_points(position) -> int:
    """Series points for a finishing position: 100, 95, 90, ... floored at 0."""
    if position is None:
        return 0
    return max(0, 100 - (position - 1) * 5)


def series_standings(series_id: int) -> dict:
    """
    Cumulative series standings across every event in the series.

    People are identified by SI card number when present, else by name, and earn
    :func:`_series_points` per event by position. Returns the series, its events,
    and standings sorted by total points.
    """
    with _lock:
        events_in = [e for e in db.all_events() if e["series_id"] == series_id]
        people: dict = {}
        for ev in events_in:
            classes, _ = evaluate_event(ev["id"])
            for entry in classes:
                for r in entry["results"]:
                    # Identify a person by name + club, not SI card: cards are
                    # per-event (hire cards, replacements), so a card key would
                    # split one person across rounds. Same-name-same-club
                    # different people is rare and accepted.
                    key = (r["name"].strip().lower(), (r.get("club") or "").strip().lower())
                    person = people.setdefault(key, {
                        "name": r["name"], "club": r.get("club") or "",
                        "points": 0, "events": 0})
                    person["points"] += _series_points(r.get("position"))
                    if r.get("position"):
                        person["events"] += 1
                    person["name"] = r["name"]  # keep most recent display name
        standings = sorted(people.values(),
                           key=lambda p: (-p["points"], p["name"].lower()))
        for i, row in enumerate(standings):
            row["rank"] = i + 1
        return {"series": db.get_series(series_id), "events": events_in,
                "standings": standings}


def competitor_profile(*, card: int | None = None, name: str | None = None) -> dict:
    """A person's results across all events, matched by card number or name."""
    with _lock:
        out = []
        display = name
        for ev in db.all_events():
            classes, _ = evaluate_event(ev["id"])
            for entry in classes:
                for r in entry["results"]:
                    matched = (
                        (card is not None and r.get("card_number") == card)
                        or (name is not None and r["name"].lower() == name.lower()))
                    if matched:
                        display = r["name"]
                        out.append({"event": ev, "class": entry["class"]["name"],
                                    "result": r})
        return {"name": display, "card": card, "results": out}


def _load_active() -> None:
    """Replace the in-memory model with the active event's persisted data."""
    data = db.load_event(_active_event_id)
    _courses.clear(); _courses.update(data["courses"])
    _classes.clear(); _classes.update(data["classes"])
    _competitors.clear(); _competitors.update(data["competitors"])
    _counters.update(data["counters"])


def reload() -> None:
    """Reload the active event from disk (used after a restore)."""
    with _lock:
        _load_active()


def restore(backup_path: str) -> None:
    """
    Replace the database from a backup file, then reload memory -- atomically
    from the operator's point of view (no mutation can interleave the swap).

    Raises StoreError if the file isn't a valid better-meos backup.
    """
    import sqlite3
    with _lock:
        try:
            db.restore_from(backup_path)
        except (ValueError, sqlite3.Error) as err:
            raise StoreError(f"Could not restore backup: {err}")
        _load_active()


def _init() -> None:
    """
    Prepare the store on import.

    On a brand-new database, seed it from the mock roster (preserving the old
    behaviour of having data to show immediately) and persist that seed. On an
    existing database, load the active event back into memory so edits survive
    restarts.
    """
    db.connect()
    fresh = db.is_empty()
    db.save_event(EVENT)
    if fresh:
        _seed()  # _insert_* write through to the database
    else:
        _load_active()
    # Sync EVENT / EVENT_DATE from the persisted active-event row.
    _apply_event(db.get_event(_active_event_id))


_init()
