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


def test_kiosk_printing_command_uses_its_own_profile():
    import launcher
    cmd = launcher.kiosk_command("msedge.exe", "http://127.0.0.1:8799/start", r"C:\p")
    assert "--kiosk-printing" in cmd and "--user-data-dir=C:\\p" in cmd
    assert cmd[-1].endswith("/start")


def test_slip_uses_thermal_layout_by_default():
    import app as appmod
    import store
    comp_id = next(c["id"] for c in store._competitors.values() if not c.get("vacant"))
    html = appmod.app.test_client().get(f"/slip/{comp_id}?print=1").get_data(as_text=True)
    assert "size: 80mm auto" in html and "afterprint" in html
