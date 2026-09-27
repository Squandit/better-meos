"""
The command palette's search (Ctrl+K in the console): runners by name, card,
bib or club, classes, clubs and settings. Pages and actions are matched in the
browser; this covers what lives in the event and the settings.
"""

from __future__ import annotations

import config
import display
import store


def _rank(text: str, q: str) -> int | None:
    """0 = starts with the query, 1 = a word starts with it, 2 = contains it."""
    text = (text or "").lower()
    if text.startswith(q):
        return 0
    if any(word.startswith(q) for word in text.split()):
        return 1
    return 2 if q in text else None


def query(q: str, limit: int = 8) -> dict:
    q = (q or "").strip().lower()
    if not q:
        return {"runners": [], "classes": [], "clubs": [], "settings": []}
    _, by_id = store.evaluate()
    fmt = display.time_formatter()
    now = store.event_now()

    runners = []
    for comp in store._competitors.values():
        if comp.get("vacant"):
            continue
        card = str(comp.get("card_number") or "")
        bib = str(comp.get("bib") or "")
        rank = _rank(comp["name"], q)
        if q.isdigit() and (card.startswith(q) or bib == q):
            rank = 0 if card == q or bib == q else 1
        if rank is None:
            club_rank = _rank(comp.get("club") or "", q)
            rank = None if club_rank is None else 3
        if rank is None:
            continue
        cls = store.get_class(comp["class_id"]) or {}
        res = by_id.get(comp["id"])
        row = display.view_row(res, fmt, now) if res else {}
        runners.append((rank, comp["name"].lower(), {
            "id": comp["id"], "name": comp["name"], "club": comp.get("club") or "",
            "class": cls.get("name", ""), "card": comp.get("card_number"), "bib": comp.get("bib"),
            "status": row.get("status_label", ""), "time": row.get("time"),
            "position": row.get("position")}))
    runners.sort(key=lambda x: (x[0], x[1]))

    classes = sorted(
        ({"id": c["id"], "name": c["name"]} for c in store._classes.values()
         if _rank(c["name"], q) is not None),
        key=lambda c: (_rank(c["name"], q), c["name"].lower()))
    clubs = sorted({(c.get("club") or "").strip() for c in store._competitors.values()
                    if _rank(c.get("club") or "", q) is not None} - {""},
                   key=lambda name: (_rank(name, q), name.lower()))
    settings = [{"key": f["key"], "label": f["label"], "group": g["group"]}
                for g in config.settings_view(query=q) for f in g["fields"]]
    return {"runners": [r for _, _, r in runners[:limit]], "classes": classes[:limit],
            "clubs": clubs[:limit], "settings": settings[:limit]}
