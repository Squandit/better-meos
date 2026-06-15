"""
Season prize ledger.

The club awards one prize per **course** for the top placement, but a person may
win **only one prize per season** -- so once someone has won, they're skipped and
the prize **cascades** to the next eligible finisher. This module remembers who
has already won (across every event in the season) in its own SQLite file, the
same separate-persistent-DB pattern as ``runners.py``, so the ledger survives
between events and is reset simply by starting a new season label.

A person is identified by SI card when present, else by name+club -- the same
rule ``stages._identity`` uses to match a runner across event files.

Path: env ``BMEOS_PRIZES_DB`` (default ``prizes.db``). The active season label is
a ``config.py`` setting (``prize_season``); blank means "derive from the open
event's year".
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime

import config
import store

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prizes (
    season TEXT NOT NULL,
    person_key TEXT NOT NULL,
    name TEXT,
    club TEXT,
    card_number INTEGER,
    course_name TEXT,
    event_name TEXT,
    event_date TEXT,
    awarded_at TEXT,
    PRIMARY KEY (season, person_key)
);
"""


def _path() -> str:
    return os.environ.get("BMEOS_PRIZES_DB", "prizes.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(path or _path(), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        _conn.commit()
        return _conn


def _c() -> sqlite3.Connection:
    if _conn is None:
        connect()
    assert _conn is not None
    return _conn


def reset_connection() -> None:
    """Drop the cached connection so the next call reopens at the current path
    (used by tests that point ``BMEOS_PRIZES_DB`` at a temp file)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def person_key(name: str, club: str, card_number) -> str:
    """Stable identity for one person: card if present, else name|club."""
    if card_number:
        return f"card:{card_number}"
    return "name:" + (name or "").strip().lower() + "|" + (club or "").strip().lower()


# --- Season -----------------------------------------------------------------

def current_season() -> str:
    """Active season label: the configured one, else the open event's year."""
    label = config.get_str("prize_season").strip()
    if label:
        return label
    iso = store.EVENT.get("date_iso") or ""
    return iso[:4] if len(iso) >= 4 else "default"


def set_season(label: str) -> None:
    config.save({"prize_season": (label or "").strip()})


# --- Ledger -----------------------------------------------------------------

def has_won(season: str, key: str) -> bool:
    with _lock:
        row = _c().execute(
            "SELECT 1 FROM prizes WHERE season = ? AND person_key = ?",
            (season, key)).fetchone()
        return row is not None


def ledger(season: str) -> list[dict]:
    with _lock:
        rows = _c().execute(
            "SELECT * FROM prizes WHERE season = ? ORDER BY awarded_at, name",
            (season,)).fetchall()
        return [dict(r) for r in rows]


def winners_set(season: str) -> set:
    return {r["person_key"] for r in ledger(season)}


def award(season: str, person: dict, course_name: str, event: dict | None = None) -> bool:
    """
    Record a prize for ``person`` (``{name, club, card_number}``) on ``course_name``.

    Returns False (a no-op) if that person already holds a prize this season --
    the PK (season, person_key) enforces the one-per-season rule. ``event``
    defaults to the open event.
    """
    event = event or store.EVENT
    key = person_key(person.get("name", ""), person.get("club", ""),
                     person.get("card_number"))
    with _lock:
        cur = _c().execute(
            "INSERT OR IGNORE INTO prizes "
            "(season, person_key, name, club, card_number, course_name, "
            " event_name, event_date, awarded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (season, key, person.get("name"), person.get("club"),
             person.get("card_number"), course_name,
             event.get("name"), event.get("date_iso"), datetime.now().isoformat()))
        _c().commit()
        return cur.rowcount > 0


def unaward(season: str, key: str) -> None:
    with _lock:
        _c().execute("DELETE FROM prizes WHERE season = ? AND person_key = ?",
                     (season, key))
        _c().commit()


def clear(season: str) -> int:
    """Wipe a whole season's ledger (a hard reset). Returns rows removed."""
    with _lock:
        cur = _c().execute("DELETE FROM prizes WHERE season = ?", (season,))
        _c().commit()
        return cur.rowcount


# --- Recommendation ---------------------------------------------------------

def recommend(course_standings: list[dict], season: str) -> list[dict]:
    """
    For each course, recommend who should get its prize.

    ``course_standings`` is ``[{course_name, rows:[{name, club, card, position},
    ...]}]`` with rows already ranked (OK finishers only). The recommendation is
    the highest-placed finisher whose person hasn't already won this season and
    isn't already recommended for another course at this event (cascade). Returns
    ``[{course_name, recommended, skipped, rows}]`` where ``recommended`` is the
    chosen row (with ``person_key``) or ``None`` if nobody is eligible, and
    ``skipped`` lists the already-won names that were passed over.
    """
    won = winners_set(season)
    recommended_here: set = set()
    out = []
    for course in course_standings:
        rows = []
        for r in course["rows"]:
            key = person_key(r.get("name", ""), r.get("club", ""), r.get("card"))
            rows.append({**r, "person_key": key,
                         "already_won": key in won,
                         "ineligible": key in won or key in recommended_here})
        chosen = None
        skipped = []
        for r in rows:
            if r["already_won"] or r["person_key"] in recommended_here:
                if r["already_won"]:
                    skipped.append(r["name"])
                continue
            chosen = r
            recommended_here.add(r["person_key"])
            break
        out.append({"course_name": course["course_name"], "recommended": chosen,
                    "skipped": skipped, "rows": rows})
    return out
