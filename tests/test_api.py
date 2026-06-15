"""End-to-end checks of the Flask routes via the test client."""

import app as appmod
import pytest


@pytest.fixture
def client():
    return appmod.app.test_client()


def test_pages_render(client):
    for p in ["/", "/competitors", "/classes", "/courses", "/download",
              "/results", "/splits", "/live"]:
        assert client.get(p).status_code == 200


def test_slip_renders_for_known_competitor(client):
    import store
    comp_id = next(iter(store._competitors))
    assert client.get(f"/slip/{comp_id}").status_code == 200


def test_slip_404_for_unknown(client):
    assert client.get("/slip/999999").status_code == 404


def test_simulate_mock_card(client):
    r = client.post("/api/reader/simulate")
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_simulate_with_body(client):
    r = client.post("/api/reader/simulate", json={
        "card_number": 8500001, "start": "09:00:00", "finish": "10:00:00",
        "punches": [{"code": 138, "time": "09:10:00"}],
    })
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_simulate_unknown_card_404(client):
    r = client.post("/api/reader/simulate", json={"card_number": 99999, "punches": []})
    assert r.status_code == 404


def test_simulate_malformed_punch_is_400_not_500(client):
    # A malformed punch must surface as a clean StoreError/400, never a 500.
    r = client.post("/api/reader/simulate",
                    json={"card_number": 8500001, "punches": [{"code": 138}]})
    assert r.status_code == 400


def test_backup_downloads_bytes(client):
    r = client.get("/api/backup")
    assert r.status_code == 200 and len(r.data) > 0


def test_tools_and_clubs_pages(client):
    assert client.get("/tools").status_code == 200
    assert client.get("/clubs").status_code == 200


def test_public_results(client):
    import store
    assert client.get(f"/public/{store.EVENT['slug']}").status_code == 200
    assert client.get("/public/nope").status_code == 404


def test_export_results_xml(client):
    r = client.get("/export/results.xml")
    assert r.status_code == 200 and b"ResultList" in r.data


def test_export_results_pdf(client):
    r = client.get("/export/results.pdf")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"


def test_slip_pdf(client):
    import store
    cid = next(iter(store._competitors))
    r = client.get(f"/slip/{cid}.pdf")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"


def test_import_courses_via_upload(client):
    import io
    from tests.test_import_export import COURSE_XML
    r = client.post("/api/import/courses", data={
        "file": (io.BytesIO(COURSE_XML.encode()), "courses.xml")},
        content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["created"] == 1


def test_import_startlist_csv_via_upload(client):
    import io
    csv_text = b"name,club,class,card,start\nUpload Ulla,TESTOC,M21A,8502222,09:40:00\n"
    r = client.post("/api/import/startlist", data={
        "file": (io.BytesIO(csv_text), "startlist.csv")},
        content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["created"] == 1
