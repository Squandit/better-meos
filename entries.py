"""
Public registration: entries and the start-list draw.

Entries are pre-event registrations kept in the database (separate from the live
competitor model the operator edits). The :func:`draw_startlist` step converts
not-yet-converted entries into competitors with assigned start times -- the
"automatic start list generation" of order.txt. Duplicate entries are rejected,
and late / on-the-day entries simply get picked up by the next draw.

Confirmation email is delegated to :mod:`notify` (scaffolded); payment is handled
by the route via :mod:`payments` (scaffolded).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import db
import notify
import store
from store import StoreError, parse_clock, format_clock


def _event_id() -> int:
    return store.active_event_id()


def _class_names() -> dict:
    return {c["id"]: c["name"] for c in store.class_options()}


def list_entries() -> list[dict]:
    """All entries for the active event, annotated with class name + status."""
    names = _class_names()
    out = []
    for e in db.all_entries(_event_id()):
        e["class_name"] = names.get(e["class_id"], "")
        e["converted"] = e["competitor_id"] is not None
        out.append(e)
    return out


def create(data: dict) -> dict:
    """Validate, dedupe and persist a public entry; send a confirmation email."""
    name = (data.get("name") or "").strip()
    if not name:
        raise StoreError("Name is required")
    try:
        class_id = int(data.get("class_id"))
    except (TypeError, ValueError):
        raise StoreError("Pick a class")
    if store.get_class(class_id) is None:
        raise StoreError("Pick a valid class")

    card_raw = (str(data.get("card_number")) if data.get("card_number") not in (None, "") else "")
    card = None
    if card_raw.strip():
        if not card_raw.strip().isdigit():
            raise StoreError("SI card number must be a whole number")
        card = int(card_raw.strip())

    club = (data.get("club") or "").strip()
    email = (data.get("email") or "").strip()
    late = bool(data.get("late"))

    # A card already assigned to a competitor (e.g. an imported start list)
    # would be rejected at the draw; surface the clash now, to the entrant.
    if card is not None and store.find_by_card(card) is not None:
        raise StoreError(f"SI card {card} is already registered")

    # Dedupe within the event: a card may be entered once; a name once per class.
    for e in db.all_entries(_event_id()):
        if card is not None and e["card_number"] == card:
            raise StoreError(f"SI card {card} is already entered")
        if e["name"].lower() == name.lower() and e["class_id"] == class_id:
            raise StoreError(f"{name} is already entered in this class")

    entry = {
        "name": name, "club": club, "class_id": class_id, "card_number": card,
        "email": email, "paid": False, "late": late, "competitor_id": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        entry["id"] = db.insert_entry(_event_id(), entry)
    except sqlite3.IntegrityError:
        # Lost a race on the UNIQUE(event_id, card_number) constraint.
        raise StoreError(f"SI card {card} is already entered")
    entry["class_name"] = _class_names().get(class_id, "")
    notify.send_entry_confirmation(entry, store.EVENT)
    return entry


def delete(entry_id: int) -> None:
    if db.get_entry(entry_id) is None:
        raise StoreError("That entry no longer exists")
    db.delete_entry(entry_id)


def mark_paid(entry_id: int) -> None:
    if db.get_entry(entry_id) is None:
        raise StoreError("That entry no longer exists")
    db.update_entry(entry_id, paid=1)


def draw_startlist(first_start, interval_minutes) -> dict:
    """
    Convert not-yet-converted entries into competitors with start times.

    Within each class, entries are ordered alphabetically (deterministic) and
    given start times from ``first_start`` (HH:MM:SS) spaced ``interval_minutes``
    apart. Already-converted entries are skipped, so re-running the draw only
    places new (e.g. late) entries. Returns ``{"created", "skipped"}``.
    """
    first = parse_clock(first_start, "First start")
    if first is None:
        raise StoreError("First start time is required")
    try:
        interval = int(interval_minutes)
    except (TypeError, ValueError):
        raise StoreError("Interval must be a whole number of minutes")
    if interval < 1:
        raise StoreError("Interval must be at least 1 minute")

    pending = [e for e in db.all_entries(_event_id()) if e["competitor_id"] is None]
    by_class: dict[int, list] = {}
    for e in pending:
        by_class.setdefault(e["class_id"], []).append(e)

    created = 0
    skipped = []
    for class_id, members in by_class.items():
        if store.get_class(class_id) is None:
            for e in members:
                skipped.append({"name": e["name"], "reason": "class no longer exists"})
            continue
        members.sort(key=lambda e: e["name"].lower())
        # Continue after any start times already assigned in this class (from an
        # earlier draw), but never before the requested first start. This keeps a
        # re-run for late entries from colliding with the original draw.
        assigned = [c["start"] for c in store._competitors_in_class(class_id)
                    if c["start"] is not None]
        start_time = first
        if assigned:
            start_time = max(first, max(assigned) + timedelta(minutes=interval))
        for e in members:
            try:
                comp = store.create_competitor({
                    "name": e["name"], "club": e["club"] or "",
                    "class_id": class_id, "card_number": e["card_number"],
                    "start": format_clock(start_time),
                })
            except StoreError as err:
                skipped.append({"name": e["name"], "reason": str(err)})
                continue
            db.update_entry(e["id"], competitor_id=comp["id"])
            created += 1
            start_time = start_time + timedelta(minutes=interval)
    return {"created": created, "skipped": skipped}
