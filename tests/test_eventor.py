"""Eventor API (gated; urllib/fetch monkeypatched -- no live Eventor)."""

import app as appmod
import eventor
import store


def test_upload_requires_configuration(monkeypatch):
    monkeypatch.delenv("EVENTOR_API_KEY", raising=False)
    monkeypatch.delenv("EVENTOR_BASE_URL", raising=False)
    r = appmod.app.test_client().post("/api/eventor/upload")
    assert r.status_code == 400
    assert "configured" in r.get_json()["error"].lower()


def test_upload_posts_iof_results(monkeypatch):
    monkeypatch.setenv("EVENTOR_API_KEY", "KEY123")
    monkeypatch.setenv("EVENTOR_BASE_URL", "https://eventor.test/")

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getcode(self):
            return 200

        def read(self):
            return b"OK"

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["key"] = req.get_header("Apikey")
        captured["data"] = req.data
        return FakeResp()

    monkeypatch.setattr(eventor.urllib.request, "urlopen", fake_urlopen)

    r = appmod.app.test_client().post("/api/eventor/upload")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert captured["url"] == "https://eventor.test/api/results"   # trailing slash trimmed
    assert captured["key"] == "KEY123"
    assert b"Result" in captured["data"]                            # IOF results XML body


def test_fetch_requires_configuration(monkeypatch):
    monkeypatch.delenv("EVENTOR_API_KEY", raising=False)
    monkeypatch.delenv("EVENTOR_BASE_URL", raising=False)
    r = appmod.app.test_client().post("/api/eventor/fetch")
    assert r.status_code == 400


def test_fetch_imports_and_autocreates_classes(monkeypatch):
    # Pull (mocked) straight from Eventor: a brand-new class is created on the fly.
    rows = [{"name": "Eventor Eve", "club": "EVOC", "class_name": "EventorAutoClass",
             "card_number": 7790001, "start": ""}]
    monkeypatch.setattr(eventor, "fetch_entries", lambda event_id: rows)
    r = appmod.app.test_client().post("/api/eventor/fetch")
    assert r.status_code == 200
    body = r.get_json()
    assert body["created"] >= 1 and body["classes_created"] >= 1
    assert any(c["name"] == "EventorAutoClass" for c in store.class_options())
