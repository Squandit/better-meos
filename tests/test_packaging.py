"""Launcher + remote-hosting wiring (no real tunnel / no exe build here)."""

import app as appmod
import remote


def test_launcher_imports():
    import launcher
    assert hasattr(launcher, "main")


def test_remote_start_failure_is_clean(monkeypatch):
    # No ngrok available in tests -> the route must return a clean 500, not crash.
    def boom(port):
        raise RuntimeError("ngrok not available")
    monkeypatch.setattr(remote, "start", boom)
    c = appmod.app.test_client()
    r = c.post("/api/remote/start")
    assert r.status_code == 500 and "error" in r.get_json()


def test_remote_stop_ok():
    assert appmod.app.test_client().post("/api/remote/stop").status_code == 200


def test_remote_url_reflected_on_setup(monkeypatch):
    monkeypatch.setattr(remote, "_url", "https://demo.ngrok-free.app")
    html = appmod.app.test_client().get("/setup").get_data(as_text=True)
    assert "demo.ngrok-free.app" in html
