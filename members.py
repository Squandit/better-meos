"""
Members database (the "runner database").

A roster of known people with a membership ``type`` (senior / junior /
concession) that drives entry pricing on the public entry page, plus an optional
default club and SI card. Operators import it from a spreadsheet; the entry page
looks people up by name. Backed directly by the ``members`` table in :mod:`db`
(it isn't event-scoped -- the roster persists across events).
"""

from __future__ import annotations

import csv
import io

import db

TYPES = ("senior", "junior", "concession")


def _norm_type(value: str) -> str:
    v = (value or "").strip().lower()
    if "junior" in v or v in ("j", "u21", "u18", "u16"):
        return "junior"
    if "concess" in v or "pension" in v or v in ("c", "conc"):
        return "concession"
    return "senior"


def _int_or_none(value):
    value = str(value or "").strip()
    return int(value) if value.isdigit() else None


def all_members() -> list[dict]:
    return db.all_members()


def search(query: str) -> list[dict]:
    """Up to 8 members whose name contains ``query`` (for the entry autocomplete)."""
    query = (query or "").strip()
    return db.search_members(query) if query else []


def lookup(name: str) -> dict | None:
    """Exact (case-insensitive) member by name."""
    return db.find_member((name or "").strip()) if name else None


def add(data: dict) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Member name is required")
    member = {
        "name": name,
        "club": (data.get("club") or "").strip(),
        "card_number": _int_or_none(data.get("card") or data.get("card_number")),
        "type": _norm_type(data.get("type")),
        "email": (data.get("email") or "").strip(),
    }
    member["id"] = db.insert_member(member)
    return member


def delete(member_id: int) -> None:
    db.delete_member(member_id)


def import_csv(text: str, *, mode: str = "merge") -> dict:
    """
    Import members from CSV. Recognises ``name`` (or ``first``+``last``),
    ``club``/``organisation``, ``card``/``si``, ``type``/``category``, ``email``.

    ``mode`` "replace" overwrites the roster; "merge" adds new names only.
    Returns ``{"imported": n, "total": n}``.
    """
    reader = csv.DictReader(io.StringIO(text))
    parsed = []
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        name = row.get("name") or " ".join(
            p for p in (row.get("first") or row.get("firstname"),
                        row.get("last") or row.get("lastname") or row.get("surname")) if p)
        if not name:
            continue
        parsed.append({
            "name": name,
            "club": row.get("club") or row.get("organisation") or "",
            "card_number": _int_or_none(row.get("card") or row.get("si")
                                        or row.get("card number") or row.get("sicard")),
            "type": _norm_type(row.get("type") or row.get("category")),
            "email": row.get("email") or "",
        })

    if mode == "replace":
        final = parsed
    else:
        existing = db.all_members()
        seen = {m["name"].lower() for m in existing}
        final = existing + [m for m in parsed if m["name"].lower() not in seen]
    db.replace_members(final)
    return {"imported": len(parsed), "total": len(final)}
