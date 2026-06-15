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

import random
import sqlite3
from datetime import datetime, timedelta

import db
import notify
import store
from store import StoreError, parse_clock, format_clock

DRAW_METHODS = ("alpha", "random", "club_spread")


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


def _club_spread(members: list[dict]) -> list[dict]:
    """Order so the same club isn't drawn back-to-back where possible: repeatedly
    take from the club with the most remaining entries that isn't the last one
    placed. Starts from an alphabetical order so the result is deterministic."""
    groups: dict[str, list] = {}
    for e in sorted(members, key=lambda e: e["name"].lower()):
        groups.setdefault((e["club"] or "").strip().lower(), []).append(e)
    remaining = {k: list(v) for k, v in groups.items()}
    out: list[dict] = []
    last = None
    total = sum(len(v) for v in remaining.values())
    while len(out) < total:
        avail = [k for k, v in remaining.items() if v]
        choices = [k for k in avail if k != last] or avail
        k = max(choices, key=lambda c: len(remaining[c]))
        out.append(remaining[k].pop(0))
        last = k
    return out


def _ordered(members: list[dict], method: str, rng: random.Random) -> list[dict]:
    if method == "random":
        shuffled = members[:]
        rng.shuffle(shuffled)
        return shuffled
    if method == "club_spread":
        return _club_spread(members)
    return sorted(members, key=lambda e: e["name"].lower())  # alpha (default)


def draw_startlist(first_start, interval_minutes, method="alpha",
                   vacancy_every=0, seed=None) -> dict:
    """
    Convert not-yet-converted entries into competitors with start times.

    Within each class, entries are ordered by ``method`` -- ``alpha`` (default,
    deterministic), ``random``, or ``club_spread`` (avoid same-club adjacency) --
    and given start times from ``first_start`` (HH:MM:SS) spaced
    ``interval_minutes`` apart. ``vacancy_every`` (>0) leaves an empty reserve
    slot after every that-many real starts. Already-converted entries are
    skipped, so re-running only places new (e.g. late) entries. ``seed`` makes a
    ``random`` draw reproducible (tests). Returns ``{"created", "skipped",
    "vacancies"}``.
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
    method = (method or "alpha").strip().lower()
    if method not in DRAW_METHODS:
        raise StoreError("Draw method must be alpha, random or club_spread")
    try:
        vacancy_every = int(vacancy_every or 0)
    except (TypeError, ValueError):
        raise StoreError("Reserve-slot interval must be a whole number")
    rng = random.Random(seed)

    pending = [e for e in db.all_entries(_event_id()) if e["competitor_id"] is None]
    by_class: dict[int, list] = {}
    for e in pending:
        by_class.setdefault(e["class_id"], []).append(e)

    created = 0
    vacancies = 0
    skipped = []
    for class_id, members in by_class.items():
        if store.get_class(class_id) is None:
            for e in members:
                skipped.append({"name": e["name"], "reason": "class no longer exists"})
            continue
        members = _ordered(members, method, rng)
        # Continue after any start times already assigned in this class (from an
        # earlier draw), but never before the requested first start. This keeps a
        # re-run for late entries from colliding with the original draw.
        assigned = [c["start"] for c in store._competitors_in_class(class_id)
                    if c["start"] is not None]
        start_time = first
        if assigned:
            start_time = max(first, max(assigned) + timedelta(minutes=interval))
        placed = 0
        for e in members:
            # Leave a reserve/vacant slot after every N real starts in this class.
            if vacancy_every and placed and placed % vacancy_every == 0:
                start_time = start_time + timedelta(minutes=interval)
                vacancies += 1
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
            placed += 1
            start_time = start_time + timedelta(minutes=interval)
    return {"created": created, "skipped": skipped, "vacancies": vacancies}
