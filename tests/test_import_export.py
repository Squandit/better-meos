"""IOF XML parse/export and CSV start-list import."""

import xml.etree.ElementTree as ET

import iofxml
import importers
import pdf
import store

NS = "{http://www.orienteering.org/datastandard/3.0}"

COURSE_XML = """<?xml version="1.0"?>
<CourseData xmlns="http://www.orienteering.org/datastandard/3.0" iofVersion="3.0">
  <RaceCourseData>
    <Course>
      <Name>Test Course</Name>
      <CourseControl type="Start"><Control>S1</Control></CourseControl>
      <CourseControl type="Control"><Control>31</Control></CourseControl>
      <CourseControl type="Control"><Control>32</Control></CourseControl>
      <CourseControl type="Control"><Control>33</Control></CourseControl>
      <CourseControl type="Finish"><Control>F1</Control></CourseControl>
    </Course>
  </RaceCourseData>
</CourseData>
"""

STARTLIST_XML = """<?xml version="1.0"?>
<StartList xmlns="http://www.orienteering.org/datastandard/3.0" iofVersion="3.0">
  <ClassStart>
    <Class><Name>M21A</Name></Class>
    <PersonStart>
      <Person><Name><Given>Iof</Given><Family>Imported</Family></Name></Person>
      <Organisation><Name>XMLOC</Name></Organisation>
      <Start><ControlCard>8501234</ControlCard><StartTime>2026-05-17T09:31:00</StartTime></Start>
    </PersonStart>
  </ClassStart>
</StartList>
"""


def test_parse_courses_extracts_controls_in_order():
    courses = iofxml.parse_courses(COURSE_XML)
    assert len(courses) == 1
    assert courses[0]["name"] == "Test Course"
    assert courses[0]["type"] == "linear"
    assert courses[0]["controls"] == [31, 32, 33]


def test_parse_courses_rejects_wrong_root():
    try:
        iofxml.parse_courses("<NotCourseData/>")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_export_results_is_valid_iof_xml():
    classes, _ = store.evaluate()
    xml = iofxml.export_results(classes, store.EVENT)
    root = ET.fromstring(xml)
    assert root.tag == NS + "ResultList"
    assert root.findall(NS + "ClassResult")  # at least one class
    statuses = {e.text for e in root.iter(NS + "Status")}
    assert "OK" in statuses


def test_export_result_element_order_and_no_score():
    classes, _ = store.evaluate()
    root = ET.fromstring(iofxml.export_results(classes, store.EVENT))
    # No Score element (not part of the v3 Result schema).
    assert not list(root.iter(NS + "Score"))
    # In each Result, Status must precede any SplitTime, and ControlCard follows.
    for res in root.iter(NS + "Result"):
        kinds = [child.tag.split("}", 1)[-1] for child in res]
        if "SplitTime" in kinds and "Status" in kinds:
            assert kinds.index("Status") < kinds.index("SplitTime")
        if "ControlCard" in kinds and "SplitTime" in kinds:
            assert kinds.index("SplitTime") < kinds.index("ControlCard")


def test_parse_courses_rejects_wrong_version():
    v2 = ('<CourseData xmlns="http://www.orienteering.org/datastandard/2.0">'
          '</CourseData>')
    try:
        iofxml.parse_courses(v2)
        assert False, "expected ValueError"
    except ValueError as err:
        assert "v3" in str(err)


def test_pdf_handles_special_characters():
    row = {
        "name": "A & B <test>", "class": "M21A", "club": "C & D OC",
        "si": 123, "start": "09:00:00", "finish": "09:30:00",
        "time": "00:30:00", "status_label": "OK", "is_ok": True,
        "points": None, "missed_control": None,
        "splits": [{"control": 138, "leg": "1:00", "cumulative": "1:00"}],
    }
    data = pdf.splits_slip_pdf(row, {"name": "Cups & Co", "date": "1 Jan"})
    assert data[:4] == b"%PDF"


def test_csv_import_creates_and_skips():
    csv_text = (
        "name,club,class,card,start\n"
        "New Ned,TESTOC,M21A,8500099,09:33:00\n"
        "Bad Class,X,NoSuchClass,123,09:00:00\n"
        ",TESTOC,M21A,1,09:00:00\n"
    )
    rows = importers.parse_startlist_csv(csv_text)
    outcome = importers.import_competitors(rows)
    assert outcome["created"] == 1
    reasons = " ".join(s["reason"] for s in outcome["skipped"])
    assert "unknown class" in reasons
    assert "missing name" in reasons
    assert store.find_by_card(8500099)["name"] == "New Ned"


def test_iof_startlist_import():
    rows = iofxml.parse_startlist(STARTLIST_XML)
    assert rows[0]["name"] == "Iof Imported"
    assert rows[0]["card_number"] == 8501234
    outcome = importers.import_competitors(rows)
    assert outcome["created"] == 1
    assert store.find_by_card(8501234)["club"] == "XMLOC"


def test_results_round_trip_through_iof_xml():
    """Export this event's results, import them into a fresh event, and the
    engine recomputes the same statuses and times (the MeOS migration path)."""
    import io
    import iofxml
    import store
    import app as appmod
    c = appmod.app.test_client()
    xml = c.get("/export/results.xml").get_data()
    # Score classes can't round-trip: IOF results don't describe score courses
    # (import the CourseData first for those).
    before = {(r["name"], r["status"], r["total_seconds"])
              for e in store.evaluate()[0] if e["course"]["type"] == "linear"
              for r in e["results"]}
    original = store.current_event_path()
    try:
        store.new_event({"name": "Imported", "date": store.EVENT["date_iso"]})
        r = c.post("/api/import/results", data={"file": (io.BytesIO(xml), "r.xml")},
                   content_type="multipart/form-data")
        body = r.get_json()
        assert r.status_code == 200 and body["created"] > 0 and body["new_classes"]
        after = {(r["name"], r["status"], r["total_seconds"])
                 for e in store.evaluate()[0] for r in e["results"]}
        # Runners with a start round-trip exactly (DNS rows carry no splits).
        timed = {x for x in before if x[2] is not None}
        assert timed <= after
    finally:
        store.open_event(original)


def test_missed_control_exported_as_missing_split():
    from datetime import datetime
    import iofxml
    from results import build_result
    d = datetime(2026, 5, 17)
    course = {"id": 1, "name": "C", "type": "linear", "controls": [31, 32, 33]}
    card = {"id": 1, "name": "Miss Me", "class": "M", "start": d.replace(hour=10),
            "punches": [(31, d.replace(hour=10, minute=5)), (33, d.replace(hour=10, minute=15))],
            "finish": d.replace(hour=10, minute=20)}
    res = build_result(card, {"type": "linear", "controls": [31, 32, 33]})
    xml = iofxml.export_results([{"class": {"name": "M"}, "course": course, "results": [res]}],
                                {"name": "E", "date_iso": "2026-05-17"})
    assert '<SplitTime status="Missing">' in xml and "<ControlCode>32</ControlCode>" in xml
    assert "ns0:" not in xml and 'xmlns="http://www.orienteering.org/datastandard/3.0"' in xml


def test_competitorlist_fills_runner_db():
    import io
    import app as appmod
    import runners
    xml = b"""<?xml version="1.0"?>
<CompetitorList xmlns="http://www.orienteering.org/datastandard/3.0" iofVersion="3.0">
  <Competitor><Person><Name><Family>Lind</Family><Given>Karin</Given></Name></Person>
    <Organisation><Name>OK Linne</Name></Organisation><ControlCard>7654321</ControlCard></Competitor>
  <Competitor><Person><Name><Family>NoCard</Family><Given>Ned</Given></Name></Person></Competitor>
</CompetitorList>"""
    r = appmod.app.test_client().post(
        "/api/import/runners", data={"file": (io.BytesIO(xml), "c.xml")},
        content_type="multipart/form-data")
    assert r.get_json() == {"imported": 1, "skipped": 1}
    assert runners.lookup(7654321)["name"] == "Karin Lind"
