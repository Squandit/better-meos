"""
Run-an-event guide: the ordered steps an operator follows, as data.

Rendered two ways -- a full ``/guide`` page and a toggleable sidebar on every
operator page -- both from the same ``STEPS`` list, so there's one source of
truth. Each step links to the page(s) it needs (by Flask endpoint name), resolved
to URLs at render time.

# REPLACE with the OWA operator guide --------------------------------------
# These are sensible defaults that mirror the natural workflow. When the club's
# written guide arrives, edit the STEPS below (title / detail / links); nothing
# else needs to change. Keep each ``endpoint`` a real route name in app.py.
"""

from __future__ import annotations

from flask import url_for

STEPS = [
    {
        "key": "create",
        "title": "Create or open the event",
        "detail": "Start a new event file (name, date, first start, type) or open "
                  "an existing one. You can import a start list here too.",
        "links": [{"label": "Event selection", "endpoint": "start"}],
    },
    {
        "key": "courses",
        "title": "Set up courses",
        "detail": "Enter each course's control sequence (or import IOF XML). Set "
                  "start mode, score limits/formula, or forked variants as needed.",
        "links": [{"label": "Courses", "endpoint": "courses"},
                  {"label": "Import courses", "endpoint": "tools"}],
    },
    {
        "key": "classes",
        "title": "Set up classes",
        "detail": "Create the classes (categories) and point each at its course. "
                  "Choose individual, relay or patrol, and set any entry fee.",
        "links": [{"label": "Classes", "endpoint": "classes"}],
    },
    {
        "key": "competitors",
        "title": "Add competitors",
        "detail": "Import the entry list or add competitors by hand. On-the-day "
                  "entries can be added any time; cards autofill known runners.",
        "links": [{"label": "Competitors", "endpoint": "competitors"},
                  {"label": "Entries / draw", "endpoint": "entries_page"},
                  {"label": "Import start list", "endpoint": "tools"}],
    },
    {
        "key": "startlist",
        "title": "Draw the start list & bibs",
        "detail": "Generate start times (alphabetical, random or club-spread, with "
                  "reserve slots), then assign bib numbers and print start lists.",
        "links": [{"label": "Start-list draw", "endpoint": "entries_page"},
                  {"label": "Print start list / bibs", "endpoint": "tools"}],
    },
    {
        "key": "download",
        "title": "Run the event — download cards",
        "detail": "On the day, download finishing cards (real SI reader, or the "
                  "simulator for practice). Results recompute live.",
        "links": [{"label": "Download", "endpoint": "download"},
                  {"label": "Overview", "endpoint": "overview"}],
    },
    {
        "key": "results",
        "title": "Watch results & splits",
        "detail": "Live standings and the splits matrix; show the projector screen "
                  "and the speaker view; flag anyone still out on course.",
        "links": [{"label": "Results", "endpoint": "results"},
                  {"label": "Splits", "endpoint": "splits"},
                  {"label": "Live screen", "endpoint": "live"},
                  {"label": "Speaker", "endpoint": "speaker_page"}],
    },
    {
        "key": "prizes",
        "title": "Prizes & series",
        "detail": "See the recommended prize winner per course (one per person per "
                  "season) and award them; check the season standings.",
        "links": [{"label": "Prizes", "endpoint": "prizes_page"},
                  {"label": "Series", "endpoint": "series_page"}],
    },
    {
        "key": "publish",
        "title": "Publish & back up",
        "detail": "Export results (IOF XML / PDF), print the prize-giving list, "
                  "share the public results URL, and back up the event file.",
        "links": [{"label": "Import / Export", "endpoint": "tools"}],
    },
]


def steps() -> list[dict]:
    """STEPS with each link's endpoint resolved to a URL for the current request."""
    out = []
    for i, step in enumerate(STEPS, start=1):
        links = []
        for link in step["links"]:
            try:
                href = url_for(link["endpoint"])
            except Exception:
                href = "#"
            links.append({"label": link["label"], "href": href})
        out.append({"number": i, "key": step["key"], "title": step["title"],
                    "detail": step["detail"], "links": links})
    return out
