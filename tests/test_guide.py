"""Run-an-event guide: data integrity + page/sidebar rendering."""

import app as appmod
import guide


def test_steps_resolve_to_real_routes():
    # Every step link must resolve via url_for -- a typo'd endpoint would fall
    # back to '#', which this catches.
    with appmod.app.test_request_context():
        steps = guide.steps()
    assert steps and all(s["links"] for s in steps)
    assert all(link["href"] != "#" for s in steps for link in s["links"])
    assert [s["number"] for s in steps] == list(range(1, len(steps) + 1))


def test_guide_page_renders():
    html = appmod.app.test_client().get("/guide").get_data(as_text=True)
    assert "Set up courses" in html and "Draw the start list" in html


def test_guide_sidebar_and_toggle_on_operator_pages():
    html = appmod.app.test_client().get("/overview").get_data(as_text=True)
    assert "data-guide-panel" in html and "data-guide-toggle" in html
    assert "Run an event" in html


def test_setup_shows_checklist():
    html = appmod.app.test_client().get("/setup").get_data(as_text=True)
    assert "setup-checklist" in html


def test_guide_is_operator_only_surface():
    c = appmod.app.test_client()
    assert c.get("/guide", environ_overrides={"SERVER_PORT": "8800"}).status_code == 404
