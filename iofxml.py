"""
IOF XML v3 interchange (the orienteering data standard).

Two directions:

* :func:`parse_courses` reads a ``CourseData`` file (what a course planner like
  Condes / OCAD / Purple Pen exports) into plain course dicts the store can
  create.
* :func:`parse_startlist` reads a ``StartList`` file into competitor dicts.
* :func:`export_results` turns the engine's evaluated classes into a
  ``ResultList`` document for publishing or upload.

Only the parts better-meos needs are implemented; unknown elements are ignored.
The namespace is the IOF v3 standard ``http://www.orienteering.org/datastandard/3.0``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

from results import aligned_splits

NS = "http://www.orienteering.org/datastandard/3.0"
_NS = {"i": NS}
# Write IOF as the default namespace (<ResultList xmlns="...">) rather than an
# ns0: prefix: equivalent XML, but some older orienteering tools only accept
# the unprefixed form.
ET.register_namespace("", NS)

# Our internal status codes <-> IOF ResultStatus values.
STATUS_TO_IOF = {
    "ok": "OK",
    "mp": "MissingPunch",
    "dns": "DidNotStart",
    "dnf": "DidNotFinish",
    "dsq": "Disqualified",
    "oot": "OverTime",
    "nc": "NotCompeting",
    "pending": "Active",       # no card read yet (Inactive when not started)
}
IOF_TO_STATUS = {v: k for k, v in STATUS_TO_IOF.items()}
IOF_TO_STATUS["Inactive"] = "pending"


def _tag(elem) -> str:
    """Local tag name, namespace stripped."""
    return elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag


def _require_v3(root, expected_local: str) -> None:
    """Confirm the document is the expected IOF v3 type, with a clear message
    for the common 'right file, wrong version/namespace' mistake."""
    if root.tag == f"{{{NS}}}{expected_local}":
        return
    if _tag(root) == expected_local:
        raise ValueError(
            f"Only IOF XML v3 is supported (this looks like a different "
            f"version of {expected_local})")
    raise ValueError(f"Not an IOF {expected_local} file")


def _find(parent, name):
    return parent.find(f"i:{name}", _NS)


def _findall(parent, name):
    return parent.findall(f"i:{name}", _NS)


def _text(parent, name, default=None):
    el = _find(parent, name)
    return el.text if el is not None and el.text is not None else default


# ---------------------------------------------------------------------------
# Parsing (import)
# ---------------------------------------------------------------------------

def _split_name(person) -> str:
    """Join an IOF ``Person/Name`` (Given + Family) into our single name string."""
    name = _find(person, "Name")
    if name is None:
        return ""
    given = _text(name, "Given", "") or ""
    family = _text(name, "Family", "") or ""
    return " ".join(part for part in (given.strip(), family.strip()) if part)


def parse_courses(source) -> list[dict]:
    """
    Parse an IOF ``CourseData`` document into linear course dicts.

    ``source`` is a path, file object or bytes/str of XML. Returns
    ``[{"name": str, "type": "linear", "controls": [int, ...]}, ...]`` in file
    order. Only ``CourseControl`` entries of type ``Control`` contribute codes
    (Start/Finish markers are skipped).
    """
    root = _parse_root(source)
    _require_v3(root, "CourseData")

    courses = []
    for course in root.iter(f"{{{NS}}}Course"):
        name = _text(course, "Name", "").strip()
        codes, leg_lengths = [], []
        for cc in _findall(course, "CourseControl"):
            if cc.get("type") != "Control":
                continue
            code = _text(cc, "Control")
            if code and code.strip().isdigit():
                codes.append(int(code.strip()))
                leg = _text(cc, "LegLength")
                leg_lengths.append(int(float(leg)) if leg and leg.strip() else None)
        length = _text(course, "Length")  # course length in metres (geometry)
        if name and codes:
            courses.append({
                "name": name, "type": "linear", "controls": codes,
                "length_m": int(float(length)) if length and length.strip() else None,
                "leg_lengths": leg_lengths,
            })
    if not courses:
        raise ValueError("No courses with controls found in the file")
    return courses


def parse_startlist(source) -> list[dict]:
    """
    Parse an IOF ``StartList`` document into competitor dicts.

    Returns ``[{"name", "club", "class_name", "card_number", "start"}, ...]``;
    ``start`` is a ``datetime`` or None, ``card_number`` an int or None. The
    caller resolves ``class_name`` to a class id before creating competitors.
    """
    root = _parse_root(source)
    _require_v3(root, "StartList")

    out = []
    for class_start in root.iter(f"{{{NS}}}ClassStart"):
        cls = _find(class_start, "Class")
        class_name = _text(cls, "Name", "").strip() if cls is not None else ""
        for ps in _findall(class_start, "PersonStart"):
            person = _find(ps, "Person")
            start_el = _find(ps, "Start")
            org = _find(ps, "Organisation")
            card = _text(start_el, "ControlCard") if start_el is not None else None
            start_txt = _text(start_el, "StartTime") if start_el is not None else None
            out.append({
                "name": _split_name(person) if person is not None else "",
                "club": (_text(org, "Name", "") or "").strip() if org is not None else "",
                "class_name": class_name,
                "card_number": int(card) if card and card.strip().isdigit() else None,
                "start": _parse_iso(start_txt),
            })
    return out


def parse_entrylist(source) -> list[dict]:
    """
    Parse an IOF ``EntryList`` document (what Eventor exports) into competitor
    dicts: ``[{"name", "club", "class_name", "card_number", "start": None}, ...]``.
    """
    root = _parse_root(source)
    _require_v3(root, "EntryList")
    out = []
    for pe in root.iter(f"{{{NS}}}PersonEntry"):
        person = _find(pe, "Person")
        org = _find(pe, "Organisation")
        cls = _find(pe, "Class")
        card = _text(pe, "ControlCard")
        out.append({
            "name": _split_name(person) if person is not None else "",
            "club": (_text(org, "Name", "") or "").strip() if org is not None else "",
            "class_name": _text(cls, "Name", "").strip() if cls is not None else "",
            "card_number": int(card) if card and card.strip().isdigit() else None,
            "start": None,
        })
    return out


def parse_resultlist(source) -> list[dict]:
    """
    Parse an IOF ``ResultList`` (e.g. exported from MeOS or another event
    system) into runs: ``[{"name", "club", "class_name", "card_number",
    "start", "finish", "status", "splits": [(code, seconds_from_start), ...]}]``.
    Missing splits (status="Missing") are skipped: the run simply lacks that
    punch, and our engine works out the mispunch itself.
    """
    root = _parse_root(source)
    _require_v3(root, "ResultList")
    out = []
    for class_result in root.iter(f"{{{NS}}}ClassResult"):
        cls = _find(class_result, "Class")
        class_name = _text(cls, "Name", "").strip() if cls is not None else ""
        for pr in _findall(class_result, "PersonResult"):
            person = _find(pr, "Person")
            org = _find(pr, "Organisation")
            res = _find(pr, "Result")
            if res is None:
                continue
            splits = []
            for st in _findall(res, "SplitTime"):
                code, secs = _text(st, "ControlCode"), _text(st, "Time")
                if st.get("status") in ("Missing", "Additional") or not code or secs is None:
                    continue
                try:
                    splits.append((int(code), int(float(secs))))
                except ValueError:
                    continue
            card = _text(res, "ControlCard")
            out.append({
                "name": _split_name(person) if person is not None else "",
                "club": (_text(org, "Name", "") or "").strip() if org is not None else "",
                "class_name": class_name,
                "card_number": int(card) if card and card.strip().isdigit() else None,
                "start": _parse_iso(_text(res, "StartTime")),
                "finish": _parse_iso(_text(res, "FinishTime")),
                "status": IOF_TO_STATUS.get(_text(res, "Status", "OK"), "ok"),
                "splits": splits,
            })
    return out


def parse_competitorlist(source) -> list[dict]:
    """
    Parse an IOF ``CompetitorList`` (MeOS's runner-database export, Eventor's
    club member lists) into ``[{"name", "club", "card_number"}]``.
    """
    root = _parse_root(source)
    _require_v3(root, "CompetitorList")
    out = []
    for comp in root.iter(f"{{{NS}}}Competitor"):
        person = _find(comp, "Person")
        org = _find(comp, "Organisation")
        card = _text(comp, "ControlCard")
        out.append({
            "name": _split_name(person) if person is not None else "",
            "club": (_text(org, "Name", "") or "").strip() if org is not None else "",
            "card_number": int(card) if card and card.strip().isdigit() else None,
        })
    return out


def _parse_root(source):
    if isinstance(source, (bytes, bytearray)):
        return ET.fromstring(source)
    if isinstance(source, str) and source.lstrip().startswith("<"):
        return ET.fromstring(source)
    return ET.parse(source).getroot()


def _parse_iso(text):
    if not text:
        return None
    # IOF times may carry a timezone/offset; drop it to a naive local datetime.
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_results(classes_eval: list[dict], event: dict,
                   courses: dict | None = None) -> str:
    """
    Build an IOF ``ResultList`` XML document from evaluated classes.

    ``classes_eval`` is ``store.evaluate()[0]`` -- a list of
    ``{"class", "course", "results"}`` with results already ranked. Returns the
    XML as a unicode string (with declaration).
    """
    root = ET.Element(f"{{{NS}}}ResultList", {
        "iofVersion": "3.0",
        "creator": "better-meos",
    })
    ev = ET.SubElement(root, f"{{{NS}}}Event")
    _sub(ev, "Name", event.get("name", ""))
    if event.get("date_iso"):
        start = ET.SubElement(ev, f"{{{NS}}}StartTime")
        _sub(start, "Date", event["date_iso"])

    for entry in classes_eval:
        cls = entry["class"]
        course = entry.get("course") or {}
        cr = ET.SubElement(root, f"{{{NS}}}ClassResult")
        class_el = ET.SubElement(cr, f"{{{NS}}}Class")
        _sub(class_el, "Name", cls["name"])
        if course.get("name"):
            course_el = ET.SubElement(cr, f"{{{NS}}}Course")
            _sub(course_el, "Name", course["name"])
            if course.get("length_m"):
                _sub(course_el, "Length", str(course["length_m"]))

        for r in entry["results"]:
            pr = ET.SubElement(cr, f"{{{NS}}}PersonResult")
            person = ET.SubElement(pr, f"{{{NS}}}Person")
            name_el = ET.SubElement(person, f"{{{NS}}}Name")
            given, family = _name_parts(r["name"])
            _sub(name_el, "Family", family)
            _sub(name_el, "Given", given)
            if r.get("club"):
                org = ET.SubElement(pr, f"{{{NS}}}Organisation")
                _sub(org, "Name", r["club"])

            # Element order follows the IOF v3 Result schema: StartTime,
            # FinishTime, Time, Position, Status, then SplitTime*, then
            # ControlCard. (The standard has no Score element on a foot-O
            # Result; score points live in the HTML/PDF/CSV exports instead.)
            res = ET.SubElement(pr, f"{{{NS}}}Result")
            if r.get("start"):
                _sub(res, "StartTime", r["start"].isoformat())
            if r.get("finish"):
                _sub(res, "FinishTime", r["finish"].isoformat())
            if r.get("total_seconds") is not None:
                _sub(res, "Time", str(r["total_seconds"]))
            if r.get("position") is not None:
                _sub(res, "Position", str(r["position"]))
            status = STATUS_TO_IOF.get(r["status"], "OK")
            if r["status"] == "pending" and r.get("start") is None:
                status = "Inactive"
            _sub(res, "Status", status)
            own = (courses or {}).get(r.get("course_id"), course)  # forked runner
            for code, secs in _split_times(r, own):
                attrs = {"status": "Missing"} if secs is None else {}
                st = ET.SubElement(res, f"{{{NS}}}SplitTime", attrs)
                _sub(st, "ControlCode", str(code))
                if secs is not None:
                    _sub(st, "Time", str(secs))
            if r.get("card_number"):
                _sub(res, "ControlCard", str(r["card_number"]))

    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _split_times(r: dict, course: dict) -> list[tuple]:
    """
    SplitTimes as WinSplits / Routegadget expect them: one per course control
    in course order (repeated controls included), a missed control as None
    (exported with status="Missing"). Score courses have no fixed order, so
    they list the punches as punched.
    """
    if course.get("type") == "linear" and r.get("start") is not None:
        aligned = aligned_splits(r["start"], r.get("punches", []), r.get("finish"),
                                 list(course.get("controls", [])))
        return [(a["control"], a["cumulative_seconds"]) for a in aligned
                if a["control"] != "F"]
    return [(s["control"], s["cumulative_seconds"]) for s in r.get("splits", [])
            if s["control"] != "F"]


def _sub(parent, name, text):
    el = ET.SubElement(parent, f"{{{NS}}}{name}")
    el.text = text
    return el


def _name_parts(full: str) -> tuple[str, str]:
    """Split our single name into (given, family) for IOF Person/Name."""
    parts = full.strip().rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", full.strip()
