"""The shared runner database and the ported PayPal entry page backend."""

import app as appmod
import runners
import store


def _seed_members():
    runners.record(9100001, "Entry Ann", "REGOC", "M21A")
    runners.record(9100002, "Junior Jim", "REGOC", "M21A")


def test_runner_import_search_lookup():
    runners.import_csv("name,club,card\nImp Ivy,IMPOC,9100009\n")
    assert runners.search("imp")[0]["name"] == "Imp Ivy"
    ivy = runners.lookup(9100009)
    assert ivy["name"] == "Imp Ivy" and ivy["club"] == "IMPOC"


def test_entry_page_renders_with_config():
    c = appmod.app.test_client()
    html = c.get("/enter").get_data(as_text=True)
    assert c.get("/enter").status_code == 200
    assert "window.ENTRY_CONFIG" in html
    assert store.EVENT["name"] in html
    assert "<!-- CONFIG_INJECT -->" not in html  # placeholder was replaced


def test_entry_lookup_and_classes_endpoints():
    _seed_members()
    c = appmod.app.test_client()
    assert "<Class" in c.get("/get-classes").get_data(as_text=True)
    found = c.get("/search-competitors?q=entry%20ann").get_json()
    assert found and found[0]["name"] == "Entry Ann"
    one = c.get("/lookup-competitor?name=Entry Ann").get_json()
    assert one["club"] == "REGOC"
    assert c.get("/lookup-competitor?name=Nobody").get_json() is None


def test_entry_check_entered():
    c = appmod.app.test_client()
    # Seeded competitor "Test Runner" exists in the active event.
    assert c.get("/check-entered?name=Test%20Runner").get_json()["entered"] is True
    assert c.get("/check-entered?name=Ghost").get_json()["entered"] is False


def test_entry_submit_creates_competitor_and_rejects_dupe_card():
    c = appmod.app.test_client()
    cid = store.class_options()[0]["id"]
    r = c.get(f"/submit-entry?class={cid}&name=Paid Pat&club=REGOC&card=9100050")
    assert "<Status>OK</Status>" in r.get_data(as_text=True)
    assert store.find_by_card(9100050)["name"] == "Paid Pat"
    # Same card again -> Fail with Info
    r2 = c.get(f"/submit-entry?class={cid}&name=Other&club=&card=9100050")
    body = r2.get_data(as_text=True)
    assert "<Status>Fail</Status>" in body and "<Info>" in body


def test_entry_log_and_results():
    c = appmod.app.test_client()
    r = c.post("/log-entries", json={
        "entries": [{"name": "Paid Pat", "className": "M21A", "price": 10.0, "ok": True}],
        "total": 10.0, "paypalId": "TESTREF", "email": "",
    })
    assert r.get_json()["emailSent"] is False  # no SMTP configured
    results = c.get("/get-results").get_json()
    assert isinstance(results, list)
    assert all("className" in cls and "competitors" in cls for cls in results)


def test_entry_config_is_xss_escaped():
    # A club name containing </script> must be neutralised in the injected config.
    cid = store.class_options()[0]["id"]
    c = appmod.app.test_client()
    c.get(f"/submit-entry?class={cid}&name=XSS Xavier&club=" +
          "%3C/script%3E%3Cscript%3Ealert(1)%3C/script%3E&card=9109999")
    html = c.get("/enter").get_data(as_text=True)
    # The raw breakout sequence must not appear inside the injected config.
    assert "</script><script>alert(1)" not in html
    assert "\\u003c/script\\u003e" in html  # escaped form present


def test_log_entries_rejects_bad_email_and_caps(monkeypatch):
    import notify
    monkeypatch.setattr(notify, "is_enabled", lambda: True)
    sent = {"called": False}
    monkeypatch.setattr(notify, "_send", lambda *a, **k: sent.__setitem__("called", True) or True)

    c = appmod.app.test_client()
    # Malformed payload must not 500, and bad email must not send.
    r = c.post("/log-entries", json={"email": "not-an-email", "entries": "junk"})
    assert r.status_code == 200 and r.get_json()["emailSent"] is False
    assert sent["called"] is False

    r2 = c.post("/log-entries", json={"email": "ok@example.com",
                                      "entries": [{"name": "A", "price": "bad"}],
                                      "total": "x"})
    assert r2.status_code == 200 and r2.get_json()["emailSent"] is True


def test_entry_pwa_files():
    c = appmod.app.test_client()
    assert c.get("/manifest.json").status_code == 200
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.content_type
