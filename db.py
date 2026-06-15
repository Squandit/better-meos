"""
SQLite persistence for the event store.

``store.py`` keeps the live event model in memory (the working set the operator
edits) and uses this module as a write-through backing store: every insert,
update and delete is mirrored here so the data survives a restart. On boot the
store calls :func:`load_event` to repopulate its dicts.

The schema is normalised the way ``order.txt`` asks for it -- ``events``,
``courses`` + ``controls``, ``classes``, ``competitors`` + ``punches``. Results
are *not* stored; they are always recomputed by the ``results`` engine from the
raw punches, so there is a single source of truth.

Ids are assigned by the store (so an in-memory record and its row share one id);
we write them explicitly with ``INSERT OR REPLACE``. Datetimes are stored as ISO
strings and parsed back to ``datetime`` on load, honouring the project rule that
timestamps are ``datetime`` objects in memory, never seconds-since-midnight.

A single connection (``check_same_thread=False``) is shared across the Flask dev
server threads and the SI-reader thread, guarded by ``_lock``.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime

DEFAULT_PATH = os.environ.get("BMEOS_DB", "meos.db")

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    date_iso TEXT NOT NULL,
    reader_port TEXT,
    reader_enabled INTEGER NOT NULL DEFAULT 0,
    slug TEXT,
    series_id INTEGER REFERENCES series(id) ON DELETE SET NULL,
    first_start TEXT,               -- time of first start (HH:MM:SS)
    type TEXT NOT NULL DEFAULT 'linear'  -- default class kind/type for the event
);
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    time_limit_minutes INTEGER,
    penalty_per_minute INTEGER NOT NULL DEFAULT 0,
    start_mode TEXT NOT NULL DEFAULT 'clock',   -- clock | punch | mass | chase
    start_control INTEGER,                       -- start-punch code in punch mode
    length_m INTEGER,                            -- course length (course geometry)
    mass_start TEXT,                             -- HH:MM:SS, the shared time in mass mode
    score_formula TEXT,                          -- optional custom points expression (score)
    variants TEXT                                -- JSON list of alternative control sequences (forking)
);
CREATE TABLE IF NOT EXISTS controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    code INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    points INTEGER,
    leg_length_m INTEGER                         -- length of the leg to this control
);
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    course_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'individual',     -- individual | relay
    legs INTEGER NOT NULL DEFAULT 1,             -- relay leg count
    fee REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    class_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    club TEXT,
    bib INTEGER,
    start TEXT
);
CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    club TEXT,
    class_id INTEGER NOT NULL,
    card_number INTEGER,
    start TEXT,
    finish TEXT,
    manual_status TEXT,
    bib INTEGER,
    hired INTEGER NOT NULL DEFAULT 0,            -- hire/rental card flag
    team_id INTEGER,                             -- relay team membership
    leg INTEGER,                                 -- relay leg number
    time_adjustment INTEGER NOT NULL DEFAULT 0,  -- +/- seconds applied to total
    credit INTEGER NOT NULL DEFAULT 0,           -- seconds subtracted from total
    not_competing INTEGER NOT NULL DEFAULT 0,    -- runs but never ranked
    vacant INTEGER NOT NULL DEFAULT 0,           -- reserved start slot, not a runner
    -- Backs store._check_card_unique at the DB level (NULLs are unconstrained,
    -- so hire-card competitors with no number are allowed).
    UNIQUE (event_id, card_number)
);
CREATE TABLE IF NOT EXISTS punches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_id INTEGER NOT NULL REFERENCES competitors(id) ON DELETE CASCADE,
    code INTEGER NOT NULL,
    time TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    station_id TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',
    club TEXT
);
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    club TEXT,
    card_number INTEGER,
    type TEXT NOT NULL DEFAULT 'senior',
    email TEXT
);
CREATE INDEX IF NOT EXISTS idx_members_name ON members(name);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    club TEXT,
    class_id INTEGER,
    card_number INTEGER,
    email TEXT,
    paid INTEGER NOT NULL DEFAULT 0,
    late INTEGER NOT NULL DEFAULT 0,
    competitor_id INTEGER,
    created_at TEXT,
    -- One real SI card per event (SQLite allows many NULLs, so hire-card
    -- entries with no number are unconstrained). Authoritative guard against a
    -- read-then-insert dedupe race under threaded requests.
    UNIQUE (event_id, card_number)
);
CREATE INDEX IF NOT EXISTS idx_courses_event ON courses(event_id);
CREATE INDEX IF NOT EXISTS idx_entries_event ON entries(event_id);
CREATE INDEX IF NOT EXISTS idx_classes_event ON classes(event_id);
CREATE INDEX IF NOT EXISTS idx_competitors_event ON competitors(event_id);
CREATE INDEX IF NOT EXISTS idx_competitors_card ON competitors(card_number);
CREATE INDEX IF NOT EXISTS idx_punches_competitor ON punches(competitor_id);
"""


# ---------------------------------------------------------------------------
# Connection / lifecycle
# ---------------------------------------------------------------------------

def connect(path: str | None = None) -> sqlite3.Connection:
    """Open (or reopen) the database and ensure the schema exists."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(path or DEFAULT_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
        return _conn


# Columns added after the original schema; applied to pre-existing databases so
# they upgrade in place (CREATE TABLE IF NOT EXISTS never alters an existing table).
_MIGRATIONS = [
    ("events", "first_start", "TEXT"),
    ("events", "type", "TEXT NOT NULL DEFAULT 'linear'"),
    ("courses", "start_mode", "TEXT NOT NULL DEFAULT 'clock'"),
    ("courses", "start_control", "INTEGER"),
    ("courses", "length_m", "INTEGER"),
    ("courses", "mass_start", "TEXT"),
    ("courses", "score_formula", "TEXT"),
    ("controls", "leg_length_m", "INTEGER"),
    ("classes", "kind", "TEXT NOT NULL DEFAULT 'individual'"),
    ("classes", "legs", "INTEGER NOT NULL DEFAULT 1"),
    ("classes", "fee", "REAL NOT NULL DEFAULT 0"),
    ("competitors", "bib", "INTEGER"),
    ("competitors", "hired", "INTEGER NOT NULL DEFAULT 0"),
    ("competitors", "team_id", "INTEGER"),
    ("competitors", "leg", "INTEGER"),
    ("competitors", "time_adjustment", "INTEGER NOT NULL DEFAULT 0"),
    ("competitors", "credit", "INTEGER NOT NULL DEFAULT 0"),
    ("competitors", "not_competing", "INTEGER NOT NULL DEFAULT 0"),
    ("competitors", "vacant", "INTEGER NOT NULL DEFAULT 0"),
    ("courses", "variants", "TEXT"),
]


def _migrate(conn) -> None:
    for table, col, decl in _MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _c() -> sqlite3.Connection:
    if _conn is None:
        connect()
    assert _conn is not None
    return _conn


def path() -> str:
    """Filesystem path of the live database (for backup downloads)."""
    db = _c()
    for _, name, file in db.execute("PRAGMA database_list"):
        if name == "main":
            return file or ""
    return ""


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


# ---------------------------------------------------------------------------
# Backup / restore (consistent snapshots via SQLite's online-backup API)
# ---------------------------------------------------------------------------

def backup_to(dest_path: str) -> None:
    """
    Write a consistent copy of the live database to ``dest_path``.

    Uses ``sqlite3.Connection.backup`` so the snapshot is transactionally
    consistent even while the app keeps serving -- no risk of a torn file copy.
    """
    with _lock:
        dest = sqlite3.connect(dest_path)
        try:
            _c().backup(dest)
        finally:
            dest.close()


def restore_from(src_path: str) -> None:
    """
    Replace the live database's contents with those of ``src_path`` in place.

    Copies the uploaded database *into* the open connection (again via the
    backup API), so there is no closing/reopening of the live file -- which on
    Windows would otherwise risk a "file in use" error. The source is validated
    as a better-meos backup first, so a bad upload can't clobber live data.
    Raises ``ValueError`` if the file isn't a recognisable backup.
    """
    with _lock:
        src = sqlite3.connect(src_path)
        try:
            tables = {
                r[0] for r in src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not {"events", "competitors"} <= tables:
                raise ValueError("not a better-meos backup database")
            src.backup(_c())
        finally:
            src.close()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _json_list(value: str | None) -> list:
    """Parse a JSON list column (course variants); [] for null/garbage."""
    if not value:
        return []
    import json
    try:
        out = json.loads(value)
        return out if isinstance(out, list) else []
    except ValueError:
        return []


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def is_empty() -> bool:
    """True when no event has been created yet (fresh database)."""
    with _lock:
        row = _c().execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return row["n"] == 0


def save_event(event: dict) -> None:
    # UPSERT (not INSERT OR REPLACE): REPLACE deletes the existing row first,
    # which would cascade-delete this event's courses/classes/competitors via
    # their ON DELETE CASCADE foreign keys. Updating in place avoids that.
    with _lock:
        _c().execute(
            """INSERT INTO events
               (id, name, date_iso, reader_port, reader_enabled, slug, series_id,
                first_start, type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name = excluded.name, date_iso = excluded.date_iso,
                 reader_port = excluded.reader_port,
                 reader_enabled = excluded.reader_enabled,
                 slug = excluded.slug, series_id = excluded.series_id,
                 first_start = excluded.first_start, type = excluded.type""",
            (event["id"], event["name"], event["date_iso"], event.get("reader_port"),
             1 if event.get("reader_enabled") else 0, event.get("slug"),
             event.get("series_id"), event.get("first_start"),
             event.get("type", "linear")),
        )
        _c().commit()


def all_events() -> list[dict]:
    with _lock:
        rows = _c().execute("SELECT * FROM events ORDER BY date_iso, id").fetchall()
        return [dict(r) for r in rows]


def get_event(event_id: int) -> dict | None:
    with _lock:
        row = _c().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None


def read_event_meta(path: str) -> dict | None:
    """
    Read the event row from another event file without disturbing the open one.

    Used by the start page to list events in a folder. Returns the event dict
    (id 1) or None if the file isn't a readable better-meos event.
    """
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM events ORDER BY id LIMIT 1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def next_event_id() -> int:
    with _lock:
        row = _c().execute("SELECT MAX(id) AS m FROM events").fetchone()
        return (row["m"] or 0) + 1


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def save_series(series: dict) -> None:
    # UPSERT, not REPLACE: replacing a series row would null out every event's
    # series_id (events.series_id is ON DELETE SET NULL).
    with _lock:
        _c().execute(
            "INSERT INTO series (id, name) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name",
            (series["id"], series["name"]))
        _c().commit()


def all_series() -> list[dict]:
    with _lock:
        rows = _c().execute("SELECT * FROM series ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def get_series(series_id: int) -> dict | None:
    with _lock:
        row = _c().execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
        return dict(row) if row else None


def next_series_id() -> int:
    with _lock:
        row = _c().execute("SELECT MAX(id) AS m FROM series").fetchone()
        return (row["m"] or 0) + 1


# ---------------------------------------------------------------------------
# Courses (+ controls)
# ---------------------------------------------------------------------------

def save_course(event_id: int, course: dict) -> None:
    """Upsert a course and rewrite its control rows from the in-memory record."""
    with _lock:
        db = _c()
        import json
        variants = course.get("variants") or []
        db.execute(
            """INSERT OR REPLACE INTO courses
               (id, event_id, name, type, time_limit_minutes, penalty_per_minute,
                start_mode, start_control, length_m, mass_start, score_formula, variants)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (course["id"], event_id, course["name"], course["type"],
             course["time_limit_minutes"], course["penalty_per_minute"],
             course.get("start_mode", "clock"), course.get("start_control"),
             course.get("length_m"), course.get("mass_start"),
             course.get("score_formula"),
             json.dumps(variants) if variants else None),
        )
        db.execute("DELETE FROM controls WHERE course_id = ?", (course["id"],))
        leg_lengths = course.get("leg_lengths") or []
        if course["type"] == "score":
            for seq, ctl in enumerate(course["controls"]):
                db.execute(
                    "INSERT INTO controls (course_id, code, sequence, points) VALUES (?, ?, ?, ?)",
                    (course["id"], ctl["code"], seq, ctl["points"]),
                )
        else:
            for seq, code in enumerate(course["controls"]):
                length = leg_lengths[seq] if seq < len(leg_lengths) else None
                db.execute(
                    "INSERT INTO controls (course_id, code, sequence, points, leg_length_m) "
                    "VALUES (?, ?, ?, NULL, ?)",
                    (course["id"], code, seq, length),
                )
        db.commit()


def delete_course(course_id: int) -> None:
    with _lock:
        _c().execute("DELETE FROM courses WHERE id = ?", (course_id,))
        _c().commit()


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------

def save_class(event_id: int, cls: dict) -> None:
    with _lock:
        _c().execute(
            "INSERT OR REPLACE INTO classes (id, event_id, name, course_id, kind, legs, fee) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cls["id"], event_id, cls["name"], cls["course_id"],
             cls.get("kind", "individual"), cls.get("legs", 1), cls.get("fee", 0)),
        )
        _c().commit()


def delete_class(class_id: int) -> None:
    with _lock:
        _c().execute("DELETE FROM classes WHERE id = ?", (class_id,))
        _c().commit()


# ---------------------------------------------------------------------------
# Teams (relay)
# ---------------------------------------------------------------------------

def save_team(event_id: int, team: dict) -> None:
    with _lock:
        _c().execute(
            "INSERT OR REPLACE INTO teams (id, event_id, class_id, name, club, bib, start) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (team["id"], event_id, team["class_id"], team["name"], team.get("club"),
             team.get("bib"), _iso(team.get("start"))),
        )
        _c().commit()


def delete_team(team_id: int) -> None:
    with _lock:
        _c().execute("DELETE FROM teams WHERE id = ?", (team_id,))
        _c().commit()


# ---------------------------------------------------------------------------
# Competitors (+ punches)
# ---------------------------------------------------------------------------

def save_competitor(event_id: int, comp: dict) -> None:
    """Upsert a competitor and rewrite its punch rows from the in-memory record."""
    with _lock:
        db = _c()
        db.execute(
            """INSERT OR REPLACE INTO competitors
               (id, event_id, name, club, class_id, card_number, start, finish,
                manual_status, bib, hired, team_id, leg,
                time_adjustment, credit, not_competing, vacant)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (comp["id"], event_id, comp["name"], comp["club"], comp["class_id"],
             comp["card_number"], _iso(comp["start"]), _iso(comp["finish"]),
             comp["manual_status"], comp.get("bib"), 1 if comp.get("hired") else 0,
             comp.get("team_id"), comp.get("leg"),
             int(comp.get("time_adjustment") or 0), int(comp.get("credit") or 0),
             1 if comp.get("not_competing") else 0, 1 if comp.get("vacant") else 0),
        )
        db.execute("DELETE FROM punches WHERE competitor_id = ?", (comp["id"],))
        for seq, p in enumerate(comp["punches"]):
            db.execute(
                "INSERT INTO punches (competitor_id, code, time, sequence, station_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (comp["id"], p["code"], _iso(p["time"]), seq, p.get("station_id")),
            )
        db.commit()


def delete_competitor(comp_id: int) -> None:
    with _lock:
        _c().execute("DELETE FROM competitors WHERE id = ?", (comp_id,))
        _c().commit()


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Users (optional auth)
# ---------------------------------------------------------------------------

def insert_user(username: str, password_hash: str, role: str, club: str | None) -> int:
    with _lock:
        cur = _c().execute(
            "INSERT INTO users (username, password_hash, role, club) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, club))
        _c().commit()
        return cur.lastrowid


def get_user(username: str) -> dict | None:
    with _lock:
        row = _c().execute("SELECT * FROM users WHERE username = ?",
                           (username,)).fetchone()
        return dict(row) if row else None


def count_users() -> int:
    with _lock:
        return _c().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


# ---------------------------------------------------------------------------
# Members (the runner database powering the entry page)
# ---------------------------------------------------------------------------

def insert_member(member: dict) -> int:
    with _lock:
        cur = _c().execute(
            "INSERT INTO members (name, club, card_number, type, email) "
            "VALUES (?, ?, ?, ?, ?)",
            (member["name"], member.get("club"), member.get("card_number"),
             member.get("type", "senior"), member.get("email")))
        _c().commit()
        return cur.lastrowid


def all_members() -> list[dict]:
    with _lock:
        rows = _c().execute("SELECT * FROM members ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def search_members(query: str, limit: int = 8) -> list[dict]:
    with _lock:
        rows = _c().execute(
            "SELECT * FROM members WHERE LOWER(name) LIKE ? ORDER BY name LIMIT ?",
            (f"%{query.lower()}%", limit)).fetchall()
        return [dict(r) for r in rows]


def find_member(name: str) -> dict | None:
    with _lock:
        row = _c().execute("SELECT * FROM members WHERE LOWER(name) = ?",
                           (name.lower(),)).fetchone()
        return dict(row) if row else None


def delete_member(member_id: int) -> None:
    with _lock:
        _c().execute("DELETE FROM members WHERE id = ?", (member_id,))
        _c().commit()


def replace_members(members: list[dict]) -> None:
    with _lock:
        _c().execute("DELETE FROM members")
        for m in members:
            _c().execute(
                "INSERT INTO members (name, club, card_number, type, email) "
                "VALUES (?, ?, ?, ?, ?)",
                (m["name"], m.get("club"), m.get("card_number"),
                 m.get("type", "senior"), m.get("email")))
        _c().commit()


# ---------------------------------------------------------------------------
# Entries (pre-event registrations; converted to competitors at the draw)
# ---------------------------------------------------------------------------

def insert_entry(event_id: int, entry: dict) -> int:
    with _lock:
        cur = _c().execute(
            """INSERT INTO entries
               (event_id, name, club, class_id, card_number, email, paid, late,
                competitor_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, entry["name"], entry.get("club"), entry.get("class_id"),
             entry.get("card_number"), entry.get("email"),
             1 if entry.get("paid") else 0, 1 if entry.get("late") else 0,
             entry.get("competitor_id"), entry.get("created_at")),
        )
        _c().commit()
        return cur.lastrowid


def update_entry(entry_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock:
        _c().execute(f"UPDATE entries SET {cols} WHERE id = ?",
                     (*fields.values(), entry_id))
        _c().commit()


def all_entries(event_id: int) -> list[dict]:
    with _lock:
        rows = _c().execute(
            "SELECT * FROM entries WHERE event_id = ? ORDER BY id", (event_id,))
        return [dict(r) for r in rows]


def get_entry(entry_id: int) -> dict | None:
    with _lock:
        row = _c().execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None


def delete_entry(entry_id: int) -> None:
    with _lock:
        _c().execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        _c().commit()


def load_event(event_id: int) -> dict:
    """
    Read one event's full model back into plain dicts shaped exactly like the
    store's in-memory records, so the store can drop them straight into its maps.

    Returns ``{"courses": {id: ...}, "classes": {id: ...},
    "competitors": {id: ...}, "counters": {"course": n, "class": n,
    "competitor": n}}`` where each counter is the highest id seen in that table,
    so the store can resume its per-kind id sequences without collisions.
    """
    with _lock:
        return _load_event_conn(_c(), event_id)


def load_event_file(path: str) -> dict | None:
    """
    Load another event file's full model without touching the open connection.

    Used by the multi-stage view to combine results across ``.bmeos`` files.
    Returns the same shape as :func:`load_event`, plus ``meta`` (the event row),
    or ``None`` if the file isn't a readable event. The throwaway connection is
    schema-migrated first so files written by an older version still read.
    """
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        try:
            meta = conn.execute(
                "SELECT * FROM events ORDER BY id LIMIT 1").fetchone()
            if meta is None:
                return None
            data = _load_event_conn(conn, meta["id"])
            data["meta"] = dict(meta)
            return data
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _load_event_conn(db: sqlite3.Connection, event_id: int) -> dict:
    """Load one event's model from an arbitrary connection (shared by
    :func:`load_event` and :func:`load_event_file`). The reentrant module lock is
    harmless for the live connection and a throwaway one alike."""
    with _lock:
        courses: dict[int, dict] = {}
        for row in db.execute("SELECT * FROM courses WHERE event_id = ?", (event_id,)):
            ctls = db.execute(
                "SELECT code, points, leg_length_m FROM controls "
                "WHERE course_id = ? ORDER BY sequence",
                (row["id"],),
            ).fetchall()
            if row["type"] == "score":
                controls: list = [{"code": c["code"], "points": c["points"]} for c in ctls]
            else:
                controls = [c["code"] for c in ctls]
            courses[row["id"]] = {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "controls": controls,
                "time_limit_minutes": row["time_limit_minutes"],
                "penalty_per_minute": row["penalty_per_minute"],
                "start_mode": row["start_mode"] or "clock",
                "start_control": row["start_control"],
                "length_m": row["length_m"],
                "mass_start": row["mass_start"],
                "score_formula": row["score_formula"],
                "variants": _json_list(row["variants"]),
                "leg_lengths": [c["leg_length_m"] for c in ctls],
            }

        classes: dict[int, dict] = {}
        for row in db.execute("SELECT * FROM classes WHERE event_id = ?", (event_id,)):
            classes[row["id"]] = {
                "id": row["id"], "name": row["name"], "course_id": row["course_id"],
                "kind": row["kind"] or "individual", "legs": row["legs"] or 1,
                "fee": row["fee"] or 0,
            }

        teams: dict[int, dict] = {}
        for row in db.execute("SELECT * FROM teams WHERE event_id = ?", (event_id,)):
            teams[row["id"]] = {
                "id": row["id"], "class_id": row["class_id"], "name": row["name"],
                "club": row["club"] or "", "bib": row["bib"], "start": _dt(row["start"]),
            }

        competitors: dict[int, dict] = {}
        for row in db.execute("SELECT * FROM competitors WHERE event_id = ?", (event_id,)):
            punches = [
                {"code": p["code"], "time": _dt(p["time"]), "station_id": p["station_id"]}
                for p in db.execute(
                    "SELECT code, time, station_id FROM punches "
                    "WHERE competitor_id = ? ORDER BY sequence",
                    (row["id"],),
                )
            ]
            competitors[row["id"]] = {
                "id": row["id"],
                "name": row["name"],
                "club": row["club"] or "",
                "class_id": row["class_id"],
                "card_number": row["card_number"],
                "start": _dt(row["start"]),
                "finish": _dt(row["finish"]),
                "punches": punches,
                "manual_status": row["manual_status"] or "",
                "bib": row["bib"],
                "hired": bool(row["hired"]),
                "team_id": row["team_id"],
                "leg": row["leg"],
                "time_adjustment": row["time_adjustment"] or 0,
                "credit": row["credit"] or 0,
                "not_competing": bool(row["not_competing"]),
                "vacant": bool(row["vacant"]),
            }

        # Highest id per kind across ALL events (ids are table-wide primary keys),
        # so the store resumes each per-kind sequence without colliding with rows
        # belonging to other events.
        counters = {}
        for kind, table in (("course", "courses"), ("class", "classes"),
                            ("competitor", "competitors"), ("team", "teams")):
            row = db.execute(f"SELECT MAX(id) AS m FROM {table}").fetchone()
            counters[kind] = row["m"] or 0

        return {
            "courses": courses,
            "classes": classes,
            "competitors": competitors,
            "teams": teams,
            "counters": counters,
        }
