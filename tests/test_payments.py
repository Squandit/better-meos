"""Online entry + PayPal: the server prices, captures and verifies every payment.

PayPal itself is replaced by ``FakePayPal`` (patched over ``payments._http``),
which behaves like the Orders v2 API closely enough to exercise every branch:
token, create, capture (incl. idempotent retries), get, declines and tampering.
"""

import itertools
import json
from decimal import Decimal

import pytest

import app as appmod
import db
import notify
import online_entry
import payments
import store


_order_ids = itertools.count(1)


class FakePayPal:
    def __init__(self):
        self.orders = {}          # id -> {"amount", "currency", "ref", "status"}
        self.captures = 0         # how many real captures happened
        self.request_ids = {}     # PayPal-Request-Id -> cached response
        self.capture_override = None   # callable(order) -> (status, body)
        self.calls = []

    def __call__(self, method, url, *, body, headers, timeout=20.0):
        path = url.split("paypal.com", 1)[1]
        self.calls.append((method, path))
        if path == "/v1/oauth2/token":
            return 200, {"access_token": "tok", "expires_in": 3600}
        rid = headers.get("PayPal-Request-Id")
        if rid and rid in self.request_ids:
            return self.request_ids[rid]
        payload = json.loads(body) if body else {}
        if method == "POST" and path == "/v2/checkout/orders":
            unit = payload["purchase_units"][0]
            oid = f"ORDER{next(_order_ids)}"  # unique across tests, like PayPal
            self.orders[oid] = {"amount": unit["amount"]["value"],
                                "currency": unit["amount"]["currency_code"],
                                "ref": unit["custom_id"], "status": "APPROVED"}
            out = (201, {"id": oid, "status": "CREATED"})
        elif method == "POST" and path.endswith("/capture"):
            oid = path.split("/")[-2]
            order = self.orders.get(oid)
            if order is None:
                return 404, {"name": "RESOURCE_NOT_FOUND"}
            if order["status"] == "COMPLETED":
                return 422, {"details": [{"issue": "ORDER_ALREADY_CAPTURED"}]}
            if self.capture_override:
                out = self.capture_override(oid, order)
            else:
                order["status"] = "COMPLETED"
                self.captures += 1
                out = (201, self._repr(oid, order))
        elif method == "GET" and path.startswith("/v2/checkout/orders/"):
            oid = path.split("/")[-1]
            order = self.orders.get(oid)
            out = (200, self._repr(oid, order)) if order else (404, {})
        else:
            raise AssertionError(f"unexpected PayPal call {method} {path}")
        if rid:
            self.request_ids[rid] = out
        return out

    @staticmethod
    def _repr(oid, order, *, cap_status="COMPLETED", value=None):
        return {"id": oid, "status": order["status"], "purchase_units": [{
            "reference_id": order["ref"],
            "payments": {"captures": [{
                "id": f"CAP-{oid}", "status": cap_status, "custom_id": order["ref"],
                "amount": {"currency_code": order["currency"],
                           "value": value or order["amount"]}}]}}]}


@pytest.fixture
def paypal(cfg, monkeypatch):
    cfg.save({"paypal_client_id": "client", "paypal_client_secret": "s3cr3t-PAYPAL",
              "fee_senior": 10, "family_cap": 25})
    fake = FakePayPal()
    monkeypatch.setattr(payments, "_http", fake)
    payments._token.update(value=None, expires=0.0, key=None)
    return fake


_card = iter(range(9400001, 9499999))


def _cart(n=1, **over):
    cid = store.class_options()[0]["id"]
    items = []
    for i in range(n):
        item = {"name": f"Payer {next(_card)}", "club": "PAY", "card": str(next(_card)),
                "classId": cid}
        item.update(over)
        items.append(item)
    return items


def _order(c, cart, email="payer@example.com"):
    return c.post("/api/online-entry/order", json={"entries": cart, "email": email})


# --- Pricing ---------------------------------------------------------------

def test_quote_uses_decimal_and_family_cap(paypal):
    q = payments.quote(["senior"] * 3)
    assert q["subtotal"] == Decimal("30.00") and q["total"] == Decimal("25.00")
    assert q["capped"] is True
    assert payments.quote(["senior"])["total"] == Decimal("10.00")


def test_client_cannot_choose_a_cheaper_type(paypal):
    # A browser claiming everyone is a junior still pays the senior fee.
    import config
    config.save({"fee_junior": 1})
    c = appmod.app.test_client()
    r = _order(c, _cart(1, type="junior"))
    oid = r.get_json()["orderID"]
    assert paypal.orders[oid]["amount"] == "10.00"


# --- Happy path --------------------------------------------------------------

def test_paid_order_enters_only_after_verified_capture(paypal):
    c = appmod.app.test_client()
    cart = _cart(3)
    r = _order(c, cart)
    assert r.status_code == 200
    oid = r.get_json()["orderID"]
    # Server priced it: 3 x 10 capped at 25, not whatever the page said.
    assert paypal.orders[oid]["amount"] == "25.00"
    # Nobody is entered before the money is captured.
    assert all(store.find_by_card(int(i["card"])) is None for i in cart)

    r = c.post("/api/online-entry/capture", json={"orderID": oid})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True
    assert all(x["ok"] for x in body["results"]) and body["reference"] == f"CAP-{oid}"
    assert all(store.find_by_card(int(i["card"])) for i in cart)
    assert db.get_order_by_paypal(oid)["status"] == "completed"
    # The Economy page sees them as paid, for exactly what was charged.
    entered = [store.find_by_card(int(i["card"])) for i in cart]
    assert round(sum(c["paid"] for c in entered), 2) == 25.00
    assert {c["pay_method"] for c in entered} == {"PayPal"}
    assert all(store.fee_due(c) == c["paid"] for c in entered)


def test_capture_is_idempotent(paypal):
    c = appmod.app.test_client()
    oid = _order(c, _cart()).get_json()["orderID"]
    first = c.post("/api/online-entry/capture", json={"orderID": oid}).get_json()
    again = c.post("/api/online-entry/capture", json={"orderID": oid}).get_json()
    assert paypal.captures == 1
    assert first["results"] == again["results"]


def test_receipt_built_from_server_data(paypal, monkeypatch):
    sent = {}
    monkeypatch.setattr(notify, "is_enabled", lambda: True)
    monkeypatch.setattr(notify, "_send", lambda to, subj, body: sent.update(to=to, body=body) or True)
    c = appmod.app.test_client()
    cart = _cart()
    oid = _order(c, cart, email="receipt@example.com").get_json()["orderID"]
    body = c.post("/api/online-entry/capture", json={"orderID": oid}).get_json()
    assert body["emailSent"] is True and sent["to"] == "receipt@example.com"
    assert cart[0]["name"] in sent["body"] and f"CAP-{oid}" in sent["body"]


# --- Things that must never lead to a free or wrong entry --------------------

def test_unknown_order_id_is_refused(paypal):
    c = appmod.app.test_client()
    r = c.post("/api/online-entry/capture", json={"orderID": "NOTOURS1"})
    assert r.status_code == 400
    assert paypal.captures == 0


def test_amount_mismatch_is_not_entered(paypal):
    c = appmod.app.test_client()
    cart = _cart()
    oid = _order(c, cart).get_json()["orderID"]

    def cheap(oid_, order):
        order["status"] = "COMPLETED"
        return 201, FakePayPal._repr(oid_, order, value="0.01")
    paypal.capture_override = cheap
    r = c.post("/api/online-entry/capture", json={"orderID": oid})
    assert r.status_code == 400
    assert store.find_by_card(int(cart[0]["card"])) is None
    row = db.get_order_by_paypal(oid)
    assert row["status"] == "failed" and "expected 10.00" in row["note"]


def test_pending_capture_is_not_entered(paypal):
    c = appmod.app.test_client()
    cart = _cart()
    oid = _order(c, cart).get_json()["orderID"]

    def held(oid_, order):
        return 201, FakePayPal._repr(oid_, {**order, "status": "COMPLETED"},
                                     cap_status="PENDING")
    paypal.capture_override = held
    assert c.post("/api/online-entry/capture", json={"orderID": oid}).status_code == 400
    assert store.find_by_card(int(cart[0]["card"])) is None
    assert db.get_order_by_paypal(oid)["status"] == "pending"


def test_stale_cart_is_not_charged(paypal):
    # The card gets registered at the desk between order and capture: the
    # server notices before capturing, so the buyer is never charged.
    c = appmod.app.test_client()
    cart = _cart()
    oid = _order(c, cart).get_json()["orderID"]
    store.create_competitor({"name": "Desk Entry", "class_id": cart[0]["classId"],
                             "card_number": int(cart[0]["card"])})
    r = c.post("/api/online-entry/capture", json={"orderID": oid})
    assert r.status_code == 400 and "not been taken" in r.get_json()["error"]
    assert paypal.captures == 0
    assert db.get_order_by_paypal(oid)["status"] == "rejected"


def test_pressing_pay_again_supersedes_the_unpaid_checkout(paypal):
    # Buyer closes the PayPal window and presses Pay again: the first order is
    # retired and can never be captured, so they can't be charged twice.
    c = appmod.app.test_client()
    cart = _cart()
    first = _order(c, cart).get_json()["orderID"]
    second = _order(c, cart).get_json()["orderID"]
    assert db.get_order_by_paypal(first)["status"] == "superseded"
    r = c.post("/api/online-entry/capture", json={"orderID": first})
    assert r.status_code == 400 and "not been charged" in r.get_json()["error"]
    assert paypal.captures == 0
    assert c.post("/api/online-entry/capture", json={"orderID": second}).status_code == 200
    assert paypal.captures == 1


def test_card_being_paid_for_is_held(paypal):
    c = appmod.app.test_client()
    cart = _cart()
    oid = _order(c, cart).get_json()["orderID"]
    db.update_order(db.get_order_by_paypal(oid)["id"], status="capturing")
    r = _order(c, _cart(1, card=cart[0]["card"]))
    assert r.status_code == 400 and "another checkout" in r.get_json()["error"]


def test_lost_capture_response_retries_without_double_charge(paypal):
    c = appmod.app.test_client()
    cart = _cart()
    oid = _order(c, cart).get_json()["orderID"]
    real = paypal.capture_override

    def captured_then_network_dies(oid_, order):
        order["status"] = "COMPLETED"
        paypal.captures += 1
        raise payments.PayPalError("could not reach PayPal: timed out")
    paypal.capture_override = captured_then_network_dies
    r = c.post("/api/online-entry/capture", json={"orderID": oid})
    assert r.status_code == 400 and "won't be charged twice" in r.get_json()["error"]
    assert db.get_order_by_paypal(oid)["status"] == "capturing"
    assert store.find_by_card(int(cart[0]["card"])) is None
    # Pressing pay again: PayPal says "already captured", we verify and enter.
    paypal.capture_override = real
    r = c.post("/api/online-entry/capture", json={"orderID": oid})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert paypal.captures == 1
    assert store.find_by_card(int(cart[0]["card"])) is not None


def test_not_approved_is_a_refusal_not_a_stuck_order(paypal):
    c = appmod.app.test_client()
    oid = _order(c, _cart()).get_json()["orderID"]
    paypal.capture_override = lambda oid_, order: (
        422, {"details": [{"issue": "ORDER_NOT_APPROVED"}]})
    r = c.post("/api/online-entry/capture", json={"orderID": oid})
    assert r.status_code == 400 and "not been charged" in r.get_json()["error"]
    row = db.get_order_by_paypal(oid)
    assert row["status"] == "created" and row["capture_attempt"] == 1


def test_declined_card_reopens_the_checkout(paypal):
    c = appmod.app.test_client()
    oid = _order(c, _cart()).get_json()["orderID"]
    paypal.capture_override = lambda oid_, order: (
        422, {"details": [{"issue": "INSTRUMENT_DECLINED"}]})
    r = c.post("/api/online-entry/capture", json={"orderID": oid})
    assert r.status_code == 400 and "declined" in r.get_json()["error"]
    assert db.get_order_by_paypal(oid)["status"] == "created"
    paypal.capture_override = None
    assert c.post("/api/online-entry/capture", json={"orderID": oid}).status_code == 200


def test_cart_validation(paypal):
    c = appmod.app.test_client()
    one = _cart()[0]
    assert _order(c, []).status_code == 400
    assert _order(c, [one, dict(one, name="Twin")]).status_code == 400   # card twice
    assert _order(c, [dict(one, classId=999999)]).status_code == 400
    assert _order(c, [dict(one, card="abc")]).status_code == 400
    assert _order(c, [dict(one, name="x" * 200)]).status_code == 400
    assert _order(c, _cart(online_entry.MAX_CART + 1)).status_code == 400
    assert _order(c, [one], email="not-an-email").status_code == 400
    assert paypal.orders == {}


def test_paypal_not_configured_refuses_paid_entries(cfg):
    cfg.save({"fee_senior": 10})
    r = _order(appmod.app.test_client(), _cart())
    assert r.status_code == 400 and "on the day" in r.get_json()["error"]


def test_entries_close_is_enforced(paypal):
    import config
    config.save({"entry_close": "2000-01-01T09:00"})
    r = _order(appmod.app.test_client(), _cart())
    assert r.status_code == 400 and "closed" in r.get_json()["error"]


def test_switching_online_entry_off_closes_the_page(paypal):
    import config
    config.save({"online_entry_open": False, "entry_message": "Parking at the <b>school</b>"},
                target="event")
    c = appmod.app.test_client()
    r = _order(c, _cart())
    assert r.status_code == 400 and "closed" in r.get_json()["error"]
    html = c.get("/enter").get_data(as_text=True)
    assert '"open": false' in html
    assert "Parking at the \\u003cb\\u003eschool" in html     # JSON-escaped, shown as text


def test_order_creation_is_rate_limited(paypal):
    c = appmod.app.test_client()
    codes = [_order(c, _cart()).status_code
             for _ in range(online_entry._RATE_LIMITS["order"] + 1)]
    assert codes[-1] == 400 and codes[0] == 200


def test_free_event_enters_directly(cfg):
    cfg.save({"fee_senior": 0})
    cart = _cart()
    r = _order(appmod.app.test_client(), cart, email="")
    assert r.status_code == 200 and r.get_json()["free"] is True
    assert store.find_by_card(int(cart[0]["card"])) is not None


# --- Operator recovery -------------------------------------------------------

def test_reconcile_finishes_an_order_paid_but_never_captured_by_us(paypal):
    # The buyer paid, but their tab closed before our capture call returned.
    c = appmod.app.test_client()
    cart = _cart()
    oid = _order(c, cart).get_json()["orderID"]
    paypal.orders[oid]["status"] = "COMPLETED"
    row = db.get_order_by_paypal(oid)
    r = c.post(f"/api/orders/{row['id']}/reconcile")
    assert r.status_code == 200 and r.get_json()["status"] == "completed"
    assert store.find_by_card(int(cart[0]["card"])) is not None


def test_paid_but_unenterable_is_flagged_for_refund(paypal, monkeypatch):
    c = appmod.app.test_client()
    cart = _cart()
    oid = _order(c, cart).get_json()["orderID"]
    # Simulate the entry failing *after* the money was taken.
    real = store.create_competitor

    def boom(data):
        raise store.StoreError("class is full")
    monkeypatch.setattr(store, "create_competitor", boom)
    body = c.post("/api/online-entry/capture", json={"orderID": oid}).get_json()
    monkeypatch.setattr(store, "create_competitor", real)
    assert body["results"][0]["ok"] is False
    row = db.get_order_by_paypal(oid)
    assert row["status"] == "needs_refund"
    assert c.post(f"/api/orders/{row['id']}/refunded").status_code == 200
    assert db.get_order(row["id"])["status"] == "refunded"
    assert "Online payments" in c.get("/entries").get_data(as_text=True)


def test_verify_capture_rejects_other_orders():
    order = FakePayPal._repr("ORDER9", {"amount": "10.00", "currency": "AUD",
                                        "ref": "5", "status": "COMPLETED"})
    ok = payments.verify_capture(order, amount=Decimal("10"), reference="5", order_id="ORDER9")
    assert ok["state"] == "paid"
    assert payments.verify_capture(order, amount=Decimal("10"), reference="6",
                                   order_id="ORDER9")["state"] == "failed"
    assert payments.verify_capture(order, amount=Decimal("10"), reference="5",
                                   order_id="ORDER8")["state"] == "failed"
    assert payments.verify_capture(order, amount=Decimal("11"), reference="5",
                                   order_id="ORDER9")["state"] == "failed"


def test_paypal_secret_never_reaches_the_browser(paypal):
    c = appmod.app.test_client()
    assert "s3cr3t-PAYPAL" not in c.get("/enter").get_data(as_text=True)
    groups = c.get("/api/config").get_json()["groups"]
    fields = {f["key"]: f for g in groups for f in g["fields"]}
    assert fields["paypal_client_secret"]["value"] == ""
    assert fields["paypal_client_secret"]["is_set"] is True


def test_late_fee_applies_after_the_late_time(paypal):
    import config
    from datetime import datetime
    config.save({"late_fee": 4, "late_fee_from": "2026-10-01T18:00"})
    assert payments.quote(["senior"], now=datetime(2026, 10, 1, 17, 59))["total"] == Decimal("10.00")
    assert payments.quote(["senior"], now=datetime(2026, 10, 1, 18, 0))["total"] == Decimal("14.00")


def test_declared_types_only_when_the_organiser_trusts_them(paypal):
    import config
    config.save({"fee_junior": 4, "family_cap": 0})
    c = appmod.app.test_client()
    oid = _order(c, _cart(1, type="junior")).get_json()["orderID"]
    assert paypal.orders[oid]["amount"] == "10.00"          # ignored by default
    config.save({"trust_member_type": True})
    oid = _order(c, _cart(1, type="junior")).get_json()["orderID"]
    assert paypal.orders[oid]["amount"] == "4.00"
    oid = _order(c, _cart(1, type="free-please")).get_json()["orderID"]
    assert paypal.orders[oid]["amount"] == "10.00"          # unknown type -> senior
