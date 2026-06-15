"""
Shared competitor database (the "runners" database).

Lives in its own SQLite file (separate from the per-event files) so it persists
across every event: card number -> name + club, plus a tally of which class each
card enters. That tally powers autofill -- when a known card is read or typed,
the entry form pre-fills the person's name/club and their **usual class** (the
one they enter most often). Starts empty and learns as events run.

Path: env ``BMEOS_RUNNERS_DB`` (default ``runners.db`` next to the events folder).
"""

from __future__ import annotations

import os
import sqlite3
import threading

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runners (
    card_number INTEGER PRIMARY KEY,
    name TEXT,
    club TEXT
);
CREATE TABLE IF NOT EXISTS run_history (
    card_number INTEGER NOT NULL,
    class_name TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (card_number, class_name)
);
CREATE INDEX IF NOT EXISTS idx_runners_name ON runners(name);
"""


def _path() -> str:
    return os.environ.get("BMEOS_RUNNERS_DB", "runners.db")


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


def usual_class(card_number: int) -> str | None:
    """The class this card enters most often, or None if unknown."""
    with _lock:
        row = _c().execute(
            "SELECT class_name FROM run_history WHERE card_number = ? "
            "ORDER BY count DESC, class_name LIMIT 1", (card_number,)).fetchone()
        return row["class_name"] if row else None


def lookup(card_number: int) -> dict | None:
    """A known runner by card: ``{card_number, name, club, usual_class}`` or None."""
    if card_number is None:
        return None
    with _lock:
        row = _c().execute("SELECT * FROM runners WHERE card_number = ?",
                           (card_number,)).fetchone()
        if row is None:
            return None
        return {"card_number": row["card_number"], "name": row["name"] or "",
                "club": row["club"] or "", "usual_class": usual_class(card_number)}


def lookup_by_name(name: str) -> dict | None:
    with _lock:
        row = _c().execute("SELECT * FROM runners WHERE LOWER(name) = ?",
                           ((name or "").strip().lower(),)).fetchone()
        if row is None:
            return None
        return {"card_number": row["card_number"], "name": row["name"] or "",
                "club": row["club"] or "", "usual_class": usual_class(row["card_number"])}


def search(query: str, limit: int = 8) -> list[dict]:
    query = (query or "").strip().lower()
    if not query:
        return []
    with _lock:
        rows = _c().execute(
            "SELECT * FROM runners WHERE LOWER(name) LIKE ? ORDER BY name LIMIT ?",
            (f"%{query}%", limit)).fetchall()
        return [{"card_number": r["card_number"], "name": r["name"] or "",
                 "club": r["club"] or "", "usual_class": usual_class(r["card_number"])}
                for r in rows]


def record(card_number, name: str = "", club: str = "", class_name: str = "") -> None:
    """
    Remember a runner and tally the class they entered. Called whenever a
    competitor is created/classed or a card is downloaded, so the database
    learns each card's name/club and usual class. No-op without a card number.
    """
    if not card_number:
        return
    with _lock:
        db = _c()
        # Upsert identity (keep the latest non-empty name/club).
        existing = db.execute("SELECT name, club FROM runners WHERE card_number = ?",
                              (card_number,)).fetchone()
        new_name = name or (existing["name"] if existing else "")
        new_club = club or (existing["club"] if existing else "")
        db.execute(
            "INSERT INTO runners (card_number, name, club) VALUES (?, ?, ?) "
            "ON CONFLICT(card_number) DO UPDATE SET name = excluded.name, "
            "club = excluded.club",
            (card_number, new_name, new_club))
        if class_name:
            db.execute(
                "INSERT INTO run_history (card_number, class_name, count) VALUES (?, ?, 1) "
                "ON CONFLICT(card_number, class_name) DO UPDATE SET count = count + 1",
                (card_number, class_name))
        db.commit()


def record_competitor(comp: dict) -> None:
    """Learn from a competitor JSON (has card_number, name, club, class_name)."""
    record(comp.get("card_number"), comp.get("name", ""), comp.get("club", ""),
           comp.get("class_name", ""))


def import_csv(text: str) -> dict:
    """
    Seed the runner database from a CSV (``name``/``first``+``last``,
    ``club``/``organisation``, ``card``/``si``, optional ``class``). A given
    ``class`` is counted toward the runner's usual class. The runner DB is keyed
    by SI card, so rows without a card can't be stored -- they're skipped and
    reported separately. Returns ``{"imported": n, "skipped": m}``.
    """
    import csv
    import io
    imported = skipped = 0
    for raw in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        name = row.get("name") or " ".join(
            p for p in (row.get("first") or row.get("firstname"),
                        row.get("last") or row.get("lastname") or row.get("surname")) if p)
        card_raw = (row.get("card") or row.get("si") or row.get("card number")
                    or row.get("sicard") or "")
        card = int(card_raw) if card_raw.isdigit() else None
        if card is None:
            skipped += 1  # no card -> can't key a runner record
            continue
        record(card, name, row.get("club") or row.get("organisation") or "",
               row.get("class") or "")
        imported += 1
    return {"imported": imported, "skipped": skipped}
