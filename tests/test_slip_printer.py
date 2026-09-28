"""Split slips printed by the app itself: layout, drawing, auto-print rules,
the print queue and the API."""

import pytest

import app as appmod
import si_reader
import slip_printer
import store


@pytest.fixture
def printer(cfg, tmp_path, monkeypatch):
    """Direct printing on, slips saved as PNGs in a temp folder."""
    monkeypatch.setenv("BMEOS_PRINT_DIR", str(tmp_path / "slips"))
    return tmp_path / "slips"


def printed(folder):
    slip_printer.wait_idle()
    return sorted(folder.glob("*.png")) if folder.exists() else []


def m21a():
    return next(c for c in store.class_options() if c["name"] == "M21A")["id"]


def runner(name, card, punches, finish="10:40:00"):
    return store.create_competitor({
        "name": name, "class_id": m21a(), "card_number": card, "start": "10:00:00"}), {
        "card_number": card, "start": store.parse_clock("10:00:00"),
        "finish": store.parse_clock(finish),
        "punches": [(c, store.parse_clock(t)) for c, t in punches]}


FULL = [(138, "10:10:00"), (130, "10:15:00"), (142, "10:25:00"), (155, "10:35:00")]


def texts(slip):
    return [c[2] for line in slip["lines"] for c in line.get("cells", [])]


def test_slip_layout_has_the_run(cfg):
    comp, card = runner("Layout Lou", 9460001, FULL)
    try:
        store.apply_card_read(card)
        slip = slip_printer.slip_lines(comp["id"])
        words = texts(slip)
        assert "Layout Lou" in words and "OK" in words and "Test Event" in words
        assert "138" in words and "155" in words         # one row per control
        assert slip["status"] == "ok" and slip["competitor_id"] == comp["id"]
        assert slip_printer.slip_lines(999999) is None
    finally:
        store.delete_competitor(comp["id"])


def test_mispunch_slip_says_what_was_missed(cfg):
    comp, card = runner("Missed Mo", 9460002, FULL[:2] + FULL[3:])
    try:
        store.apply_card_read(card)
        words = texts(slip_printer.slip_lines(comp["id"]))
        assert "missing" in words and any("missed control 142" in w for w in words)
    finally:
        store.delete_competitor(comp["id"])


def test_render_is_black_and_white_at_printer_width():
    img = slip_printer.render(slip_printer.test_slip(), width_px=576, dpi=203)
    assert img.width == 576 and img.height > 200
    hist = img.histogram()
    assert sum(hist[1:255]) == 0                        # no greys
    # Something was drawn, and not everything.
    assert 0 < hist[0] < img.width * img.height / 4


def test_width_follows_the_paper_setting(cfg):
    assert slip_printer.width_px(203) == 575            # 72 mm at 203 dpi
    assert slip_printer.width_px(600, printable_px=4800) == 1701
    cfg.save({"slip_paper": "a4"})
    assert slip_printer.width_px(600) == round(120 / 25.4 * 600)


def test_auto_print_rules():
    assert slip_printer.wanted("ok", "all") and slip_printer.wanted("mp", "all")
    assert slip_printer.wanted("ok", "ok") and not slip_printer.wanted("mp", "ok")
    assert slip_printer.wanted("mp", "not_ok") and not slip_printer.wanted("ok", "not_ok")
    assert not slip_printer.wanted("ok", "off")


def test_card_read_prints_by_the_rule(printer, cfg):
    cfg.save({"auto_print": "not_ok"})
    ok, ok_card = runner("Clean Cleo", 9460003, FULL)
    mp, mp_card = runner("Messy Max", 9460004, FULL[:1])
    try:
        si_reader.simulate(ok_card)
        assert printed(printer) == []                   # OK run, rule is problems only
        si_reader.simulate(mp_card)
        assert len(printed(printer)) == 1
        assert slip_printer.jobs()[0]["title"] == "Splits Messy Max"
        assert slip_printer.jobs()[0]["state"] == "printed"
    finally:
        store.delete_competitor(ok["id"])
        store.delete_competitor(mp["id"])


def test_nothing_prints_when_off_or_through_the_browser(printer, cfg):
    comp, card = runner("Quiet Quin", 9460005, FULL)
    try:
        si_reader.simulate(card)                        # auto_print is off by default
        cfg.save({"auto_print": "all", "print_method": "browser"})
        si_reader.simulate(card)
        assert printed(printer) == []
        assert not slip_printer.direct()
    finally:
        store.delete_competitor(comp["id"])


def test_forwarded_reads_print_at_the_station_not_the_primary(printer, cfg):
    cfg.save({"auto_print": "all", "station_token": "tok"})
    comp, card = runner("Remote Rae", 9460006, FULL)
    try:
        c = appmod.app.test_client()
        body = {"card_number": 9460006, "start": "10:00:00", "finish": "10:40:00",
                "punches": [{"code": code, "time": t} for code, t in FULL]}
        r = c.post("/api/station/push", json=body, headers={"X-Station-Token": "tok"})
        assert r.status_code == 200
        assert printed(printer) == []                   # the primary didn't print it...
        slip = r.get_json()["slip"]
        assert "Remote Rae" in texts(slip)              # ...it sent the slip back
    finally:
        store.delete_competitor(comp["id"])


def test_failed_print_is_reported_and_the_queue_carries_on(printer, monkeypatch):
    calls = []

    def flaky(slip, output=None):
        calls.append(slip["title"])
        if len(calls) == 1:
            raise slip_printer.PrintError("Printer out of paper")
        return "Receipt"

    monkeypatch.setattr(slip_printer, "print_now", flaky)
    slip_printer.send(slip_printer.test_slip())
    slip_printer.send(slip_printer.test_slip())
    slip_printer.wait_idle()
    newest, older = slip_printer.jobs()[:2]
    assert older["state"] == "failed" and "out of paper" in older["error"]
    assert newest["state"] == "printed"
    assert slip_printer.status()["failed"][0]["error"] == "Printer out of paper"


def test_print_api(printer):
    c = appmod.app.test_client()
    cid = next(iter(store._competitors))
    assert c.post(f"/api/print/slip/{cid}").get_json()["ok"]
    assert c.post("/api/print/test").get_json()["job"]["reason"] == "test"
    assert c.post("/api/print/slip/999999").status_code == 404
    assert len(printed(printer)) == 2
    assert c.get("/api/printers").get_json()["available"]
    png = c.get(f"/slip/{cid}.png")
    assert png.status_code == 200 and png.data[:4] == b"\x89PNG"
    html = c.get("/download").get_data(as_text=True)
    assert "data-print-line" in html and "data-print-test" in html


def test_without_windows_the_browser_prints(cfg):
    c = appmod.app.test_client()
    assert not slip_printer.available()
    assert c.post("/api/print/test").status_code == 400
    assert "data-print-line" not in c.get("/download").get_data(as_text=True)
