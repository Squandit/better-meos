"""Port-surface split, admin unlock gate, and the config.json settings store."""

import os

import pytest

import app as appmod
import config

PUBLIC = "8800"   # config.public_port() default; the test client defaults to 80


@pytest.fixture
def cfg(tmp_path):
    """Point config.json at a throwaway file for one test, isolated from the
    rest of the suite (env restored + cache cleared on teardown so a password
    set here can't gate other tests)."""
    prev = os.environ.get("BMEOS_CONFIG")
    os.environ["BMEOS_CONFIG"] = str(tmp_path / "config.json")
    config.reload()
    try:
        yield config
    finally:
        if prev is None:
            os.environ.pop("BMEOS_CONFIG", None)
        else:
            os.environ["BMEOS_CONFIG"] = prev
        config.reload()


# --- config.py -------------------------------------------------------------

def test_get_uses_default_then_file(cfg):
    assert cfg.get("currency") == "AUD"          # built-in default
    cfg.save({"currency": "NZD", "fee_senior": "12.5", "paypal_sandbox": False})
    assert cfg.get("currency") == "NZD"
    assert cfg.get("fee_senior") == 12.5         # coerced to float
    assert cfg.get("paypal_sandbox") is False    # coerced to bool


def test_env_is_a_fallback(cfg, monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "env-client")
    assert cfg.get_str("paypal_client_id") == "env-client"
    cfg.save({"paypal_client_id": "file-client"})
    assert cfg.get_str("paypal_client_id") == "file-client"   # file wins over env


def test_admin_password_hashed_not_plaintext(cfg):
    assert cfg.admin_password_set() is False
    cfg.save({"admin_password": "s3cret"})
    assert cfg.admin_password_set() is True
    assert cfg.check_admin_password("s3cret") is True
    assert cfg.check_admin_password("wrong") is False
    with open(os.environ["BMEOS_CONFIG"], encoding="utf-8") as f:
        raw = f.read()
    assert "s3cret" not in raw and "admin_password_hash" in raw


def test_blank_admin_password_keeps_current(cfg):
    cfg.save({"admin_password": "keepme"})
    cfg.save({"admin_password": ""})             # blank = leave unchanged
    assert cfg.check_admin_password("keepme") is True


# --- /api/config -----------------------------------------------------------

def test_api_config_reports_password_as_set_not_value(cfg):
    cfg.set_admin_password("hidden")
    groups = cfg.dashboard_values()
    security = next(g for g in groups if g["group"] == "Security")
    field = security["fields"][0]
    assert field["key"] == "admin_password" and field["value"] == "" and field["is_set"] is True


def test_api_config_get_and_save_roundtrip(cfg):
    c = appmod.app.test_client()
    assert c.get("/api/config").status_code == 200      # no password set -> open
    r = c.post("/api/config", json={"ngrok_domain": "demo.ngrok.app"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert config.get_str("ngrok_domain") == "demo.ngrok.app"


# --- Port-surface split ----------------------------------------------------

def test_public_port_hides_admin_pages():
    c = appmod.app.test_client()
    env = {"SERVER_PORT": PUBLIC}
    assert c.get("/competitors", environ_overrides=env).status_code == 404
    assert c.get("/api/competitors/1", environ_overrides=env).status_code == 404
    assert c.get("/results", environ_overrides=env).status_code == 200
    assert c.get("/enter", environ_overrides=env).status_code == 200
    assert c.get("/", environ_overrides=env).status_code == 302   # -> /results


def test_admin_port_serves_everything():
    c = appmod.app.test_client()
    assert c.get("/competitors").status_code == 200    # default test port = admin


# --- Admin unlock gate -----------------------------------------------------

def test_no_gate_without_password():
    c = appmod.app.test_client()
    assert c.get("/competitors").status_code == 200


def test_gate_blocks_until_unlocked(cfg):
    cfg.set_admin_password("pw")
    c = appmod.app.test_client()
    assert c.get("/competitors").status_code == 302                  # -> /unlock
    assert c.post("/api/competitors", json={}).status_code == 401    # API locked
    assert c.post("/unlock", data={"password": "nope"}).status_code == 401
    ok = c.post("/unlock", data={"password": "pw", "next": "/competitors"})
    assert ok.status_code == 302
    assert c.get("/competitors").status_code == 200                  # unlocked session


def test_config_page_renders(cfg):
    html = appmod.app.test_client().get("/config").get_data(as_text=True)
    assert "config.json" in html and "Admin password" in html
