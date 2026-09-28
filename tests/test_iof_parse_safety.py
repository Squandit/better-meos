"""Uploaded text is always parsed as XML, never opened as a file name."""

import pytest

import iofxml


def test_text_is_never_taken_as_a_path(tmp_path):
    secret = tmp_path / "secret.xml"
    secret.write_text('<?xml version="1.0"?><CourseData xmlns="http://www.orienteering.org/'
                      'datastandard/3.0" iofVersion="3.0"><Course><Name>X</Name>'
                      '<CourseControl type="Control"><Control>31</Control></CourseControl>'
                      "</Course></CourseData>")
    with pytest.raises(Exception) as err:
        iofxml.parse_courses(str(secret))
    assert "No such file" not in str(err.value)
    # An open file still works (that's how a script would pass one).
    with open(secret, "rb") as f:
        assert iofxml.parse_courses(f)[0]["name"] == "X"
