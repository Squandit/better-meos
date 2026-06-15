import json
import logging
import os
import tempfile
from datetime import datetime
from xml.etree.ElementTree import ParseError as ET_ERROR
from xml.sax.saxutils import escape as xml_escape

from flask import (Flask, render_template, request, jsonify, abort, Response,
                   url_for, redirect, session)

import auth
import config
import db
import entries as entries_mod
import eventor
import events
import iofxml
import importers
import network
import notify
import payments
import pdf
import prizes
import remote
import runners
import security
import series
import si_reader
import simulator
import stages
import store
from store import StoreError
from results import build_splits_matrix, format_duration

app = Flask(__name__)

# Session signing key. A known default is fine with auth OFF (sessions carry
# nothing sensitive), but with auth ON it would let anyone forge an operator
# cookie -- so fail closed: use a strong per-process random key when BMEOS_SECRET
# isn't supplied (sessions won't survive a restart; set BMEOS_SECRET to persist).
_secret = os.environ.get("BMEOS_SECRET")
if not _secret:
    if auth.is_enabled():
        _secret = os.urandom(32).hex()
        logging.getLogger("app").warning(
            "BMEOS_AUTH is on without BMEOS_SECRET; using an ephemeral session "
            "key (logins won't survive a restart). Set BMEOS_SECRET to persist.")
    else:
        _secret = "dev-insecure-key"
app.secret_key = _secret

# Format a raw seconds duration in templates (used by the profile page, which
# renders engine results directly rather than pre-formatted view rows).
app.jinja_env.filters["format_secs"] = format_duration

# Port-surface split (public vs admin) + admin unlock gate. Installed first so
# its before_request guard runs before the login / open-event guards.
security.install(app)

# Optional login gating (no-op unless BMEOS_AUTH is set). ensure_admin runs
# after an event opens (below) -- never at import, so it can't create a stray DB
# before any event file is connected.
auth.install(app)


@app.context_processor
def inject_user():
    return {"current_user": auth.current_user(), "auth_enabled": auth.is_enabled(),
            "admin_lock_enabled": config.admin_password_set()}


# Paths reachable with no event open (the start page + its actions + assets +
# the admin unlock + Settings, which are event-independent).
_NO_EVENT_OK = ("/static/", "/api/events/", "/api/config")


@app.before_request
def _require_open_event():
    """With no event open, every operator page redirects to the start screen
    (and operator APIs answer 409), so the app always begins at event selection."""
    if store.has_open_event():
        return None
    p = request.path
    if (p in ("/start", "/favicon.ico", "/sw.js", "/manifest.json",
              "/unlock", "/lock", "/config", "/series", "/profile")
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


# Result status codes -> short display labels, and the order to list them in.
STATUS_LABELS = {
    "ok": "OK",
    "mp": "MP",
    "dns": "DNS",
    "dnf": "DNF",
    "dsq": "DSQ",
    "oot": "OOT",
}
STATUS_ORDER = ["ok", "oot", "mp", "dnf", "dns", "dsq"]
FLAGGED = ("mp", "dnf", "dns", "dsq")


@app.context_processor
def inject_event():
    """Make the open event's details available to every template."""
    return {"event": store.EVENT}


def _status_label(status):
    return STATUS_LABELS.get(status, status.upper() if status else "")


def _format_splits(splits):
    """Format raw split rows (seconds) into display strings."""
    return [
        {
            "control": row["control"],
            "leg": format_duration(row["leg_seconds"]),
            "cumulative": format_duration(row["cumulative_seconds"]),
        }
        for row in splits
    ]


def _clock(dt):
    """Wall-clock time string, or None if the punch is missing."""
    return dt.strftime("%H:%M:%S") if dt else None


def _view_row(result):
    """Shape one engine result into the fields the list/table templates need."""
    total = result["total_seconds"]
    return {
        "id": result["id"],
        "position": result["position"],
        "name": result["name"],
        "club": result["club"],
        "class": result["class"],
        "si": result.get("card_number"),
        "status": result["status"],
        "status_label": _status_label(result["status"]),
        "is_ok": result["status"] == "ok",
        "manual": result.get("manual", False),
        "time": format_duration(total) if total is not None else None,
        "start": _clock(result.get("start")),
        "finish": _clock(result.get("finish")),
        "points": result["points"],
        "missed_control": result.get("missed_control"),
        "splits": _format_splits(result["splits"]),
    }


def _result_view(result):
    """Compact evaluation summary used by the editor's live preview / detail."""
    total = result["total_seconds"]
    return {
        "status": result["status"],
        "status_label": _status_label(result["status"]),
        "auto_status": result.get("auto_status"),
        "auto_status_label": _status_label(result.get("auto_status")),
        "manual": result.get("manual", False),
        "is_ok": result["status"] == "ok",
        "course_type": result.get("course_type"),
        "time": format_duration(total) if total is not None else None,
        "points": result["points"],
        "missed_control": result.get("missed_control"),
        "position": result.get("position"),
        "splits": _format_splits(result["splits"]),
    }


def _console_data():
    """Build every class with its ranked, display-ready rows."""
    classes, _ = store.evaluate()
    view = []
    for entry in classes:
        course = entry["course"]
        view.append({
            "id": entry["class"]["id"],
            "name": entry["class"]["name"],
            "course": course,
            "course_id": course["id"],
            "course_name": course["name"],
            "type": course["type"],
            "is_score": course["type"] == "score",
            "meta": store.course_meta(course),
            "rows": [_view_row(r) for r in entry["results"]],
        })
    return view


# ---------------------------------------------------------------------------
# Read pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    classes = _console_data()
    all_rows = [r for c in classes for r in c["rows"]]
    finished = [r for r in all_rows if r["time"] is not None]

    counts = {}
    for r in all_rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    status_breakdown = [
        {"status": s, "label": STATUS_LABELS[s], "count": counts[s]}
        for s in STATUS_ORDER if counts.get(s)
    ]

    latest = sorted(finished, key=lambda r: r["finish"], reverse=True)[:6]

    leaders = []
    for c in classes:
        top = next((r for r in c["rows"] if r["position"] == 1), None)
        if top:
            leaders.append({"class": c["name"], "is_score": c["is_score"], "row": top})

    stats = {
        "competitors": len(all_rows),
        "classes": len(classes),
        "finished": len(finished),
        "flagged": sum(1 for r in all_rows if r["status"] in FLAGGED),
        "awaiting": len(all_rows) - len(finished),
    }
    return render_template(
        "overview.html", active="overview", stats=stats,
        status_breakdown=status_breakdown, latest=latest, leaders=leaders,
        reader_enabled=store.EVENT.get("reader_enabled"),
        recent_reads=si_reader.recent_reads(),
    )


@app.route("/competitors")
def competitors():
    rows = [r for c in _console_data() for r in c["rows"]]
    rows.sort(key=lambda r: (r["class"], r["position"] is None, r["position"] or 0, r["name"]))
    return render_template(
        "competitors.html", active="competitors", rows=rows,
        class_options=store.class_options(),
        status_options=[{"value": s, "label": STATUS_LABELS[s]} for s in STATUS_ORDER],
    )


@app.route("/classes")
def classes():
    summary = []
    for c in _console_data():
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
            "entries": len(rows),
            "finished": sum(1 for r in rows if r["time"] is not None),
            "flagged": sum(1 for r in rows if r["status"] in FLAGGED),
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
                "controls": [{"seq": i + 1, "code": code} for i, code in enumerate(course["controls"])],
            })
    return render_template("courses.html", active="courses", courses=view)


@app.route("/download")
def download():
    rows = [r for c in _console_data() for r in c["rows"] if r["finish"] is not None]
    rows.sort(key=lambda r: r["finish"], reverse=True)
    return render_template("download.html", active="download", rows=rows, count=len(rows))


@app.route("/results")
def results():
    return render_template("results.html", active="results", classes=_console_data())


def _splits_data():
    """Per-class splits: a full leg matrix for linear classes, per-runner rows
    for score classes (legs aren't comparable when everyone picks a route)."""
    classes, _ = store.evaluate()
    view = []
    for entry in classes:
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
            item["rows"] = [_view_row(r) for r in entry["results"]]
        else:
            item["matrix"] = build_splits_matrix(
                entry["results"], course["controls"], course.get("leg_lengths"))
            item["length_m"] = course.get("length_m")
        view.append(item)
    return view


@app.route("/splits")
def splits():
    return render_template("splits.html", active="splits", classes=_splits_data())


@app.route("/slip/<int:comp_id>")
def slip(comp_id):
    """Printable finish-chute splits slip for one competitor."""
    comp = store.get_competitor(comp_id)
    if comp is None:
        abort(404)
    result = store.result_for(comp_id)
    if result is None:
        abort(404)
    return render_template("slip.html", row=_view_row(result),
                           auto_print=request.args.get("print") == "1")


@app.route("/live")
def live():
    """Projector-friendly live leaderboard (no operator chrome)."""
    return render_template("live.html", active="live", classes=_console_data())


def _club_archive():
    """All results grouped by club, each club's rows sorted by class then place."""
    rows = [r for c in _console_data() for r in c["rows"]]
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
def clubs():
    return render_template("clubs.html", active="clubs", clubs=_club_archive())


# ---------------------------------------------------------------------------
# Relay teams, economy, speaker, bib numbers / start-list printing
# ---------------------------------------------------------------------------

@app.route("/teams")
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
def speaker_page():
    """Commentator view: who's out on course, recent finishes."""
    rows = [r for c in _console_data() for r in c["rows"]]
    out = [r for r in rows if r["start"] and not r["finish"]]
    out.sort(key=lambda r: r["start"])
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
                      "card": c.get("card_number") or "",
                      "start": _clock(c.get("start"))} for c in members],
        })
    return classes


@app.route("/export/startlist.pdf")
def export_startlist_pdf():
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


@app.route("/tools")
def tools():
    """Import / export console."""
    return render_template("tools.html", active="tools")


@app.route("/public/<slug>")
def public_results(slug):
    """Permanent public, read-only results page (no operator chrome)."""
    if slug != store.EVENT["slug"]:
        abort(404)
    return render_template("public.html", classes=_console_data())


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
    created = [store.create_course(c)["name"] for c in courses]
    events.publish("course", action="import")
    return jsonify({"created": len(created), "names": created})


@app.route("/api/import/members", methods=["POST"])
def api_import_members():
    """Seed the shared runner database (autofill) from a CSV roster."""
    return jsonify(runners.import_csv(_uploaded_text()))


@app.route("/api/import/eventor", methods=["POST"])
def api_import_eventor():
    """Import competitors from an Eventor IOF XML EntryList file."""
    try:
        rows = eventor.parse_entrylist(_uploaded_text())
    except (ValueError, ET_ERROR) as err:
        raise StoreError(f"Could not read entry list: {err}")
    return jsonify(importers.import_competitors(rows))


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
    xml = iofxml.export_results(classes, store.EVENT)
    return Response(xml, mimetype="application/xml", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-results.xml"})


@app.route("/export/results.pdf")
def export_results_pdf():
    data = pdf.class_results_pdf(_console_data(), store.EVENT)
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-results.pdf"})


@app.route("/export/podium.pdf")
def export_podium_pdf():
    data = pdf.podium_pdf(_console_data(), store.EVENT)
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-podium.pdf"})


@app.route("/export/stillout.pdf")
def export_stillout_pdf():
    rows = [r for c in _console_data() for r in c["rows"] if r["start"] and not r["finish"]]
    rows.sort(key=lambda r: r["start"])
    data = pdf.still_out_pdf(rows, store.EVENT)
    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-stillout.pdf"})


@app.route("/export/splits.csv")
def export_splits_csv():
    """Long-format splits CSV for analysis (one row per leg). The IOF results XML
    (/export/results.xml) carries SplitTimes too, for SplitsBrowser/WinSplits."""
    import csv
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Class", "Position", "Name", "Club", "Status", "Total", "Control", "Leg", "Cumulative"])
    for c in _console_data():
        for r in c["rows"]:
            pos = r["position"] if r["position"] is not None else ""
            for s in r["splits"]:
                w.writerow([c["name"], pos, r["name"], r["club"] or "",
                            r["status_label"], r["time"] or "", s["control"],
                            s["leg"], s["cumulative"]])
    return Response(buf.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename={store.EVENT['slug']}-splits.csv"})


@app.route("/slip/<int:comp_id>.pdf")
def slip_pdf(comp_id):
    result = store.result_for(comp_id)
    if result is None:
        abort(404)
    data = pdf.splits_slip_pdf(_view_row(result), store.EVENT)
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


@app.route("/submit-entry")
def entry_submit():
    """On-the-day entry: create the competitor directly in the chosen class.
    Returns MeOS-style <Status>OK</Status> XML the entry page expects."""
    card = (request.args.get("card") or "").strip()
    try:
        comp = store.create_competitor({
            "name": (request.args.get("name") or "").strip(),
            "club": (request.args.get("club") or "").strip(),
            "class_id": request.args.get("class", type=int),
            "card_number": card or None,
        })
        runners.record_competitor(comp)  # learn this person + their class
        events.publish("competitor", action="entry")
        xml = "<Answer><Status>OK</Status></Answer>"
    except StoreError as err:
        xml = f"<Answer><Status>Fail</Status><Info>{xml_escape(str(err))}</Info></Answer>"
    return Response(xml, mimetype="application/xml")


@app.route("/log-entries", methods=["POST"])
def entry_log():
    data = _payload()
    # Public endpoint: keep the receipt path from being a spam/DoS amplifier --
    # validate the recipient and cap the (attacker-supplied) entries list. notify
    # itself re-validates and parses numbers safely.
    entries = data.get("entries")
    data["entries"] = entries[:50] if isinstance(entries, list) else []
    sent = notify.send_entry_receipt(data, store.EVENT)
    return jsonify({"ok": True, "emailSent": bool(sent)})


_STATUS_TO_ENTRY = {"dsq": "dq"}  # entry page uses 'dq'; others map 1:1


@app.route("/get-results")
def entry_results():
    class_id = request.args.get("classId", type=int)
    classes, _ = store.evaluate()
    out = []
    for entry in classes:
        cls = entry["class"]
        if class_id and cls["id"] != class_id:
            continue
        comps = []
        for r in entry["results"]:
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
                           entries=entries_mod.list_entries())


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
        data.get("first_start"), data.get("interval_minutes"),
        method=data.get("method", "alpha"),
        vacancy_every=data.get("vacancy_every", 0))
    events.publish("competitor", action="draw")
    return jsonify(outcome)


# ---------------------------------------------------------------------------
# Multi-event: events, series, competitor profiles
# ---------------------------------------------------------------------------

@app.route("/start")
def start():
    """Event selection: open an event file from the folder, or create a new one."""
    return render_template("start.html", events=store.events_in_folder(),
                           folder=store.events_dir())


@app.route("/setup")
def setup():
    """Per-event hub shown after opening/creating an event."""
    rows = [r for c in _console_data() for r in c["rows"]]
    counts = {
        "competitors": len(rows),
        "classes": len(store.class_options()),
        "downloaded": sum(1 for r in rows if r["finish"]),
        "out": sum(1 for r in rows if r["start"] and not r["finish"]),
    }
    return render_template("setup.html", active="setup", counts=counts,
                           remote_url=remote.url(), remote_configured=remote.is_configured())


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
    return jsonify({"event": event, "imported": imported}), 201


@app.route("/api/events/open", methods=["POST"])
def api_open_event():
    store.open_event(_payload().get("path", ""))
    auth.ensure_admin()
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
# Multi-day / multi-stage: combine several event files into overall standings
# ---------------------------------------------------------------------------

@app.route("/stages")
def stages_page():
    """Pick stage files from the events folder and combine them into overall
    (multi-day) standings, or set chase starts on the open event."""
    return render_template("stages.html", active="stages",
                           events=store.events_in_folder(),
                           folder=store.events_dir(),
                           first_start=store.EVENT.get("first_start") or "")


@app.route("/api/stages/combined", methods=["POST"])
def api_stages_combined():
    paths = _payload().get("paths") or []
    if not isinstance(paths, list) or not paths:
        raise StoreError("Pick at least one stage file")
    return jsonify({"classes": stages.combined_results(paths)})


@app.route("/api/stages/chase", methods=["POST"])
def api_stages_chase():
    """Set chase/handicap start times on the OPEN event from earlier stages."""
    data = _payload()
    paths = data.get("paths") or []
    if not isinstance(paths, list) or not paths:
        raise StoreError("Pick the earlier stage file(s) first")
    assigned = stages.apply_chase_starts(paths, data.get("first_start") or "")
    events.publish("competitor", action="chase")
    return jsonify({"ok": True, "assigned": assigned})


# ---------------------------------------------------------------------------
# Season prizes: one prize per course for the top placement, one prize per
# person per season (cascades to the next eligible finisher).
# ---------------------------------------------------------------------------

def _course_standings():
    """OK finishers grouped by course and ranked, for prize recommendations.
    Classes that share a course compete together for the one course prize."""
    classes, _ = store.evaluate()
    by_course: dict = {}
    for entry in classes:
        course = entry["course"]
        cs = by_course.setdefault(course["id"], {
            "course_id": course["id"], "course_name": course["name"],
            "is_score": course["type"] == "score", "rows": []})
        for r in entry["results"]:
            if r["status"] == "ok" and r["total_seconds"] is not None:
                cs["rows"].append({
                    "name": r["name"], "club": r.get("club") or "",
                    "card": r.get("card_number"), "points": r.get("points"),
                    "total_seconds": r["total_seconds"],
                    "time": format_duration(r["total_seconds"]),
                })
    out = []
    for cs in by_course.values():
        if cs["is_score"]:
            cs["rows"].sort(key=lambda r: (-(r["points"] or 0), r["total_seconds"]))
        else:
            cs["rows"].sort(key=lambda r: r["total_seconds"])
        for i, r in enumerate(cs["rows"]):
            r["position"] = i + 1
        out.append(cs)
    out.sort(key=lambda c: c["course_name"].lower())
    return out


@app.route("/prizes")
def prizes_page():
    season = prizes.current_season()
    standings = _course_standings()
    return render_template(
        "prizes.html", active="prizes", season=season,
        recommendations=prizes.recommend(standings, season),
        ledger=prizes.ledger(season))


@app.route("/api/prizes/award", methods=["POST"])
def api_prize_award():
    data = _payload()
    course = _clean(data.get("course_name"))
    if not course:
        raise StoreError("Which course is this prize for?")
    person = {"name": _clean(data.get("name")), "club": _clean(data.get("club")),
              "card_number": data.get("card_number")}
    awarded = prizes.award(prizes.current_season(), person, course)
    return jsonify({"ok": True, "awarded": awarded})


@app.route("/api/prizes/unaward", methods=["POST"])
def api_prize_unaward():
    key = _clean(_payload().get("person_key"))
    if key:
        prizes.unaward(prizes.current_season(), key)
    return jsonify({"ok": True})


@app.route("/api/prizes/season", methods=["POST"])
def api_prize_season():
    prizes.set_season(_clean(_payload().get("season")))
    return jsonify({"ok": True, "season": prizes.current_season()})


@app.route("/api/prizes/clear", methods=["POST"])
def api_prize_clear():
    removed = prizes.clear(prizes.current_season())
    return jsonify({"ok": True, "removed": removed})


# ---------------------------------------------------------------------------
# Series (cross-event season points) + competitor profiles, scanned from the
# events folder (no event need be open).
# ---------------------------------------------------------------------------

@app.route("/series")
def series_page():
    return render_template("series.html", active="series",
                           standings=series.standings(),
                           folder=store.events_dir())


@app.route("/profile")
def profile_page():
    key = _clean(request.args.get("key"))
    if not key:
        abort(404)
    data = series.profile(key)
    if data["person"] is None:
        abort(404)
    return render_template("profile.html", active="series",
                           person=data["person"], results=data["results"])


# ---------------------------------------------------------------------------
# Operator API (JSON)
# ---------------------------------------------------------------------------

def _payload():
    """Parsed JSON body, or {} for an empty/non-JSON request."""
    return request.get_json(silent=True) or {}


def _clean(value) -> str:
    """Trim a value to a string ('' for None) for light request-field handling."""
    return "" if value is None else str(value).strip()


@app.errorhandler(StoreError)
def _handle_store_error(err):
    return jsonify({"error": str(err)}), 400


# --- Competitors -----------------------------------------------------------

@app.route("/api/competitors", methods=["POST"])
def api_create_competitor():
    comp = store.create_competitor(_payload())
    runners.record_competitor(comp)  # learn this person + their usual class
    result = store.result_for(comp["id"])
    events.publish("competitor", action="create", id=comp["id"])
    return jsonify({"competitor": comp, "result": _result_view(result) if result else None}), 201


@app.route("/api/competitors/<int:comp_id>", methods=["GET"])
def api_get_competitor(comp_id):
    comp = store.get_competitor(comp_id)
    if comp is None:
        abort(404)
    result = store.result_for(comp_id)
    return jsonify({
        "competitor": store.competitor_json(comp),
        "result": _result_view(result) if result else None,
    })


@app.route("/api/competitors/<int:comp_id>", methods=["PUT", "PATCH"])
def api_update_competitor(comp_id):
    comp = store.update_competitor(comp_id, _payload())
    result = store.result_for(comp_id)
    events.publish("competitor", action="update", id=comp_id)
    return jsonify({"competitor": comp, "result": _result_view(result) if result else None})


@app.route("/api/competitors/<int:comp_id>", methods=["DELETE"])
def api_delete_competitor(comp_id):
    store.delete_competitor(comp_id)
    events.publish("competitor", action="delete", id=comp_id)
    return jsonify({"ok": True})


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Evaluate an unsaved competitor payload so the editor can show the effect."""
    result = store.preview(_payload())
    return jsonify({"result": _result_view(result)})


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
    """SSE feed: pushes a line whenever the event data changes."""
    def gen():
        q = events.subscribe()
        try:
            # An initial comment opens the stream immediately for the browser.
            yield ": connected\n\n"
            while True:
                payload = q.get()
                yield f"data: {payload}\n\n"
        finally:
            events.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
        # Secondary station: forward the read to the primary instead of applying
        # it locally (this instance may not even have an event open).
        if network.is_secondary():
            try:
                outcome = network.push_card(card)
            except Exception as err:  # network/HTTP failure -> report, don't 500
                return jsonify({"ok": False,
                                "error": f"primary unreachable: {err}"}), 502
            return jsonify(outcome), (200 if outcome.get("ok") else 404)
        outcome = si_reader.simulate(card, station_id=card.get("station_id"),
                                     auto_create=auto or None)
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
            return redirect(request.args.get("next") or url_for("index"))
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
    nxt = request.args.get("next") or url_for("index")
    if request.method == "POST":
        if config.check_admin_password(request.form.get("password", "")):
            session.permanent = True
            session["admin_ok"] = True
            return redirect(request.form.get("next") or nxt)
        return render_template("unlock.html", error="Incorrect password", next=nxt), 401
    return render_template("unlock.html", error=None, next=nxt)


@app.route("/lock")
def lock():
    session.pop("admin_ok", None)
    return redirect(url_for("unlock"))


@app.route("/config")
def config_page():
    """Settings dashboard: edit the interchangeable values (PayPal, ngrok, SMTP,
    fees, ports, admin password) -> config.json."""
    return render_template("config.html", active="config",
                           groups=config.dashboard_values())


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify({"groups": config.dashboard_values()})


@app.route("/api/config", methods=["POST"])
def api_save_config():
    config.save(_payload())
    return jsonify({"ok": True,
                    "note": "Port changes take effect after a restart."})


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
    # The real SI reader only starts if the event has it enabled (BMEOS_READER);
    # otherwise reads come from POST /api/reader/simulate. The reloader would
    # start the thread twice, so only start it in the main process.
    # With the debug reloader on, the serving process is the one where Werkzeug
    # sets WERKZEUG_RUN_MAIN; start the reader only there so it isn't launched
    # twice. (No-op anyway unless the event has the reader enabled.)
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        si_reader.start_all()
    # The dev server is single-port (the full admin surface); the port split is
    # a launcher/production concern -- run launcher.py to serve both ports.
    # threaded=True so a long-lived SSE stream doesn't block other requests.
    app.run(host="0.0.0.0", port=config.admin_port(), debug=True, threaded=True)
