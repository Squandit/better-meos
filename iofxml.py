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

NS = "http://www.orienteering.org/datastandard/3.0"
_NS = {"i": NS}

# Our internal status codes <-> IOF ResultStatus values.
STATUS_TO_IOF = {
    "ok": "OK",
    "mp": "MissingPunch",
    "dns": "DidNotStart",
    "dnf": "DidNotFinish",
    "dsq": "Disqualified",
    "oot": "OverTime",
}
IOF_TO_STATUS = {v: k for k, v in STATUS_TO_IOF.items()}


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
        codes = []
        for cc in _findall(course, "CourseControl"):
            if cc.get("type") != "Control":
                continue
            code = _text(cc, "Control")
            if code and code.strip().isdigit():
                codes.append(int(code.strip()))
        if name and codes:
            courses.append({"name": name, "type": "linear", "controls": codes})
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

def export_results(classes_eval: list[dict], event: dict) -> str:
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

    for entry in classes_eval:
        cls = entry["class"]
        cr = ET.SubElement(root, f"{{{NS}}}ClassResult")
        class_el = ET.SubElement(cr, f"{{{NS}}}Class")
        _sub(class_el, "Name", cls["name"])

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
            _sub(res, "Status", STATUS_TO_IOF.get(r["status"], "OK"))
            for s in r.get("splits", []):
                if s["control"] == "F":
                    continue
                st = ET.SubElement(res, f"{{{NS}}}SplitTime")
                _sub(st, "ControlCode", str(s["control"]))
                _sub(st, "Time", str(s["cumulative_seconds"]))
            if r.get("card_number"):
                _sub(res, "ControlCard", str(r["card_number"]))

    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


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
