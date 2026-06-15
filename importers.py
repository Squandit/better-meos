"""
Start-list import.

Reads a roster from a CSV (or normalised rows from any source, e.g. the IOF XML
parser) and creates competitors through the store, resolving each row's class by
name against the existing classes. Rows that can't be placed -- no name, an
unknown class, or a validation error -- are collected and reported rather than
aborting the whole import, so an operator gets a partial load plus a list of what
needs fixing.

CSV is intentionally forgiving about headers: ``name`` (or ``first``+``last``),
``club``/``organisation``, ``class``, ``card``/``si``, ``start``/``starttime``.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

import store


def _int_or_none(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def parse_startlist_csv(text: str) -> list[dict]:
    """Parse CSV text into normalised start-list rows (see module docstring)."""
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        name = row.get("name") or " ".join(
            p for p in (row.get("first"), row.get("last")) if p)
        rows.append({
            "name": name,
            "club": row.get("club") or row.get("organisation") or "",
            "class_name": row.get("class") or row.get("class_name") or "",
            "card_number": _int_or_none(
                row.get("card") or row.get("si") or row.get("cardnumber")),
            "start": row.get("start") or row.get("starttime") or "",
        })
    return rows


def _to_clock(value) -> str:
    """Normalise a start value (datetime or string) to an HH:MM:SS clock string."""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    return str(value)


# A linear course auto-created classes are parked on until the operator sets
# their real controls (Eventor knows the classes, not the course geometry).
PLACEHOLDER_COURSE = "Unassigned — set course"


def _placeholder_course_id() -> int:
    for c in store.course_options():
        if c["name"] == PLACEHOLDER_COURSE:
            return c["id"]
    return store.create_course(
        {"name": PLACEHOLDER_COURSE, "type": "linear", "controls": [1]})["id"]


def import_competitors(rows: list[dict], *, auto_create_classes: bool = False) -> dict:
    """
    Create competitors from normalised rows, matching ``class_name`` to a class.

    With ``auto_create_classes`` (the Eventor path), a class named in the import
    that doesn't exist yet is created automatically on a shared placeholder
    course -- the operator then assigns the real course on the Classes page. This
    mirrors MeOS, which pulls the classes from Eventor and lets you set courses
    after. Returns ``{"created", "classes_created", "skipped"}``; never raises for
    per-row problems.
    """
    class_by_name = {c["name"].lower(): c["id"] for c in store.class_options()}
    created = 0
    classes_created = 0
    skipped = []
    for i, row in enumerate(rows, start=1):
        name = (row.get("name") or "").strip()
        class_name = (row.get("class_name") or "").strip()
        if not name:
            skipped.append({"row": i, "name": "", "reason": "missing name"})
            continue
        class_id = class_by_name.get(class_name.lower())
        if class_id is None and auto_create_classes and class_name:
            try:
                cls = store.create_class(
                    {"name": class_name, "course_id": _placeholder_course_id()})
                class_id = cls["id"]
                class_by_name[class_name.lower()] = class_id
                classes_created += 1
            except store.StoreError as err:
                skipped.append({"row": i, "name": name, "reason": str(err)})
                continue
        if class_id is None:
            skipped.append({"row": i, "name": name,
                            "reason": f"unknown class {class_name!r}"})
            continue
        try:
            store.create_competitor({
                "name": name,
                "club": row.get("club", ""),
                "class_id": class_id,
                "card_number": row.get("card_number"),
                "start": _to_clock(row.get("start")),
            })
            created += 1
        except store.StoreError as err:
            skipped.append({"row": i, "name": name, "reason": str(err)})
    return {"created": created, "classes_created": classes_created, "skipped": skipped}
