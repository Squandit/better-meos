import functools
import json
import os
import tempfile
import threading
from datetime import datetime
from xml.etree.ElementTree import ParseError as ET_ERROR
from xml.sax.saxutils import escape as xml_escape

from flask import (Flask, render_template, request, jsonify, abort, Response,
                   make_response, url_for, redirect, session)

import auth
import backups
import config
import dashboard
import db
import display
import draw
import entries as entries_mod
import eventor
import events
import iofxml
import importers
import online_entry
import payments
import pdf
import publish
import remote
import runners
import season
import security
import settings_schema
import si_reader
import speaker
import simulator
import stages
import store
from store import StoreError
from results import build_splits_matrix, format_duration

app = Flask(__name__)

# Session signing key: BMEOS_SECRET, else a random key generated once and kept
# in config.json. Never a fixed default -- the admin lock and logins live in the
# session cookie, so a key anyone can read would let them forge an unlocked one.
app.secret_key = config.secret_key()

# Format a raw seconds duration in templates (for pages that render engine
# results directly rather than pre-formatted view rows), in the event's format.
app.jinja_env.filters["format_secs"] = lambda secs: display.time_formatter()(secs) or ""


def _ordinal(n) -> str:
    """1 -> 1st, 2 -> 2nd, 11 -> 11th, 23 -> 23rd."""
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


app.jinja_env.filters["ordinal"] = _ordinal

# Port-surface split (public vs admin) + admin unlock gate. Installed first so
# its before_request guard runs before the login / open-event guards.
security.install(app)

# Optional login gating (no-op unless BMEOS_AUTH is set). ensure_admin runs
# after an event opens (below) -- never at import, so it can't create a stray DB
# before any event file is connected.
auth.install(app)


# ---------------------------------------------------------------------------
# Rendered-page cache for the read-only result views
# ---------------------------------------------------------------------------
# Live screens and phones reload these pages after every change, all at once,
# and they render identically for everyone until the data changes again. So a
# page is rendered once per db revision (see db.revision) and served from memory
# until the next write; a burst of reloads costs one render, not one each.

_page_cache: dict = {}
_render_locks: dict = {}
_page_cache_revision = None
_page_cache_lock = threading.Lock()
# Bound on cached variants per revision (different paths / query strings), so
# junk query strings can't grow memory without limit.
_PAGE_CACHE_MAX = 200


def _page_key():
    """Everything a cached page's HTML depends on besides the event data."""
    user = auth.current_user() or {}
    return (request.full_path, request.environ.get("SERVER_PORT", ""),
            user.get("username"), user.get("role"), auth.is_enabled(),
            config.admin_password_set(), si_reader.reader_enabled())


def _cached(key, revision):
    with _page_cache_lock:
        return _page_cache.get(key) if _page_cache_revision == revision else None


def cached_page(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        global _page_cache_revision
        # Read the revision BEFORE rendering: the page is then at least as new
        # as its key, never older (a write mid-render just makes the next
        # request miss).
        revision = db.revision()
        key = _page_key()
        with _page_cache_lock:
            if _page_cache_revision != revision:
                _page_cache.clear()
                _render_locks.clear()
                _page_cache_revision = revision
            hit = _page_cache.get(key)
            render_lock = _render_locks.get(key)
            if render_lock is None and len(_render_locks) < _PAGE_CACHE_MAX:
                render_lock = _render_locks[key] = threading.Lock()
        if hit is None and render_lock is None:
            return view(*args, **kwargs)  # cache full this revision: just render
        if hit is None:
            # One render per page per revision: a reload burst waits for the
            # first render instead of every request rendering the same page.
            with render_lock:
                hit = _cached(key, revision)
                if hit is None:
                    resp = make_response(view(*args, **kwargs))
                    if resp.status_code != 200:
                        return resp
                    hit = (resp.get_data(), resp.mimetype)
                    with _page_cache_lock:
                        if _page_cache_revision == revision \
                                and len(_page_cache) < _PAGE_CACHE_MAX:
                            _page_cache[key] = hit
        return Response(hit[0], mimetype=hit[1])
    return wrapper


@app.context_processor
def inject_user():
    return {"current_user": auth.current_user(), "auth_enabled": auth.is_enabled(),
            "admin_lock_enabled": config.admin_password_set(),
            "page_has_settings": config.page_has_settings,
            "appearance": _appearance()}


def _appearance() -> dict:
    """Theme / accent / density / text size for the <html> element."""
    return {"theme": config.get_str("theme"), "accent": config.get_str("accent"),
            "density": config.get_str("density"), "text": config.get_str("text_size")}


# Paths reachable with no event open (the start page + its actions + assets +
# the admin unlock + Settings, which are event-independent).
_NO_EVENT_OK = ("/static/", "/api/events/", "/api/config")


@app.before_request
def _set_actor():
    """Who the audit log credits for changes made by this request."""
    user = auth.current_user()
    if user:
        store.set_actor(user["username"])
    elif security.is_public_path(request.path) and request.path.startswith("/api/online-entry"):
        store.set_actor("online entry")
    else:
        store.set_actor(f"console {request.remote_addr or ''}".strip())


@app.before_request
def _require_open_event():
    """With no event open, every operator page redirects to the start screen
    (and operator APIs answer 409), so the app always begins at event selection."""
    if store.has_open_event():
        return None
    p = request.path
    if (p in ("/start", "/favicon.ico", "/sw.js", "/manifest.json",
              "/unlock", "/lock", "/config", "/login", "/logout")
            or p.startswith(_NO_EVENT_OK)):
        return None
    if p.startswith("/api/"):
        return jsonify({"error": "No event open"}), 409
    return redirect(url_for("start"))


# A representative downloaded card, used as the default body for the reader
# simulator endpoint so a read can be triggered with no payload. Its number
# matches a seeded competitor (Test Runner, M21A), so a bare simulate call lands
# on a real entry. Shape mirrors what the sportident library returns.
MOCK_CARD_DATA = {
    "card_number": 8635918,
    "start": datetime(2026, 5, 17, 9, 27, 8),
    "finish": datetime(2026, 5, 17, 10, 49, 53),
    "check": datetime(2026, 5, 17, 9, 22, 36),
    "clear": None,
    "punches": [
        (138, datetime(2026, 5, 17, 9, 38, 27)),
        (130, datetime(2026, 5, 17, 9, 41, 29)),
        (142, datetime(2026, 5, 17, 10, 5, 12)),
        (155, datetime(2026, 5, 17, 10, 30, 41)),
    ],
}


@app.context_processor
def inject_event():
    """Make the open event's details available to every template."""
    # The real reader is switched on in Settings, not per event file.
    return {"event": {**store.EVENT, "reader_enabled": si_reader.reader_enabled()}}


# ---------------------------------------------------------------------------
# Read pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """The customisable home screen (see dashboard.py)."""
    classes = display.console_data()
    ctx = {"classes": classes, "rows": [r for c in classes for r in c["rows"]],
           "evaluated": store.evaluate()[0]}
    return render_template("overview.html", active="overview",
                           widgets=dashboard.build(ctx), catalogue=dashboard.catalogue())


@app.route("/api/dashboard", methods=["POST"])
def api_dashboard_layout():
    """Save the home screen layout (which widgets, order, width) for this PC."""
    return jsonify({"ok": True, "layout": dashboard.save_layout(_payload().get("layout"))})


@app.route("/api/dashboard/reset", methods=["POST"])
def api_dashboard_reset():
    dashboard.reset_layout()
    return jsonify({"ok": True})


@app.route("/api/dashboard/notes", methods=["POST"])
def api_dashboard_notes():
    text = str(_payload().get("text") or "")
    try:
        config.save({"dashboard_notes": text}, target="event")
    except config.SettingError as err:
        raise StoreError(str(err))
    return jsonify({"ok": True})


def _vacant_rows():
    """Unfilled start slots, shaped like result rows so the competitor list can
    show them (click one to fill it with an on-the-day entry)."""
    rows = []
    for comp in store._competitors.values():
        if not comp.get("vacant"):
            continue
        cls = store.get_class(comp["class_id"]) or {}
        rows.append({"id": comp["id"], "position": None, "name": comp["name"],
                     "club": "", "class": cls.get("name", ""), "si": comp["card_number"],
                     "status": "vacant", "status_label": "Vacant", "is_ok": False,
                     "manual": False, "time": None, "start": display.clock(comp["start"]),
                     "finish": None, "points": None, "missed_control": None, "splits": []})
    return rows


@app.route("/competitors")
def competitors():
    rows = [r for c in display.console_data() for r in c["rows"]]
    rows.extend(_vacant_rows())
    rows.sort(key=lambda r: (r["class"], r["position"] is None, r["position"] or 0, r["name"]))
    return render_template(
        "competitors.html", active="competitors", rows=rows,
        class_options=store.class_options(), course_options=store.course_options(),
        status_options=[{"value": s, "label": display.STATUS_LABELS[s]} for s in display.STATUS_ORDER],
    )


@app.route("/classes")
def classes():
    summary = []
    for c in display.console_data():
        rows = c["rows"]
        cls = store.get_class(c["id"]) or {}
        summary.append({
            "id": c["id"],
            "name": c["name"],
            "type": c["type"].title(),
            "course_id": c["course_id"],
            "course_name": c["course_name"],
            "meta": c["meta"],
            "kind": cls.get("kind", "individual"),
            "legs": cls.get("legs", 1),
            "fee": cls.get("fee", 0),
            "fork_courses": ",".join(str(i) for i in cls.get("fork_courses") or []),
            "restart": cls.get("restart") or "",
            "entries": len(rows),
            "finished": sum(1 for r in rows if r["time"] is not None),
            "flagged": sum(1 for r in rows if r["status"] in display.FLAGGED),
        })
    return render_template(
        "classes.html", active="classes", classes=summary,
        course_options=store.course_options(),
    )


@app.route("/courses")
def courses():
    view = []
    for item in store.courses_with_classes():
        course = item["course"]
        if course["type"] == "score":
            view.append({
                "id": course["id"],
                "name": course["name"],
                "is_score": True,
                "classes": item["classes"],
                "time_limit": course["time_limit_minutes"],
                "penalty": course["penalty_per_minute"],
                "total_pts": store.course_total_points(course),
                "controls": [{"code": c["code"], "pts": c["points"]} for c in course["controls"]],
            })
        else:
            view.append({
                "id": course["id"],
                "name": course["name"],
                "is_score": False,
                "classes": item["classes"],
                "count": len(course["controls"]),
                "max_time": course.get("time_limit_minutes"),
                "controls": [{"seq": i + 1, "code": code} for i, code in enumerate(course["controls"])],
            })
    return render_template("courses.html", active="courses", courses=view)


def _unmatched_with_hints() -> list[dict]:
    """Kept reads plus what we can guess about them: the runner (from the
    runner database) and the classes their punches fit."""
    out = []
    for read in store.unmatched_reads():
        known = runners.lookup(read["card_number"]) or {}
        classes = store.suggest_classes(read["codes"])
        usual = next((c for c in store.class_options()
                      if known.get("usual_class") and c["name"] == known["usual_class"]), None)
        if usual and not any(c["class_id"] == usual["id"] for c in classes):
            classes.insert(0, {"class_id": usual["id"], "name": usual["name"],
                               "reason": "their usual class", "complete": False})
        out.append({**read, "known": known, "suggested": classes})
    return out


@app.route("/download")
def download():
    all_rows = [r for c in display.console_data() for r in c["rows"]]
    rows = [r for r in all_rows if r["finish"] is not None]
    rows.sort(key=lambda r: r["finish"], reverse=True)
    assignable = sorted(all_rows, key=lambda r: r["name"].lower())
    reads = [{"seq": r["seq"], "id": r["competitor_id"],
              "status": (store.result_for(r["competitor_id"]) or {}).get("status")}
             for r in si_reader.recent_reads() if r["competitor_id"]]
    return render_template("download.html", active="download", rows=rows, count=len(rows),
                           unmatched=_unmatched_with_hints(), assignable=assignable,
                           class_options=store.class_options(),
                           readers=si_reader.reader_status(),
                           auto_print=config.get_str("auto_print") or "off",
                           auto_print_choices=settings_schema.BY_KEY["auto_print"].choices,
                           reads=reads, boot=si_reader.BOOT_ID)


@app.route("/readout")
def readout_page():
    """Runner-facing readout screen: big result + OK / mispunch sound per read."""
    return render_template("readout.html", active="download",
                           sound=config.get("readout_sound"),
                           show_place=config.get("readout_show_place"))


@app.route("/api/readout/latest")
def api_readout_latest():
    """The last card read, with the runner's result, for the readout screen."""
    read = si_reader.latest_read()
    if read is None:
        return jsonify(None)
    out = {"seq": read["seq"], "time": read["time"], "ok": read["ok"],
           "card_number": read["card_number"], "station": read["station"]}
    comp = store.get_competitor(read["competitor_id"]) if read["competitor_id"] else None
    result = store.result_for(comp["id"]) if comp else None
    if comp and result:
        row = display.view_row(result)
        out["runner"] = {"id": comp["id"], "name": row["name"], "club": row["club"],
                         "class": row["class"], "status": row["status"],
                         "status_label": row["status_label"], "time": row["time"],
                         "position": row["position"], "missed_control": row["missed_control"],
                         "ignored": row["ignored"], "hired": bool(comp.get("hired")),
                         "card_returned": bool(comp.get("card_returned"))}
    return jsonify(out)


@app.route("/api/card-reads/<int:read_id>/assign", methods=["POST"])
def api_assign_card_read(read_id):
    """Attach a kept (unmatched) card read to a competitor."""
    comp_id = store._as_int(_payload().get("competitor_id"), "Competitor", minimum=1)
    comp = store.assign_card_read(read_id, comp_id)
    events.publish("card_read", action="assign")
    return jsonify({"ok": True, "competitor": comp})


@app.route("/api/card-reads/<int:read_id>/enter", methods=["POST"])
def api_enter_card_read(read_id):
    """Quick entry: add the runner a kept read belongs to and give them the run."""
    comp = store.enter_from_read(read_id, _payload())
    events.publish("card_read", action="enter")
    return jsonify({"ok": True, "competitor": comp})


@app.route("/api/card-reads/<int:read_id>", methods=["DELETE"])
def api_delete_card_read(read_id):
    store.delete_card_read(read_id)
    return jsonify({"ok": True})


@app.route("/results")
@cached_page
def results():
    return render_template("results.html", active="results", classes=display.results_view(),
                           show_splits=config.get("results_show_splits"))


def _splits_data():
    """Per-class splits: a full leg matrix for linear classes, per-runner rows
    for score classes (legs aren't comparable when everyone picks a route)."""
    classes, _ = store.evaluate()
    fmt = display.time_formatter()
    view = []
    for entry in display.ordered(classes, name=lambda e: e["class"]["name"]):
        course = entry["course"]
        cls = entry["class"]
        is_score = course["type"] == "score"
        item = {
            "id": cls["id"],
            "name": cls["name"],
            "meta": store.course_meta(course),
            "is_score": is_score,
        }
        if is_score:
            item["rows"] = [display.view_row(r, fmt) for r in entry["results"]]
            view.append(item)
            continue
        # Forked classes: one splits table per course variant (legs of
        # different forks aren't comparable column by column).
        by_course: dict = {}
        for r in entry["results"]:
            by_course.setdefault(r.get("course_id", course["id"]), []).append(r)
        for course_id in sorted(by_course, key=lambda c: (c != course["id"], c)):
            variant = store.get_course(course_id) or course
            part = dict(item)
            if len(by_course) > 1:
                part["name"] = f'{cls["name"]} · {variant["name"]}'
                part["meta"] = store.course_meta(variant)
            part["matrix"] = build_splits_matrix(
                by_course[course_id], variant["controls"], variant.get("leg_lengths"),
                fmt=fmt)
            part["length_m"] = variant.get("length_m")
            view.append(part)
    return view


@app.route("/splits")
@cached_page
def splits():
    return render_template("splits.html", active="splits", classes=_splits_data())


@app.route("/slip/<int:comp_id>")
def slip(comp_id):
    """Printable finish-chute splits slip for one competitor."""
    comp = store.get_competitor(comp_id)
    if comp is None:
        abort(404)
    slip_data = display.slip_view(comp_id)
    if slip_data is None:
        abort(404)
    return render_template("slip.html", **slip_data,
                           auto_print=request.args.get("print") == "1",
                           thermal=(config.get_str("slip_paper") or "80mm").lower() != "a4")


@app.route("/live")
@cached_page
def live():
    """Projector-friendly live leaderboard (no operator chrome). ``?classes=``
    picks this screen's classes (else the event's live screen setting)."""
    blocks = display.results_view()
    wanted = request.args.get("classes") or config.get_str("live_classes")
    if wanted:
        names = {n.strip().lower() for n in wanted.replace(";", ",").replace("\n", ",").split(",")
                 if n.strip()}
        blocks = [b for b in blocks if b["name"].lower() in names]
    latest = display.latest_finishers(12)
    return render_template(
        "live.html", active="live", classes=blocks,
        title=config.get_str("live_title") or store.EVENT["name"],
        rows=max(1, int(config.get("live_rows") or 8)),
        page_seconds=int(config.get("live_page_seconds") or 0),
        scale=int(config.get_str("live_text_size") or 100) / 100,
        show_clock=config.get("live_show_clock"),
        latest=latest if config.get("live_show_latest") else [],
        fresh={r["id"] for r in latest if r["fresh"]})


def _club_archive():
    """All results grouped by club, each club's rows sorted by class then place."""
    rows = [r for c in display.console_data() for r in c["rows"]]
    clubs: dict[str, list] = {}
    for r in rows:
        clubs.setdefault(r["club"] or "Independent", []).append(r)
    archive = []
    for name in sorted(clubs):
        members = sorted(clubs[name], key=lambda r: (
            r["class"], r["position"] is None, r["position"] or 0, r["name"]))
        archive.append({"club": name, "rows": members})
    return archive


@app.route("/clubs")
@cached_page
def clubs():
    return render_template("clubs.html", active="clubs", clubs=_club_archive())


# ---------------------------------------------------------------------------
# Relay teams, economy, speaker, bib numbers / start-list printing
# ---------------------------------------------------------------------------

@app.route("/teams")
@cached_page
def teams_page():
    return render_template("teams.html", active="teams", classes=store.team_results())


@app.route("/api/teams")
def api_list_teams():
    """Teams in a class (for assigning competitors to relay legs in the editor)."""
    class_id = request.args.get("class_id", type=int)
    teams = store.teams_in_class(class_id) if class_id else []
    return jsonify([{"id": t["id"], "name": t["name"]} for t in teams])


@app.route("/api/teams", methods=["POST"])
def api_create_team():
    team = store.create_team(_payload())
    events.publish("team", action="create")
    return jsonify({"team": team}), 201


@app.route("/api/teams/<int:team_id>", methods=["DELETE"])
def api_delete_team(team_id):
    store.delete_team(team_id)
    events.publish("team", action="delete")
    return jsonify({"ok": True})


@app.route("/economy")
def economy_page():
    return render_template("economy.html", active="economy",
                           economy=store.economy_summary(), event=store.EVENT)


@app.route("/speaker")
@cached_page
def speaker_page():
    """Commentator view: who's out on course, recent finishes."""
    rows = [r for c in display.console_data() for r in c["rows"]]
    out = speaker.out_on_course(store.evaluate()[0])
    finished = [r for r in rows if r["finish"]]
    finished.sort(key=lambda r: r["finish"], reverse=True)
    return render_template("speaker.html", active="speaker",
                           out=out, recent=finished[:12])


@app.route("/api/bibs/assign", methods=["POST"])
def api_assign_bibs():
    count = store.assign_bibs(store._as_int(_payload().get("start", 1), "Start", minimum=1))
    events.publish("competitor", action="bibs")
    return jsonify({"ok": True, "assigned": count})


def _startlist_data():
    """Per-class rows for the start list / bibs, straight from the store."""
    by_class = {}
    for c in store._competitors.values():
        by_class.setdefault(c["class_id"], []).append(c)
    classes = []
    for cls in store._classes_sorted():
        members = sorted(by_class.get(cls["id"], []),
                         key=lambda c: (c["start"] or datetime.max, c["name"].lower()))
        classes.append({
            "name": cls["name"],
            "rows": [{"bib": c.get("bib"), "name": c["name"], "club": c.get("club") or "",
                      "vacant": bool(c.get("vacant")),
                      "card": c.get("card_number") or "",
                      "start": display.clock(c.get("start"))} for c in members],
        })
    return classes


def _starters():
    """Everyone with a start time, in start order (the starter's view)."""
    rows = []
    for cls in _startlist_data():
        for r in cls["rows"]:
            if r["start"]:
                rows.append({**r, "class": cls["name"]})
    rows.sort(key=lambda r: (r["start"], r["class"], r["name"]))
    return rows


@app.route("/starter")
def starter_page():
    """Start clock + who's up now and next, for the start official."""
    return render_template("starter.html")


@app.route("/api/starters")
def api_starters():
    return jsonify({"now": datetime.now().strftime("%H:%M:%S"), "starters": _starters()})


@app.route("/export/startlist.pdf")
def export_startlist_pdf():
    if request.args.get("by") == "time":
        data = pdf.start_list_by_time_pdf(_starters(), store.EVENT)
        return Response(data, mimetype="application/pdf", headers={
            "Content-Disposition":
                f"attachment; filename={store.EVENT['slug']}-starters.pdf"})
    data = pdf.start_list_pdf(_startlist_data(), store.EVENT)
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-startlist.pdf"})


@app.route("/export/bibs.pdf")
def export_bibs_pdf():
    labels = []
    for cls in _startlist_data():
        for r in cls["rows"]:
            labels.append({"bib": r["bib"], "name": r["name"],
                           "club": r["club"], "class": cls["name"]})
    data = pdf.bib_labels_pdf(labels, store.EVENT)
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-bibs.pdf"})


@app.route("/draw")
def draw_page():
    """Start-list draw for chosen classes (random / club separation, vacants)."""
    rows = []
    for cls in store._classes_sorted():
        members = store._competitors_in_class(cls["id"])
        starts = sorted(c["start"] for c in members if c["start"] is not None)
        rows.append({"id": cls["id"], "name": cls["name"],
                     "course": (store.get_course(cls["course_id"]) or {}).get("name", ""),
                     "runners": sum(1 for c in members if not c.get("vacant")),
                     "vacants": sum(1 for c in members if c.get("vacant")),
                     "undrawn": sum(1 for c in members if c["start"] is None),
                     "first": display.clock(starts[0]) if starts else "",
                     "last": display.clock(starts[-1]) if starts else ""})
    return render_template("draw.html", active="draw", classes=rows,
                           first_start=store.EVENT.get("first_start") or "10:00:00")


@app.route("/api/draw", methods=["POST"])
def api_draw():
    data = _payload()
    ids = [store._as_int(i, "Class", minimum=1) for i in (data.get("class_ids") or [])]
    if not ids:
        raise StoreError("Pick at least one class to draw")
    outcome = draw.draw_classes(
        ids, first_start=data.get("first_start"),
        interval_seconds=store._as_int(data.get("interval_seconds"), "Interval", minimum=1),
        method=str(data.get("method") or "club"),
        vacants=store._as_int(data.get("vacants") or 0, "Vacant slots", minimum=0),
        keep_existing=bool(data.get("keep_existing")),
        stagger_shared_courses=bool(data.get("stagger", True)))
    events.publish("competitor", action="draw")
    return jsonify(outcome)


@app.route("/audit")
def audit_page():
    """Every change to the event, newest first."""
    return render_template("audit.html", active="audit", entries=store.audit_log())


def _stage_paths(names) -> list[str]:
    """Stage files chosen by filename, only from the events folder (never an
    arbitrary path from the browser)."""
    files = {e["filename"]: e["path"] for e in store.events_in_folder()}
    return [files[n] for n in (names or []) if n in files]


@app.route("/stages")
def stages_page():
    """Multi-day events: combined standings across stage files + chase starts."""
    events_list = sorted(store.events_in_folder(), key=lambda e: (e["date_iso"], e["name"]))
    chosen = request.args.getlist("stage")
    combined = stages.combined_results(_stage_paths(chosen)) if chosen else None
    return render_template("stages.html", active="stages", events=events_list,
                           chosen=set(chosen), combined=combined)


@app.route("/api/stages/chase", methods=["POST"])
def api_chase_starts():
    data = _payload()
    paths = _stage_paths(data.get("stages"))
    if store.current_event_path() in paths:
        raise StoreError("Pick only the earlier stages, not the one that's open")
    if not paths:
        raise StoreError("Pick the earlier stages to base the chase start on")
    count = stages.apply_chase_starts(paths, data.get("first_start"))
    events.publish("competitor", action="chase")
    return jsonify({"ok": True, "assigned": count})


@app.route("/favicon.ico")
def favicon():
    """Browsers ask for this whatever the page says; answer with the SVG icon."""
    return app.send_static_file("favicon.svg")


@app.route("/tools")
def tools():
    """Import / export console."""
    return render_template("tools.html", active="tools")


@app.route("/public/<slug>")
@cached_page
def public_results(slug):
    """Permanent public, read-only results page (no operator chrome)."""
    if slug != store.EVENT["slug"]:
        abort(404)
    return render_template("public.html", classes=display.results_view(),
                           show_splits=config.get("results_show_splits"))


# ---------------------------------------------------------------------------
# Import (IOF XML courses, CSV / IOF XML start lists)
# ---------------------------------------------------------------------------

def _uploaded_text():
    file = request.files.get("file")
    if file is None:
        raise StoreError("No file uploaded")
    return file.read().decode("utf-8-sig")


@app.route("/api/import/courses", methods=["POST"])
def api_import_courses():
    """Import courses from an IOF XML CourseData file."""
    try:
        courses = iofxml.parse_courses(_uploaded_text())
    except (ValueError, ET_ERROR) as err:
        raise StoreError(f"Could not read course file: {err}")
    with store.batch():
        created = [store.create_course(c)["name"] for c in courses]
    events.publish("course", action="import")
    return jsonify({"created": len(created), "names": created})


@app.route("/api/import/members", methods=["POST"])
def api_import_members():
    """Seed the shared runner database (autofill) from a CSV roster."""
    return jsonify(runners.import_csv(_uploaded_text()))


@app.route("/api/import/results", methods=["POST"])
def api_import_results():
    """Import a whole past event's results (IOF ResultList, e.g. from MeOS):
    runners, their punches and times; missing classes/courses are created."""
    try:
        rows = iofxml.parse_resultlist(_uploaded_text())
    except (ValueError, ET_ERROR) as err:
        raise StoreError(f"Could not read result list: {err}")
    outcome = importers.import_results(rows)
    events.publish("competitor", action="import")
    return jsonify(outcome)


@app.route("/api/import/runners", methods=["POST"])
def api_import_runners():
    """Fill the shared runner database from an IOF CompetitorList (MeOS's
    runner-database export) so entry autofill knows everyone."""
    try:
        rows = iofxml.parse_competitorlist(_uploaded_text())
    except (ValueError, ET_ERROR) as err:
        raise StoreError(f"Could not read competitor list: {err}")
    return jsonify(runners.import_rows(rows))


@app.route("/api/import/eventor", methods=["POST"])
def api_import_eventor():
    """Import competitors from an Eventor IOF XML EntryList file."""
    try:
        rows = eventor.parse_entrylist(_uploaded_text())
    except (ValueError, ET_ERROR) as err:
        raise StoreError(f"Could not read entry list: {err}")
    return jsonify(importers.import_competitors(rows))


@app.route("/api/eventor/fetch", methods=["POST"])
def api_eventor_fetch():
    """Pull entries for an Eventor event straight from the Eventor API."""
    try:
        rows = eventor.fetch_entries(_payload().get("event_id"))
    except eventor.EventorError as err:
        raise StoreError(str(err))
    outcome = importers.import_competitors(rows)
    events.publish("competitor", action="import")
    return jsonify(outcome)


@app.route("/api/radio/punch", methods=["POST"])
def api_radio_punch():
    """Live radio / online-control punch -> intermediate split, broadcast live."""
    data = _payload()
    card = store._as_int(data.get("card_number"), "SI card number", minimum=1)
    code = store._as_int(data.get("code"), "Control code", minimum=1)
    when = store.parse_clock(data.get("time"), "Punch time") or datetime.now()
    comp = store.add_radio_punch(card, code, when, data.get("station_id"))
    events.publish("radio", station_id=data.get("station_id"))
    return jsonify({"ok": True, "competitor_id": comp["id"]})


@app.route("/api/station/push", methods=["POST"])
def api_station_push():
    """
    Receive a card forwarded by a secondary download station (see network.py).

    The primary records it exactly as if read on its own reader -- same matching,
    auto-create and live broadcast -- so a multi-PC setup needs only the primary
    to hold the event file.
    """
    data = _payload()
    card = store.coerce_card(data)
    outcome = si_reader.simulate(card, station_id=card.get("station_id"),
                                 auto_create=bool(data.get("auto")) or None)
    return jsonify(outcome), (200 if outcome.get("ok") else 404)


@app.route("/api/import/startlist", methods=["POST"])
def api_import_startlist():
    """Import a start list from CSV or IOF XML StartList."""
    text = _uploaded_text()
    if text.lstrip().startswith("<"):
        try:
            rows = iofxml.parse_startlist(text)
        except (ValueError, ET_ERROR) as err:
            raise StoreError(f"Could not read start list: {err}")
    else:
        rows = importers.parse_startlist_csv(text)
    outcome = importers.import_competitors(rows)
    events.publish("competitor", action="import")
    return jsonify(outcome)


# ---------------------------------------------------------------------------
# Export (IOF XML results, PDF)
# ---------------------------------------------------------------------------

@app.route("/export/results.xml")
def export_results_xml():
    classes, _ = store.evaluate()
    xml = iofxml.export_results(classes, store.EVENT, courses=store._courses)
    return Response(xml, mimetype="application/xml", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-results.xml"})


@app.route("/export/results.pdf")
def export_results_pdf():
    data = pdf.class_results_pdf(display.results_view(on_course=False), store.EVENT)
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-results.pdf"})


@app.route("/slip/<int:comp_id>.pdf")
def slip_pdf(comp_id):
    result = store.result_for(comp_id)
    if result is None:
        abort(404)
    data = pdf.splits_slip_pdf(display.view_row(result), store.EVENT)
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=slip-{comp_id}.pdf"})


# ---------------------------------------------------------------------------
# Registration (public entry form, entries admin, start-list draw)
# ---------------------------------------------------------------------------

def _entry_config():
    """Public config embedded in the entry page (event, PayPal, prices, clubs)."""
    clubs = sorted({(c.get("club") or "").strip()
                    for c in store._competitors.values()} - {""})
    cfg_clubs = config.get_str("clubs")
    if cfg_clubs:
        clubs = [c.strip() for c in cfg_clubs.split(",") if c.strip()]
    return {
        "event": {"name": store.EVENT["name"],
                  "closeTime": config.get_str("entry_close")},
        "paypal": payments.paypal_config(),
        "prices": payments.prices(),
        "paymentRequired": online_entry.payment_required(),
        "clubs": clubs,
        "networkAddress": None,
    }


@app.route("/enter")
def enter():
    """Public entry page (the ported PayPal PWA). Config is string-injected so
    the page's embedded JS/CSS isn't run through Jinja."""
    path = os.path.join(app.root_path, "templates", "entry.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    # Escape for an HTML <script> context: json.dumps leaves '<','>','&' raw, so a
    # stored club/competitor/event name containing '</script>' would break out
    # (stored XSS, since /submit-entry is public). Encode those as \uXXXX.
    blob = (json.dumps(_entry_config())
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    inject = "<script>window.ENTRY_CONFIG = %s;</script>" % blob
    return Response(html.replace("<!-- CONFIG_INJECT -->", inject), mimetype="text/html")


# --- Entry-page backend (mirrors the Node endpoints, backed by the store) ---

def _classes_xml():
    parts = ["<EntryClasses>"]
    for c in store.class_options():
        parts.append(f'<Class id="{c["id"]}"><Name>{xml_escape(c["name"])}</Name></Class>')
    parts.append("</EntryClasses>")
    return "".join(parts)


def _runner_json(r):
    """Shape a runners-DB row for the entry page (type defaults; class = usual)."""
    return {"name": r["name"], "club": r.get("club") or "",
            "card": r.get("card_number") or "", "type": "senior",
            "class": r.get("usual_class") or ""}


@app.route("/get-classes")
def entry_get_classes():
    return Response(_classes_xml(), mimetype="application/xml")


@app.route("/get-result-classes")
def entry_result_classes():
    return Response(_classes_xml(), mimetype="application/xml")


@app.route("/search-competitors")
def entry_search():
    return jsonify([_runner_json(r) for r in runners.search(request.args.get("q", ""))])


@app.route("/lookup-competitor")
def entry_lookup():
    r = runners.lookup_by_name(request.args.get("name", ""))
    return jsonify(_runner_json(r) if r else None)


@app.route("/api/runners/lookup")
def api_runner_lookup():
    """Operator autofill: a card number -> known name/club + usual class id."""
    card = request.args.get("card", type=int)
    r = runners.lookup(card) if card else None
    if r is None:
        return jsonify(None)
    class_id = next((c["id"] for c in store.class_options()
                     if c["name"] == r.get("usual_class")), None)
    return jsonify({"name": r["name"], "club": r["club"],
                    "card_number": r["card_number"], "class_id": class_id})


@app.route("/check-entered")
def entry_check():
    name = (request.args.get("name") or "").strip().lower()
    entered = any((c["name"].strip().lower() == name) for c in store._competitors.values())
    return jsonify({"entered": entered})


# Online entry (public): the entry page sends its cart here. The server checks
# every entry, prices the cart itself and, when a fee is due, creates the PayPal
# order; entries only become competitors once the payment is captured and
# verified server-side (see online_entry.py). Never trust the browser's total.

def _client_key():
    """Who is asking, for rate limiting. Behind ngrok every request arrives from
    loopback, so use the address ngrok appended (the last X-Forwarded-For hop)."""
    addr = request.remote_addr or ""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded and (addr.startswith("127.") or addr == "::1"):
        return forwarded.split(",")[-1].strip()
    return addr


@app.route("/api/online-entry/order", methods=["POST"])
def api_online_entry_order():
    return jsonify(online_entry.start_order(_payload(), client_key=_client_key()))


@app.route("/api/online-entry/capture", methods=["POST"])
def api_online_entry_capture():
    return jsonify(online_entry.capture_order(_payload().get("orderID"),
                                              client_key=_client_key()))


_STATUS_TO_ENTRY = {"dsq": "dq"}  # entry page uses 'dq'; others map 1:1


@app.route("/get-results")
@cached_page
def entry_results():
    class_id = request.args.get("classId", type=int)
    classes, _ = store.evaluate()
    out = []
    for entry in display.ordered(classes, name=lambda e: e["class"]["name"]):
        cls = entry["class"]
        if class_id and cls["id"] != class_id:
            continue
        comps = []
        for r in entry["results"]:
            if r["status"] == "pending":
                continue   # still out (or not started): no result to show yet
            comps.append({
                "name": r["name"], "club": r.get("club") or "",
                "timeSecs": r["total_seconds"] if r["status"] == "ok" else None,
                "place": r.get("position"),
                "status": _STATUS_TO_ENTRY.get(r["status"], r["status"]),
                "splits": [{"control": s["control"], "time": s["cumulative_seconds"]}
                           for s in r["splits"] if s["control"] != "F"],
            })
        out.append({"className": cls["name"], "clsId": str(cls["id"]), "competitors": comps})
    return jsonify(out)


@app.route("/manifest.json")
def entry_manifest():
    return jsonify({
        "name": store.EVENT["name"], "short_name": "Entry", "start_url": "/enter",
        "display": "standalone", "background_color": "#1a3a2a", "theme_color": "#2d5a3d",
        "icons": [{"src": url_for("static", filename="entry/OWA_LOGO.jpg"),
                   "sizes": "192x192", "type": "image/jpeg"}],
    })


@app.route("/sw.js")
def entry_sw():
    # Served at root so its scope covers the entry page (a /static/ SW couldn't).
    path = os.path.join(app.root_path, "static", "entry", "sw.js")
    with open(path, encoding="utf-8") as f:
        return Response(f.read(), mimetype="application/javascript")


@app.route("/api/entries", methods=["POST"])
def api_create_entry():
    entry = entries_mod.create(_payload())
    payment = payments.create_checkout(
        entry,
        success_url=url_for("enter", _external=True) + "?paid=1",
        cancel_url=url_for("enter", _external=True) + "?cancelled=1",
    )
    events.publish("entry", action="create")
    return jsonify({"entry": entry, "payment": payment}), 201


@app.route("/entries")
def entries_page():
    """Operator view of entries with the start-list draw."""
    return render_template("entries.html", active="entries",
                           entries=entries_mod.list_entries(),
                           orders=online_entry.list_orders())


@app.route("/api/orders/<int:order_id>/reconcile", methods=["POST"])
def api_reconcile_order(order_id):
    """Ask PayPal what happened to an open order and finish it if it was paid."""
    order = online_entry.reconcile_order(order_id)
    return jsonify({"ok": True, "status": order["status"]})


@app.route("/api/orders/<int:order_id>/refunded", methods=["POST"])
def api_order_refunded(order_id):
    online_entry.mark_refunded(order_id)
    return jsonify({"ok": True})


@app.route("/api/entries/<int:entry_id>", methods=["DELETE"])
def api_delete_entry(entry_id):
    entries_mod.delete(entry_id)
    events.publish("entry", action="delete")
    return jsonify({"ok": True})


@app.route("/api/entries/<int:entry_id>/paid", methods=["POST"])
def api_mark_entry_paid(entry_id):
    entries_mod.mark_paid(entry_id)
    events.publish("entry", action="paid")
    return jsonify({"ok": True})


@app.route("/api/entries/draw", methods=["POST"])
def api_draw_startlist():
    data = _payload()
    outcome = entries_mod.draw_startlist(
        data.get("first_start"), data.get("interval_minutes"))
    events.publish("competitor", action="draw")
    return jsonify(outcome)


# ---------------------------------------------------------------------------
# Multi-event: events, series, competitor profiles
# ---------------------------------------------------------------------------

@app.route("/start")
def start():
    """Event selection: open an event file from the folder, or create a new one."""
    return render_template("start.html", events=store.events_in_folder(),
                           folder=store.events_dir(),
                           synced_folder=backups.in_synced_folder(store.events_dir()))


@app.route("/setup")
def setup():
    """Per-event hub shown after opening/creating an event."""
    rows = [r for c in display.console_data() for r in c["rows"]]
    counts = {
        "competitors": len(rows),
        "classes": len(store.class_options()),
        "downloaded": sum(1 for r in rows if r["finish"]),
        "out": sum(1 for r in rows if r["start"] and not r["finish"]),
    }
    return render_template("setup.html", active="setup", counts=counts,
                           remote_url=remote.url(), remote_configured=remote.is_configured(),
                           backup=backups.status(), published=publish.status(),
                           synced_folder=backups.in_synced_folder(store.events_dir()))


def _published_files() -> dict:
    """What publish.py pushes online: a self-contained results page (CSS
    inlined, reloads itself each minute) and the IOF XML results."""
    with open(os.path.join(app.root_path, "static", "style.css"), encoding="utf-8") as f:
        css = f.read()
    with app.test_request_context("/"):
        html = render_template("public.html", classes=display.results_view(), inline_css=css,
                               show_splits=config.get("results_show_splits"),
                               published_at=datetime.now().strftime("%H:%M"))
    classes, _ = store.evaluate()
    xml = iofxml.export_results(classes, store.EVENT, courses=store._courses)
    return {"results.html": html.encode("utf-8"), "results.xml": xml.encode("utf-8")}


publish.set_renderer(_published_files)


@app.route("/api/publish/now", methods=["POST"])
def api_publish_now():
    if not publish.enabled():
        return jsonify({"error": "Set a publish folder or FTP host in Settings first"}), 400
    targets = publish.publish_now()
    if not targets:
        return jsonify({"error": publish.status()["error"] or "Nothing published"}), 400
    return jsonify({"ok": True, "targets": targets})


@app.route("/prizes")
def prizes_page():
    """The prize list under the event's prize rules (see season.py)."""
    return render_template("prizes.html", active="prizes", prizes=season.prizes_for_open_event())


@app.route("/season")
def season_page():
    """Season standings across every event with the same season name."""
    names = season.season_names()
    name = request.args.get("name") or config.get_str("season_name") or (names[0] if names else "")
    return render_template("season.html", active="season", names=names, name=name,
                           table=season.standings(name) if name else None)


@app.route("/export/prizes.pdf")
def export_prizes_pdf():
    """Prize-giving list, under the same rules as the Prizes page."""
    places = max(1, int(config.get("prize_places") or 3))
    classes = [{"name": c["class"], "is_score": c["is_score"],
                "rows": [dict(w, position=w["place"]) for w in c["winners"]]}
               for c in season.prizes_for_open_event()["classes"]]
    data = pdf.prize_list_pdf([c for c in classes if c["rows"]], store.EVENT, places)
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-prizes.pdf"})


@app.route("/api/backups/now", methods=["POST"])
def api_backup_now():
    written = backups.backup_now()
    if not written:
        return jsonify({"error": backups.status()["error"] or "Nothing to back up"}), 400
    return jsonify({"ok": True, "files": written})


@app.route("/api/events/new", methods=["POST"])
def api_new_event():
    """Create + open a new event file. Optional entries file imported after."""
    data = request.form.to_dict() if request.form else _payload()
    event = store.new_event(data)
    file = request.files.get("entries")
    imported = None
    if file is not None and file.filename:
        text = file.read().decode("utf-8-sig")
        if text.lstrip().startswith("<"):
            rows = iofxml.parse_startlist(text)
        else:
            rows = importers.parse_startlist_csv(text)
        imported = importers.import_competitors(rows)
    auth.ensure_admin()  # seed the admin into the now-open event file (if auth on)
    si_reader.start_all()  # no-op unless a real reader is configured
    return jsonify({"event": event, "imported": imported}), 201


@app.route("/api/events/open", methods=["POST"])
def api_open_event():
    store.open_event(_payload().get("path", ""))
    auth.ensure_admin()
    si_reader.start_all()  # no-op unless a real reader is configured
    return jsonify({"ok": True})


@app.route("/api/events/close", methods=["POST"])
def api_close_event():
    store.close_event()
    return jsonify({"ok": True})


@app.route("/api/remote/start", methods=["POST"])
def api_remote_start():
    """Open an ngrok tunnel so the entry page is reachable on mobile data.

    Tunnels the public port (where the entry form + results live), not the admin
    console port."""
    port = config.public_port()
    try:
        return jsonify({"url": remote.start(port)})
    except Exception as err:
        return jsonify({"error": f"Could not start remote hosting: {err}"}), 500


@app.route("/api/remote/stop", methods=["POST"])
def api_remote_stop():
    remote.stop()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Operator API (JSON)
# ---------------------------------------------------------------------------

def _payload():
    """Parsed JSON body, or {} for an empty/non-JSON request."""
    return request.get_json(silent=True) or {}


@app.errorhandler(StoreError)
def _handle_store_error(err):
    return jsonify({"error": str(err)}), 400


@app.errorhandler(online_entry.EntryError)
def _handle_entry_error(err):
    return jsonify({"error": str(err), "code": err.code}), 400


# --- Competitors -----------------------------------------------------------

@app.route("/api/competitors", methods=["POST"])
def api_create_competitor():
    comp = store.create_competitor(_payload())
    runners.record_competitor(comp)  # learn this person + their usual class
    result = store.result_for(comp["id"])
    events.publish("competitor", action="create", id=comp["id"])
    return jsonify({"competitor": comp, "result": display.result_view(result) if result else None}), 201


@app.route("/api/competitors/<int:comp_id>", methods=["GET"])
def api_get_competitor(comp_id):
    comp = store.get_competitor(comp_id)
    if comp is None:
        abort(404)
    result = store.result_for(comp_id)
    return jsonify({
        "competitor": store.competitor_json(comp),
        "result": display.result_view(result) if result else None,
    })


@app.route("/api/competitors/<int:comp_id>", methods=["PUT", "PATCH"])
def api_update_competitor(comp_id):
    comp = store.update_competitor(comp_id, _payload())
    result = store.result_for(comp_id)
    events.publish("competitor", action="update", id=comp_id)
    return jsonify({"competitor": comp, "result": display.result_view(result) if result else None})


@app.route("/api/competitors/<int:comp_id>", methods=["DELETE"])
def api_delete_competitor(comp_id):
    store.delete_competitor(comp_id)
    events.publish("competitor", action="delete", id=comp_id)
    return jsonify({"ok": True})


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Evaluate an unsaved competitor payload so the editor can show the effect."""
    result = store.preview(_payload())
    return jsonify({"result": display.result_view(result)})


# --- Classes ---------------------------------------------------------------

@app.route("/api/classes", methods=["POST"])
def api_create_class():
    cls = store.create_class(_payload())
    events.publish("class", action="create", id=cls["id"])
    return jsonify({"class": cls}), 201


@app.route("/api/classes/<int:class_id>", methods=["PUT", "PATCH"])
def api_update_class(class_id):
    cls = store.update_class(class_id, _payload())
    events.publish("class", action="update", id=class_id)
    return jsonify({"class": cls})


@app.route("/api/classes/<int:class_id>/forks", methods=["POST"])
def api_assign_forks(class_id):
    """Hand out the class's fork courses to its runners / relay legs."""
    count = store.assign_forks(class_id)
    events.publish("class", action="forks", id=class_id)
    return jsonify({"ok": True, "assigned": count})


@app.route("/api/classes/<int:class_id>", methods=["DELETE"])
def api_delete_class(class_id):
    store.delete_class(class_id)
    events.publish("class", action="delete", id=class_id)
    return jsonify({"ok": True})


# --- Courses ---------------------------------------------------------------

@app.route("/api/courses", methods=["POST"])
def api_create_course():
    course = store.create_course(_payload())
    events.publish("course", action="create", id=course["id"])
    return jsonify({"course": course}), 201


@app.route("/api/courses/<int:course_id>", methods=["GET"])
def api_get_course(course_id):
    course = store.get_course(course_id)
    if course is None:
        abort(404)
    return jsonify({"course": store.course_json(course)})


@app.route("/api/courses/<int:course_id>", methods=["PUT", "PATCH"])
def api_update_course(course_id):
    course = store.update_course(course_id, _payload())
    events.publish("course", action="update", id=course_id)
    return jsonify({"course": course})


@app.route("/api/courses/<int:course_id>", methods=["DELETE"])
def api_delete_course(course_id):
    store.delete_course(course_id)
    events.publish("course", action="delete", id=course_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Live updates (Server-Sent Events)
# ---------------------------------------------------------------------------

@app.route("/api/stream")
def api_stream():
    """
    SSE feed: pushes a line whenever the event data changes.

    Each open stream holds one server worker thread, so streams are capped per
    port (``events.MAX_STREAMS``); a browser over the cap gets 503 and live.js
    falls back to polling ``/api/version``. A keep-alive comment every
    ``events.KEEPALIVE`` seconds makes a closed tab fail its write, which frees
    the thread instead of holding it until the next publish.
    """
    port = request.environ.get("SERVER_PORT", "")
    q = events.subscribe(port)
    if q is None:
        return jsonify({"error": "Too many live connections; polling instead"}), 503

    def gen():
        try:
            # An initial comment opens the stream immediately for the browser.
            yield ": connected\n\n"
            while True:
                payload = events.next_message(q)
                yield f"data: {payload}\n\n" if payload else ": ping\n\n"
        finally:
            events.unsubscribe(q, port)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/version")
def api_version():
    """A counter that bumps on every change (live.js polls it when it can't
    hold a stream open)."""
    return jsonify({"version": events.version()})


# ---------------------------------------------------------------------------
# SI reader (simulated download for hardware-free testing)
# ---------------------------------------------------------------------------

@app.route("/api/reader/simulate", methods=["POST"])
def api_reader_simulate():
    """
    Simulate a card download. A different random person from the built-in pool
    walks up and downloads each time (creating an on-the-day entry if needed),
    so the whole flow works with no real data and no hardware. With an explicit
    JSON card body it reads exactly that card instead (used by tests).
    """
    data = _payload()
    if data:
        card = store.coerce_card(data)
        auto = bool(data.get("auto"))
        # On a secondary station process_card forwards the read to the primary
        # (this instance may not even have an event open).
        outcome = si_reader.simulate(card, station_id=card.get("station_id"),
                                     auto_create=auto or None)
        if outcome.get("push_failed"):
            return jsonify(outcome), 502
        return jsonify(outcome), (200 if outcome.get("ok") else 404)
    outcome = simulator.simulate_one()
    return jsonify(outcome)


# ---------------------------------------------------------------------------
# Backup / restore (the whole SQLite database)
# ---------------------------------------------------------------------------

@app.route("/api/backup")
def api_backup():
    """Download a consistent snapshot of the SQLite database as a backup."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db.backup_to(tmp)
        with open(tmp, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return Response(
        data, mimetype="application/x-sqlite3",
        headers={"Content-Disposition":
                 f"attachment; filename={store.EVENT['slug']}-backup.db"},
    )


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """Replace the database with an uploaded backup, then reload into memory."""
    file = request.files.get("file")
    if file is None:
        return jsonify({"error": "No backup file uploaded"}), 400
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        file.save(tmp)
        store.restore(tmp)  # validates, swaps in place, reloads (raises StoreError)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    events.publish("restore")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Auth (login / logout) — active only when BMEOS_AUTH is set
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = auth.verify(request.form.get("username"), request.form.get("password"))
        if user:
            auth.login_user(user)
            return redirect(security.safe_next(request.args.get("next"), url_for("index")))
        return render_template("login.html", error="Invalid username or password"), 401
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    auth.logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Admin unlock (single shared password) + the Settings dashboard
# ---------------------------------------------------------------------------

@app.route("/unlock", methods=["GET", "POST"])
def unlock():
    """Password prompt for the operator console (active only when an admin
    password is set in Settings). The unlock lives in a day-long session."""
    if not config.admin_password_set():
        return redirect(url_for("index"))
    nxt = security.safe_next(request.args.get("next"), url_for("index"))
    if request.method == "POST":
        if config.check_admin_password(request.form.get("password", "")):
            session.clear()  # fresh session on privilege change
            session.permanent = True
            session["admin_ok"] = True
            return redirect(security.safe_next(request.form.get("next"), nxt))
        return render_template("unlock.html", error="Incorrect password", next=nxt), 401
    return render_template("unlock.html", error=None, next=nxt)


@app.route("/lock")
def lock():
    session.pop("admin_ok", None)
    return redirect(url_for("unlock"))


@app.route("/config")
def config_page():
    """The master Settings page: every setting, searchable, for this event or
    as the defaults kept on this computer."""
    target = "computer" if request.args.get("target") == "computer" \
        or not store.has_open_event() else "event"
    return render_template("config.html", active="config", target=target,
                           groups=config.settings_view(target=target),
                           event_open=store.has_open_event())


def _settings_response(target: str, page: str | None = None, query: str = ""):
    return jsonify({"groups": config.settings_view(page=page, target=target, query=query),
                    "target": target, "event_open": store.has_open_event(),
                    "event_name": store.EVENT.get("name", "")})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    target = request.args.get("target") or "event"
    if target not in ("event", "computer"):
        raise StoreError("target must be event or computer")
    return _settings_response(target, request.args.get("page") or None,
                              request.args.get("q", ""))


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = _payload()
    target = data.get("target") or "auto"
    if target not in ("auto", "event", "computer"):
        raise StoreError("target must be auto, event or computer")
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    reset = data.get("reset") if isinstance(data.get("reset"), list) else []
    try:
        config.save(values, target=target, reset=[str(k) for k in reset])
    except config.SettingError as err:
        raise StoreError(str(err))
    _after_settings_change()
    return jsonify({"ok": True})


def _after_settings_change():
    """Apply settings that take effect straight away (the reader on/off)."""
    if si_reader.reader_enabled():
        if store.has_open_event():
            si_reader.start_all()
    else:
        si_reader.stop()


@app.route("/api/serial-ports")
def api_serial_ports():
    """COM ports on this PC, for picking the SI station in Settings."""
    return jsonify(si_reader.serial_ports())


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify({"groups": config.dashboard_values()})


@app.route("/api/config", methods=["POST"])
def api_save_config():
    try:
        config.save(_payload())
    except config.SettingError as err:
        raise StoreError(str(err))
    # Reader on/off applies straight away (ports and LAN access need a restart).
    _after_settings_change()
    return jsonify({"ok": True,
                    "note": "Port and network changes take effect after a restart."})


# ---------------------------------------------------------------------------
# Offline sync (scaffold: export/import of the whole database)
# ---------------------------------------------------------------------------
# Full multi-node sync (conflict resolution, change logs) is out of scope; the
# local SQLite database already makes the app fully offline-capable. These
# endpoints expose backup/restore under sync-friendly names so a future sync
# agent (or a manual round-trip between a field laptop and a base) has a seam.

@app.route("/api/sync/export")
def api_sync_export():
    return api_backup()


@app.route("/api/sync/import", methods=["POST"])
def api_sync_import():
    return api_restore()


if __name__ == "__main__":
    # Card data is persisted in SQLite by the ``store`` module (loaded on import).
    # The real SI reader only starts if it's enabled in Settings (BMEOS_READER);
    # otherwise reads come from POST /api/reader/simulate. The reloader would
    # start the thread twice, so only start it in the main process.
    # With the debug reloader on, the serving process is the one where Werkzeug
    # sets WERKZEUG_RUN_MAIN; start the reader only there so it isn't launched
    # twice. (No-op anyway unless the event has the reader enabled.)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        si_reader.start_all()
        backups.start()
        publish.start()
    # The dev server is single-port (the full admin surface); the port split is
    # a launcher/production concern -- run launcher.py to serve both ports.
    # threaded=True so a long-lived SSE stream doesn't block other requests.
    # Loopback only: the dev server has the debugger on and no port split.
    app.run(host="127.0.0.1", port=config.admin_port(), debug=True, threaded=True)
