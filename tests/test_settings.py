"""The settings system: event vs computer scope, validation, gear menus."""

import json

import app as appmod
import config
import db
import settings_schema
import store


def test_schema_is_consistent():
    for spec in settings_schema.SCHEMA:
        assert spec.group in settings_schema.GROUPS
        if spec.type == "choice":
            assert spec.default in {v for v, _ in spec.choices}, spec.key
        assert not (spec.type == "password" and spec.scope == "event")


def test_event_value_overrides_computer_default(cfg):
    cfg.save({"fee_senior": 12}, target="computer")
    assert cfg.get("fee_senior") == 12 and cfg.source("fee_senior") == "computer"
    cfg.save({"fee_senior": 7.5})                       # auto -> the open event
    assert cfg.get("fee_senior") == 7.5 and cfg.source("fee_senior") == "event"
    assert cfg.get("fee_senior", include_event=False) == 12
    with open(config._path(), encoding="utf-8") as f:
        assert json.load(f)["fee_senior"] == 12          # event value not in config.json
    cfg.save({}, reset=["fee_senior"])                   # back to the default
    assert cfg.get("fee_senior") == 12


def test_computer_scoped_settings_ignore_the_event(cfg):
    cfg.save({"backup_keep": 9})
    assert db.event_settings().get("backup_keep") is None
    assert cfg.get("backup_keep") == 9


def test_validation_rejects_bad_values(cfg):
    c = appmod.app.test_client()
    for values in ({"slip_paper": "tissue"}, {"backup_keep": 0}, {"fee_senior": "lots"},
                   {"entry_close": "next friday"}):
        r = c.post("/api/settings", json={"target": "event", "values": values})
        assert r.status_code == 400, values
        assert r.get_json()["error"]
    assert cfg.get("slip_paper") == "80mm"


def test_api_lists_page_settings_and_hides_secrets(cfg):
    cfg.save({"ftp_pass": "hunter2"})
    c = appmod.app.test_client()
    d = c.get("/api/settings?page=setup").get_json()
    keys = {f["key"] for g in d["groups"] for f in g["fields"]}
    assert "backup_keep" in keys and "fee_senior" not in keys
    body = c.get("/api/settings").get_data(as_text=True)
    assert "hunter2" not in body
    ftp = next(f for g in d["groups"] for f in g["fields"] if f["key"] == "ftp_pass")
    assert ftp["is_set"] is True and ftp["value"] == ""


def test_saving_a_setting_refreshes_cached_pages(cfg):
    before = db.revision()
    appmod.app.test_client().post("/api/settings", json={"values": {"prize_places": 5}})
    assert db.revision() > before


def test_gear_button_only_on_pages_with_settings():
    c = appmod.app.test_client()
    assert "data-page-settings=\"download\"" in c.get("/download").get_data(as_text=True)
    assert "data-page-settings" not in c.get("/audit").get_data(as_text=True)


def test_settings_page_has_both_targets():
    c = appmod.app.test_client()
    html = c.get("/config").get_data(as_text=True)
    assert "Defaults for every event" in html and "This event" in html
    assert "Showing the defaults" in c.get("/config?target=computer").get_data(as_text=True)


def test_event_settings_travel_with_the_event_file(cfg):
    cfg.save({"prize_places": 7})
    original = store.current_event_path()
    try:
        store.new_event({"name": "Other Event", "date": "2026-11-01"})
        assert cfg.get("prize_places") == 3             # a different event: default
        store.open_event(original)
        assert cfg.get("prize_places") == 7
    finally:
        store.open_event(original)


def test_appearance_settings_reach_the_page(cfg):
    c = appmod.app.test_client()
    cfg.save({"theme": "dark", "accent": "ocean", "text_size": "125"})
    html = c.get("/competitors").get_data(as_text=True)
    assert 'data-theme="dark"' in html and 'data-accent="ocean"' in html and 'data-text="125"' in html
    assert 'data-theme="dark"' in c.get("/start").get_data(as_text=True)


def test_a_saved_api_key_can_be_removed(cfg):
    cfg.save({"eventor_api_key": "old-key"}, target="computer")
    cfg.save({"eventor_api_key": ""}, target="computer")        # blank keeps it
    assert cfg.get_str("eventor_api_key") == "old-key"
    c = __import__("app").app.test_client()
    c.post("/api/settings", json={"target": "computer", "values": {},
                                  "reset": ["eventor_api_key"]})
    assert cfg.get_str("eventor_api_key") == ""
