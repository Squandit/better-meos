"""Registration page + reader autofill plumbing (last-seen card, port discovery)."""

import app as appmod
import si_reader
import store


def test_last_seen_updates_on_read():
    before = si_reader.last_seen()["seq"]
    si_reader.simulate(store.coerce_card({"card_number": 8635918, "punches": []}))
    seen = si_reader.last_seen()
    assert seen["card_number"] == 8635918
    assert seen["seq"] > before          # a fresh tap bumps the sequence


def test_register_page_renders():
    assert appmod.app.test_client().get("/register").status_code == 200


def test_last_card_and_ports_endpoints():
    c = appmod.app.test_client()
    lc = c.get("/api/reader/last-card").get_json()
    assert "seq" in lc and "card_number" in lc
    ports = c.get("/api/reader/ports").get_json()
    assert "ports" in ports and "detected" in ports


def test_port_discovery_never_raises():
    # No hardware assumptions -- just must not crash if pyserial/ports are absent.
    assert isinstance(si_reader.list_serial_ports(), list)
    si_reader.autodetect_port()
