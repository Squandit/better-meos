"""
Online entry with payment: the server side of the entry page (``/enter``).

The browser only ever sends *what* people want to enter. Everything that
decides money or creates competitors happens here:

1. :func:`start_order` checks every entry (class exists, card not taken, not
   already entered, not in someone else's open checkout), prices the cart with
   the fees in Settings, freezes the cart in ``online_orders`` and creates a
   PayPal order for exactly that amount. A free event skips PayPal and enters
   people straight away.
2. The buyer approves the payment in PayPal's own window.
3. :func:`capture_order` re-checks the frozen cart (if it has gone stale the
   payment is *not* taken), captures the order, and verifies PayPal's answer:
   COMPLETED, our amount, our currency, our reference. Only then are the
   competitors created, from the frozen cart, never from the request.

Every step is recorded on the order row so the operator can see (and, via
:func:`reconcile_order`, recover) anything that stops half-way. A capture is
idempotent: retrying it returns the first outcome, and PayPal is sent the same
request id so it can't charge twice.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta

import config
import db
import events
import notify
import payments
import runners
import store
from store import StoreError

log = logging.getLogger("online_entry")

MAX_CART = 20
MAX_TEXT = 80
# A PayPal order can be approved for about three hours; after that we treat the
# checkout as abandoned and release the cards it was holding.
ORDER_TTL = timedelta(hours=3)
# SI cards are at most 7 digits today (SIAC 8xxxxxx, pCard 4xxxxxx).
MAX_CARD = 99_999_999

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# One capture at a time: the re-check -> capture -> create sequence must not
# interleave with another checkout for the same card.
_capture_lock = threading.Lock()


class EntryError(StoreError):
    """Shown to the entrant as-is (the API turns it into a 400). ``code`` lets
    the page react to specific cases (e.g. 'declined' -> let PayPal retry)."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Rate limiting (per client, in memory)
# ---------------------------------------------------------------------------

_RATE_WINDOW = 600.0
_RATE_LIMITS = {"order": 20, "capture": 40}
_hits: dict[tuple[str, str], deque] = {}
_hits_lock = threading.Lock()


def _rate_limit(kind: str, client_key: str | None) -> None:
    now = time.monotonic()
    key = (kind, client_key or "?")
    with _hits_lock:
        q = _hits.setdefault(key, deque())
        while q and now - q[0] > _RATE_WINDOW:
            q.popleft()
        if len(q) >= _RATE_LIMITS[kind]:
            raise EntryError("Too many attempts from this device. Wait a few "
                             "minutes and try again.")
        q.append(now)


def reset_rate_limits() -> None:
    """Forget all rate-limit history (tests)."""
    with _hits_lock:
        _hits.clear()


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

MEMBER_TYPES = ("senior", "junior", "concession")


def _member_type(item: dict) -> str:
    """
    Which fee applies to one entrant.

    Nothing server-side knows anyone's membership type, so by default everyone
    pays the senior fee and whatever the browser claims is ignored (letting the
    client declare itself a junior would be a way to pay less). An organiser
    who is happy to trust entrants turns on "Let entrants choose junior /
    concession", and then the declared type is used.
    """
    if config.get("trust_member_type") and item.get("type") in MEMBER_TYPES:
        return item["type"]
    return "senior"


def payment_required() -> bool:
    return any(payments.quote([t])["total"] > 0 for t in (
        MEMBER_TYPES if config.get("trust_member_type") else ("senior",)))


def _entries_closed(now: datetime | None = None) -> bool:
    """True once the 'Entry close time' in Settings has passed. The setting is
    free text; only an ISO date/time (what the page's countdown reads) is
    enforced, anything else is display-only."""
    if not config.get("online_entry_open"):
        return True
    text = config.get_str("entry_close").strip()
    if not text:
        return False
    try:
        close = datetime.fromisoformat(text)
    except ValueError:
        return False
    current = now or datetime.now(close.tzinfo)
    return current >= close


def _text(value, field: str, *, required: bool) -> str:
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise EntryError(f"{field} is required")
    if len(text) > MAX_TEXT:
        raise EntryError(f"{field} is too long")
    if any(ord(ch) < 32 for ch in text):
        raise EntryError(f"{field} has invalid characters")
    return text


# Order states where money may be moving or has moved: their cards are held.
# A plain 'created' order (buyer hasn't paid) holds nothing; a newer checkout
# for the same card simply supersedes it (see start_order).
_MONEY_STATES = ("capturing", "pending", "paid")


def _cards_held_by_open_orders(exclude_order: int | None = None) -> set[int]:
    """Cards in checkouts whose payment is being captured or held by PayPal."""
    held = set()
    for order in db.all_orders():
        if order["id"] == exclude_order or order["status"] not in _MONEY_STATES:
            continue
        for item in json.loads(order["entries_json"]):
            held.add(item["card"])
    return held


def _supersede_unpaid(cards: set[int]) -> None:
    """Retire unpaid checkouts for these cards (someone pressed Pay again, or
    closed the PayPal window and started over). Their PayPal approval can never
    be captured afterwards, so nobody pays twice."""
    for order in db.all_orders():
        if order["status"] != "created":
            continue
        if cards & {item["card"] for item in json.loads(order["entries_json"])}:
            db.update_order(order["id"], status="superseded",
                            note="replaced by a newer checkout")


def _check_items(items: list[dict], *, exclude_order: int | None = None) -> None:
    """Every entry must still be enterable right now. Caller holds store._lock."""
    held = _cards_held_by_open_orders(exclude_order)
    per_class: dict[int, int] = {}
    for item in items:
        per_class[item["class_id"]] = per_class.get(item["class_id"], 0) + 1
    for item in items:
        cls = store.get_class(item["class_id"])
        if cls is None:
            raise EntryError(f"The course for {item['name']} is no longer offered")
        if cls.get("online_entry") is False:
            raise EntryError(f"{cls['name']} isn't open for online entry")
        if not store.class_open_for_entry(cls["id"], per_class[cls["id"]]):
            raise EntryError(f"{cls['name']} is full")
        owner = store.find_by_card(item["card"])
        if owner is not None:
            raise EntryError(f"SI card {item['card']} is already registered")
        if item["card"] in held:
            raise EntryError(f"SI card {item['card']} is being entered in another "
                             f"checkout right now")
        lowered = item["name"].lower()
        if any(c["name"].strip().lower() == lowered and c["class_id"] == cls["id"]
               for c in store._competitors.values()):
            raise EntryError(f"{item['name']} is already entered in {cls['name']}")


def clean_cart(raw) -> list[dict]:
    """Validate the browser's cart into ``[{name, club, card, class_id}]``."""
    if not isinstance(raw, list) or not raw:
        raise EntryError("Your cart is empty")
    if len(raw) > MAX_CART:
        raise EntryError(f"At most {MAX_CART} people per checkout")
    items, cards, names = [], set(), set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise EntryError("Your cart is malformed; reload the page")
        name = _text(entry.get("name"), "Name", required=True)
        club = _text(entry.get("club"), "Club", required=False)
        class_id = store._as_int(entry.get("classId"), "Course")
        card = store._as_int(entry.get("card"), "SI card number", minimum=1)
        if card > MAX_CARD:
            raise EntryError(f"SI card {card} isn't a valid card number")
        if card in cards:
            raise EntryError(f"SI card {card} is in your cart twice")
        if name.lower() in names:
            raise EntryError(f"{name} is in your cart twice")
        cards.add(card)
        names.add(name.lower())
        member_type = str(entry.get("type") or "senior").lower()
        items.append({"name": name, "club": club, "card": card, "class_id": class_id,
                      "type": member_type if member_type in MEMBER_TYPES else "senior"})
    return items


# ---------------------------------------------------------------------------
# Order lifecycle
# ---------------------------------------------------------------------------

def _enter_items(items: list[dict]) -> list[dict]:
    """Create a competitor per entry; returns a per-entry outcome list."""
    results = []
    with store.batch(), store.acting_as("online entry"):
        for item in items:
            cls = store.get_class(item["class_id"])
            outcome = {"name": item["name"], "className": cls["name"] if cls else "",
                       "card": item["card"], "club": item["club"]}
            price = float(item.get("price") or 0)
            try:
                comp = store.create_competitor({
                    "name": item["name"], "club": item["club"],
                    "class_id": item["class_id"], "card_number": item["card"],
                    # What they were charged, already paid (Economy page).
                    "fee": price, "paid": price, "pay_method": "PayPal" if price else "",
                })
                runners.record_competitor(comp)
                outcome.update(ok=True, detail="")
            except StoreError as err:
                outcome.update(ok=False, detail=str(err))
            results.append(outcome)
    events.publish("competitor", action="entry")
    return results


def _with_prices(results: list[dict], items: list[dict]) -> list[dict]:
    """Attach the price each person was charged (frozen when the order was made)."""
    for outcome, item in zip(results, items):
        outcome["price"] = float(item.get("price") or 0)
    return results


def start_order(data: dict, *, client_key: str | None = None) -> dict:
    """
    Validate + price a cart. Free: enter everyone now and return the outcome.
    Paid: freeze the cart, create the PayPal order and return its id for the
    PayPal button (``{"orderID", "total", "currency"}``).
    """
    _rate_limit("order", client_key)
    if _entries_closed():
        raise EntryError("Online entries have closed")
    items = clean_cart(data.get("entries"))
    email = str(data.get("email") or "").strip()
    price = payments.quote([_member_type(i) for i in items])
    code = payments.currency()
    now = datetime.now().isoformat(timespec="seconds")

    if price["total"] <= 0:
        for item in items:
            item["price"] = "0"
        with store._lock:
            _check_items(items)
            results = _with_prices(_enter_items(items), items)
        db.insert_order({"created_at": now, "status": "free", "amount": "0",
                         "currency": code, "email": email,
                         "entries_json": json.dumps(items),
                         "note": None})
        return {"free": True, "results": results, "total": 0.0, "currency": code}

    if not payments.paypal_enabled():
        raise EntryError("Online payment isn't set up for this event, so entries "
                         "can't be taken here. Please enter on the day.")
    if not _EMAIL_RE.match(email) or len(email) > 200:
        raise EntryError("Please enter a valid email address for your receipt")

    for item, share in zip(items, price["shares"]):
        item["price"] = payments.money_str(share, code)     # what they actually pay
    with store._lock:
        _check_items(items)
        _supersede_unpaid({i["card"] for i in items})
        order_id = db.insert_order({
            "created_at": now, "status": "created",
            "amount": payments.money_str(price["total"], code), "currency": code,
            "email": email, "entries_json": json.dumps(items),
        })
    names = ", ".join(i["name"] for i in items)
    try:
        paypal_id = payments.paypal_create_order(
            amount=price["total"], reference=str(order_id),
            description=f"{store.EVENT.get('name', '')} entry: {names}",
            request_id=f"bmeos-order-{uuid.uuid4().hex}")
    except payments.PayPalError as err:
        log.error("PayPal create failed for order %s: %s %s", order_id, err, err.body)
        db.update_order(order_id, status="failed", note=f"create: {err}")
        raise EntryError("Couldn't start the PayPal payment. You have not been "
                         "charged; please try again.")
    db.update_order(order_id, paypal_order_id=paypal_id)
    return {"orderID": paypal_id, "total": float(price["total"]), "currency": code}


def _outcome(order: dict, *, email_sent: bool | None = None) -> dict:
    results = json.loads(order["result_json"] or "[]")
    return {"ok": True, "results": results, "total": float(order["amount"]),
            "currency": order["currency"],
            "reference": order["capture_id"] or order["paypal_order_id"],
            "emailSent": bool(email_sent), "status": order["status"]}


def _finish_paid(order: dict, verdict: dict) -> dict:
    """The money is ours: record it, then enter everyone from the frozen cart."""
    db.update_order(order["id"], status="paid", capture_id=verdict["capture_id"],
                    captured_at=datetime.now().isoformat(timespec="seconds"), note=None)
    items = json.loads(order["entries_json"])
    results = _with_prices(_enter_items(items), items)
    failed = [r for r in results if not r["ok"]]
    status = "needs_refund" if failed else "completed"
    note = ("Paid but not entered: " + "; ".join(
        f"{r['name']} ({r['detail']})" for r in failed)) if failed else None
    db.update_order(order["id"], status=status, result_json=json.dumps(results),
                    note=note)
    if failed:
        log.error("order %s paid but %d entr%s failed: %s", order["id"], len(failed),
                  "y" if len(failed) == 1 else "ies", note)
    order = db.get_order(order["id"])
    sent = notify.send_order_receipt(order, results, store.EVENT)
    return _outcome(order, email_sent=sent)


def capture_order(paypal_order_id, *, client_key: str | None = None) -> dict:
    """
    The buyer approved the payment in PayPal: capture it, verify it, enter them.
    Safe to call again for the same order (returns the first outcome).
    """
    _rate_limit("capture", client_key)
    order_key = str(paypal_order_id or "").strip()
    if not order_key:
        raise EntryError("Missing PayPal order")
    with _capture_lock:
        order = db.get_order_by_paypal(order_key)
        if order is None:
            raise EntryError("That payment doesn't belong to this event")
        if order["status"] in ("completed", "needs_refund"):
            return _outcome(order)
        if order["status"] == "pending":
            raise EntryError("PayPal is still reviewing this payment. The "
                             "organiser will confirm your entry once it clears.")
        if order["status"] == "superseded":
            raise EntryError("This checkout was replaced by a newer one. You have "
                             "not been charged for it.")
        if order["status"] not in ("created", "capturing"):
            raise EntryError("This checkout can't be completed; please start again. "
                             "If you were charged, contact the organiser.")

        if order["status"] == "created":
            # Nothing has been attempted yet: last chance to back out without
            # taking any money.
            if datetime.fromisoformat(order["created_at"]) < datetime.now() - ORDER_TTL:
                db.update_order(order["id"], status="rejected", note="checkout expired")
                raise EntryError("This checkout expired before it was paid. You "
                                 "have not been charged; please start again.")
            items = json.loads(order["entries_json"])
            with store._lock:
                try:
                    _check_items(items, exclude_order=order["id"])
                except EntryError as err:
                    db.update_order(order["id"], status="rejected", note=str(err))
                    raise EntryError(f"{err}. Your payment has not been taken.")
            # From here money may move: hold the cards until we know.
            db.update_order(order["id"], status="capturing")

        # Same key for retries of one attempt (PayPal replays the first result,
        # so an unknown outcome can't charge twice); a new key after a decline,
        # or PayPal would just replay the decline.
        request_id = f"bmeos-capture-{order_key}-{order['capture_attempt']}"
        try:
            paid = payments.paypal_capture_order(order_key, request_id=request_id)
        except payments.PayPalError as err:
            log.error("PayPal capture failed for order %s: %s %s", order["id"], err,
                      err.body)
            if err.definite_refusal:
                # PayPal answered "no" (declined card, not approved yet, bad
                # credentials...): no money moved, so the checkout is open again.
                # New request id next time, or PayPal would replay this refusal.
                db.update_order(order["id"], status="created",
                                note=err.issue or f"PayPal HTTP {err.status}",
                                capture_attempt=order["capture_attempt"] + 1)
                if err.issue in ("INSTRUMENT_DECLINED", "PAYER_ACTION_REQUIRED"):
                    raise EntryError("PayPal declined that payment method. Please try "
                                     "again with another card or account.",
                                     code="declined")
                raise EntryError("PayPal couldn't take this payment. You have not "
                                 "been charged; please try again.")
            # Network trouble: we don't know if PayPal took the money. Leave it
            # 'capturing'; a retry (same request id) can't charge twice, and the
            # operator can check it with PayPal from the Entries page.
            raise EntryError("Couldn't confirm the payment with PayPal. Please "
                             "press pay again in a minute; you won't be charged twice.")

        verdict = payments.verify_capture(paid, amount=payments.to_money(order["amount"]),
                                          reference=str(order["id"]),
                                          order_id=order_key)
        if verdict["state"] == "pending":
            db.update_order(order["id"], status="pending",
                            capture_id=verdict["capture_id"], note=verdict["reason"])
            raise EntryError("PayPal is holding this payment for review. The "
                             "organiser will confirm your entry once it clears.")
        if verdict["state"] != "paid":
            db.update_order(order["id"], status="failed",
                            capture_id=verdict["capture_id"], note=verdict["reason"])
            log.error("order %s failed verification: %s", order["id"], verdict["reason"])
            raise EntryError("The payment didn't go through as expected. If you "
                             "were charged, contact the organiser (they can see it).")
        return _finish_paid(order, verdict)


def reconcile_order(order_id: int) -> dict:
    """
    Operator action: ask PayPal what happened to an open/pending order and
    finish it if the money arrived (e.g. the entrant closed the tab mid-capture).
    Returns the updated order row.
    """
    with _capture_lock:
        order = db.get_order(order_id)
        if order is None:
            raise StoreError("That order no longer exists")
        if order["status"] not in ("created", "capturing", "pending") \
                or not order["paypal_order_id"]:
            raise StoreError("Only open or pending PayPal orders can be checked")
        try:
            remote = payments.paypal_get_order(order["paypal_order_id"])
        except payments.PayPalError as err:
            raise StoreError(f"Couldn't check with PayPal: {err}")
        if remote.get("status") != "COMPLETED":
            db.update_order(order_id, note=f"PayPal status: {remote.get('status')}")
            return db.get_order(order_id)
        verdict = payments.verify_capture(remote, amount=payments.to_money(order["amount"]),
                                          reference=str(order["id"]),
                                          order_id=order["paypal_order_id"])
        if verdict["state"] == "paid":
            _finish_paid(order, verdict)
        elif verdict["state"] == "pending":
            db.update_order(order_id, status="pending", capture_id=verdict["capture_id"],
                            note=verdict["reason"])
        else:
            db.update_order(order_id, status="failed", capture_id=verdict["capture_id"],
                            note=verdict["reason"])
        return db.get_order(order_id)


def mark_refunded(order_id: int) -> None:
    """Operator records that they refunded a paid-but-not-entered order in
    PayPal (refunds are done in PayPal itself, deliberately not from here)."""
    order = db.get_order(order_id)
    if order is None:
        raise StoreError("That order no longer exists")
    if order["status"] not in ("needs_refund", "failed"):
        raise StoreError("Only orders flagged for a refund can be marked refunded")
    db.update_order(order_id, status="refunded",
                    note=(order["note"] or "") + " [refunded by operator]")


def list_orders() -> list[dict]:
    """Orders for the operator's Entries page, newest first."""
    out = []
    for order in db.all_orders():
        items = json.loads(order["entries_json"])
        order["people"] = ", ".join(i["name"] for i in items)
        # 'capturing' that long means we never heard back from PayPal, and
        # 'paid' means we crashed before entering them: either way the buyer
        # may have paid without being entered.
        order["needs_attention"] = order["status"] in ("needs_refund", "pending",
                                                       "failed", "paid") \
            or (order["status"] == "capturing" and
                datetime.fromisoformat(order["created_at"]) < datetime.now() - timedelta(minutes=5))
        out.append(order)
    return out
