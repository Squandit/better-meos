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
