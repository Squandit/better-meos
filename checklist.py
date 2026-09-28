"""
Two checklists on the Setup page (and a home screen widget).

* **Race day**: what's missing before the first start: courses, entries,
  card numbers, start times, the reader, backups, payments set to real money.
* **Close-out**: what's left after the last finisher: who hasn't read out
  (the search-and-rescue question), kept cards, hire cards, money owed,
  results published, a fresh backup.

Each item is ``{state, text, href, action}``: state ``ok`` / ``warn`` /
``bad``, a link to where it's fixed, and for a few an action the page can run.
"""

from __future__ import annotations

import backups
import config
import draw
import payments
import publish
import si_reader
import store


def _item(state: str, text: str, href: str = "", action: str = "") -> dict:
    return {"state": state, "text": text, "href": href, "action": action}


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def race_day() -> list[dict]:
    items = []
    courses = list(store._courses.values())
    if not courses:
        items.append(_item("bad", "No courses yet: import them from OCAD / Purple Pen", "/tools"))
    else:
        empty = [c["name"] for c in courses if not c["controls"]]
        items.append(_item("bad", f"Courses with no controls: {', '.join(empty)}", "/courses")
                     if empty else _item("ok", f"{_plural(len(courses), 'course')} with controls", "/courses"))
    classes = list(store._classes.values())
    if not classes:
        items.append(_item("bad", "No classes yet", "/classes"))
    runners = [c for c in store._competitors.values() if not c.get("vacant")]
    if not runners:
        items.append(_item("warn", "No entries yet", "/competitors"))
    else:
        items.append(_item("ok", f"{_plural(len(runners), 'runner')} entered in "
                           f"{_plural(len(classes), 'class', 'classes')}", "/competitors"))
        no_card = [c for c in runners if not c.get("card_number") and not c.get("hired")]
        if no_card:
            items.append(_item("warn", f"{_plural(len(no_card), 'runner')} with no SI card "
                               "(and no hire card)", "/competitors"))
    undrawn = sorted({store._classes[c["class_id"]]["name"] for c in runners
                      if c["start"] is None
                      and store._classes[c["class_id"]].get("kind") not in ("relay", "patrol")
                      and store._courses.get(
                          store._classes[c["class_id"]]["course_id"], {}).get("start_mode", "clock")
                      == "clock"})
    if undrawn:
        items.append(_item("warn", f"No start times in {', '.join(undrawn[:6])}"
                           + ("…" if len(undrawn) > 6 else ""), "/draw"))
    elif runners:
        items.append(_item("ok", "Everyone has a start time (or a punch / mass start)", "/draw"))
    for clash in draw.course_clashes():
        items.append(_item("warn", f"Course {clash['course']}: {' and '.join(clash['classes'])} "
                           f"have {_plural(clash['count'], 'start')} at the same minute", "/draw"))
    if si_reader.reader_enabled():
        broken = [r for r in si_reader.reader_status() if r["state"] != "running"]
        items.append(_item("bad", "SI reader isn't running: " + ", ".join(
            f"{r['station']} {r['port']} ({r['state']})" for r in broken), "/download")
            if broken or not si_reader.reader_status()
            else _item("ok", "SI reader connected", "/download"))
    else:
        items.append(_item("warn", "SI reader is off (simulated reads only)", "/config?q=reader"))
    if backups.in_synced_folder(store.events_dir()):
        items.append(_item("warn", "The event file is in a OneDrive / synced folder: sync can "
                           "corrupt a live database", "/setup"))
    status = backups.status()
    if status["error"]:
        items.append(_item("bad", f"Backups failing: {status['error']}", "/config?q=backup"))
    else:
        items.append(_item("ok", "Automatic backups on", "/config?q=backup"))
    if payments.paypal_enabled() and config.get("paypal_sandbox"):
        items.append(_item("warn", "PayPal is in sandbox mode: entries pay with test money",
                           "/config?q=sandbox"))
    if config.get("admin_lan") and not config.admin_password_set():
        items.append(_item("bad", "The console is open to other computers with no admin password",
                           "/config?q=admin"))
    return items


def _still_out() -> list[dict]:
    """Runners with no card read whose start has passed."""
    now = store.event_now()
    evaluated, _ = store.evaluate()
    return [r for e in evaluated for r in e["results"]
            if r["status"] == "pending" and r.get("start") is not None and r["start"] <= now]


def close_out() -> list[dict]:
    items = []
    out = _still_out()
    out_ids = {r["id"] for r in out}
    not_started = [r for e in store.evaluate()[0] for r in e["results"]
                   if r["status"] == "pending" and r["id"] not in out_ids]
    if out:
        # Everyone, not the first few: this is the list of people to find.
        out.sort(key=lambda r: (r["start"], r["name"].lower()))
        names = ", ".join(f"{r['name']} ({r['class']}, out {r['start']:%H:%M})" for r in out)
        items.append(_item("bad", f"{_plural(len(out), 'runner')} started but never read out: "
                           f"{names}. Check they're safe before anyone packs up.",
                           "/speaker", "out"))
    else:
        items.append(_item("ok", "Everyone who started has read out", "/speaker"))
    if not_started:
        items.append(_item("warn", f"{_plural(len(not_started), 'entry', 'entries')} never started "
                           "and have no result yet", "/competitors", "dns"))
    kept = store.unmatched_reads()
    items.append(_item("bad", f"{_plural(len(kept), 'card read')} not matched to anyone", "/download")
                 if kept else _item("ok", "Every card read is matched", "/download"))
    hire = store.economy_summary()["outstanding"]
    items.append(_item("warn", f"{_plural(len(hire), 'hire card')} not returned", "/economy")
                 if hire else _item("ok", "Every hire card is back", "/economy"))
    owing = store.payments_summary()
    if owing["owing"] > 0:
        count = sum(1 for r in owing["runners"] if r["owing"])
        items.append(_item("warn", f"{_plural(count, 'runner')} still owe "
                           f"{owing['owing']:.2f} {config.get_str('currency')}", "/economy"))
    pub = publish.status()
    if pub["enabled"]:
        items.append(_item("bad", f"Publishing results failed: {pub['error']}", "/setup")
                     if pub["error"] else _item("ok", f"Results published (last {pub['last_time'] or 'not yet'})", "/setup"))
    else:
        items.append(_item("warn", "Results not published online: export the IOF XML for "
                           "Eventor from Import / Export", "/tools"))
    last = backups.status()["last_time"]
    items.append(_item("ok" if last else "warn",
                       f"Last backup {last}" if last else "No backup yet this session",
                       "/setup", "" if last else "backup"))
    return items


def summary() -> dict:
    """Counts for the home screen widget."""
    before, after = race_day(), close_out()
    return {"race_day": before, "close_out": after,
            "open": sum(1 for i in before + after if i["state"] != "ok")}


def mark_remaining(status: str) -> int:
    """Close-out (MeOS's "set remaining runners"): every runner with no result
    yet gets DNS or DNF. The page only offers it once someone has confirmed
    that everyone still listed as out is accounted for."""
    if status not in ("dns", "dnf"):
        raise store.StoreError("Pick DNS or DNF")
    evaluated, _ = store.evaluate()
    targets = [r["id"] for e in evaluated for r in e["results"] if r["status"] == "pending"]
    with store.batch():
        for cid in targets:
            store.update_competitor(cid, {"manual_status": status})
        store._audit("close-out", f"{len(targets)} runners", f"set to {status.upper()}")
    return len(targets)
