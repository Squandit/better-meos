"""Economy: what each runner owes, payments, club invoices."""

import pytest

import app as appmod
import config
import store


@pytest.fixture
def money_class(cfg):
    course = store.get_class(next(c["id"] for c in store.class_options()
                                  if c["name"] == "M21A"))["course_id"]
    cls = store.create_class({"name": "Money", "course_id": course, "fee": 12})
    made = [store.create_competitor({"name": n, "club": club, "class_id": cls["id"], **extra})["id"]
            for n, club, extra in (("Pay Pat", "Alpha OC", {}),
                                   ("Hire Hal", "Alpha OC", {"hired": True}),
                                   ("Own Fee", "Beta OC", {"fee": "7.50"}))]
    yield cls, made
    for cid in made:
        store.delete_competitor(cid)
    store.delete_class(cls["id"])


def test_what_each_runner_owes(money_class):
    _, (pat, hal, own) = money_class
    config.save({"hire_card_fee": 3}, target="event")
    assert store.fee_due(store.get_competitor(pat)) == 12
    assert store.fee_due(store.get_competitor(hal)) == 15        # class fee + hire card
    assert store.fee_due(store.get_competitor(own)) == 7.5       # their own fee wins


def test_recording_payments(money_class):
    _, (pat, hal, own) = money_class
    c = appmod.app.test_client()
    assert c.post(f"/api/competitors/{pat}/payment", json={"method": "Cash"}).status_code == 200
    assert store.get_competitor(pat)["paid"] == 12                # the whole amount due
    c.post(f"/api/competitors/{own}/payment", json={"method": "Card", "amount": "5"})
    assert c.post(f"/api/competitors/{hal}/payment", json={"amount": "-1"}).status_code == 400
    money = store.payments_summary()
    rows = {r["name"]: r for r in money["runners"]}
    assert rows["Own Fee"]["owing"] == 2.5 and rows["Pay Pat"]["owing"] == 0
    methods = dict(money["methods"])
    assert methods["Cash"] >= 12 and methods["Card"] >= 5
    alpha = next(x for x in money["clubs"] if x["club"] == "Alpha OC")
    assert alpha["entries"] >= 2 and alpha["owing"] >= 12
    assert any(a["action"] == "payment" for a in store.audit_log(20))
    c.post(f"/api/competitors/{pat}/payment", json={"method": "", "amount": 0})   # undo
    assert store.get_competitor(pat)["paid"] == 0


def test_editor_saves_fee_and_payment(money_class):
    _, (pat, _, _) = money_class
    c = appmod.app.test_client()
    r = c.put(f"/api/competitors/{pat}", json={"fee": "20", "paid": "20", "pay_method": "Bank transfer"})
    assert r.status_code == 200
    comp = c.get(f"/api/competitors/{pat}").get_json()["competitor"]
    assert comp["fee"] == 20 and comp["paid"] == 20 and comp["due"] == 20
    c.put(f"/api/competitors/{pat}", json={"fee": ""})             # back to the class fee
    assert store.fee_due(store.get_competitor(pat)) == 12


def test_economy_page_and_invoices(money_class):
    config.save({"invoice_text": "Pay to BSB 000-000 acct 1234"}, target="event")
    c = appmod.app.test_client()
    html = c.get("/economy").get_data(as_text=True)
    assert "Pay Pat" in html and "Alpha OC" in html and "All invoices" in html
    one = c.get("/export/invoices.pdf?club=Alpha OC")
    assert one.status_code == 200 and one.data.startswith(b"%PDF")
    assert c.get("/export/invoices.pdf").status_code == 200


def test_family_cap_is_split_so_the_books_match_the_charge():
    from decimal import Decimal
    import payments
    shares = payments._shares([Decimal(10)] * 3, Decimal("25.00"))
    assert sum(shares) == Decimal("25.00") and shares[0] == Decimal("8.33")
    uneven = payments._shares([Decimal(10), Decimal(5)], Decimal("12.00"))
    assert uneven == [Decimal("8.00"), Decimal("4.00")]
    assert payments._shares([], Decimal(0)) == []
