"""
SI card download station.

Two ways in, one way out. The *real* path drives a SportIdent base station on a
COM port (the hardware the original commented-out block in ``app.py`` targeted);
the *simulator* feeds the same processing path a hand-made card dict so the whole
read pipeline can be exercised with no hardware. Both end in
:func:`process_card`, which records the card against its competitor
(``store.apply_card_read``) and notifies live pages (``events.publish``).

The real reader runs on a supervised background thread and is **off by
default** -- it only starts when "Use a real SI reader" is on in Settings (env
``BMEOS_READER`` as fallback), so development and tests never block waiting for
a serial port. A reader that fails (cable pulled) is reopened automatically.

Card dict shape (mirrors ``MOCK_CARD_DATA``)::

    {"card_number": int, "start": datetime|None, "finish": datetime|None,
     "punches": [(code:int, time:datetime), ...], "station_id": str|None}
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from datetime import datetime

import config
import events
import network
import store
from store import StoreError

log = logging.getLogger("si_reader")

# Which punching system the real reader speaks. SportIdent is fully wired; Emit
# is scaffolded behind this flag (BMEOS_PUNCH_SYSTEM=emit) -- the read loop maps
# its card structure through the same process_card path. The simulator is
# system-agnostic, so it works regardless.
PUNCH_SYSTEM = os.environ.get("BMEOS_PUNCH_SYSTEM", "sportident").lower()

# Running reader threads + their stop signals, keyed by station id (supports
# several download units that can be started/stopped independently).
_threads: dict[str, threading.Thread] = {}
_stops: dict[str, threading.Event] = {}
_start_lock = threading.Lock()

# A small ring buffer of recent downloads, for the operator dashboard.
_recent: deque = deque(maxlen=25)
_recent_lock = threading.Lock()
_read_seq = 0


def recent_reads() -> list[dict]:
    """Most-recent-first list of recent card reads (for the dashboard)."""
    with _recent_lock:
        return list(reversed(_recent))


# ---------------------------------------------------------------------------
# Shared processing path
# ---------------------------------------------------------------------------

# Auto-create courses/classes/competitors from unknown cards (MeOS interactive
# setup). Off by default; enable with BMEOS_AUTO_CREATE. The API can also request
# it per-read.
AUTO_CREATE = bool(os.environ.get("BMEOS_AUTO_CREATE"))


def process_card(card: dict, *, station_id: str | None = None,
                 auto_create: bool | None = None) -> dict:
    """
    Record one downloaded card and broadcast the result.

    Returns ``{"ok": True, "competitor": ...}`` on success or
    ``{"ok": False, "error": msg, "card_number": n}`` when no competitor is
    registered for the card -- the caller (API or reader loop) decides what to do,
    but either way a live event is published so the operator sees the read.

    When ``auto_create`` is set (defaults to the ``BMEOS_AUTO_CREATE`` env flag),
    an unknown card builds its own course/class/competitor instead of failing.
    """
    if station_id is not None:
        card = {**card, "station_id": station_id}
    if auto_create is None:
        auto_create = AUTO_CREATE
    with store.acting_as(f"SI reader ({station_id or 'main'})"):
        return _process_card(card, station_id, auto_create)


def _process_card(card: dict, station_id: str | None, auto_create: bool) -> dict:
    when = datetime.now().strftime("%H:%M:%S")
    if network.is_secondary():
        # A secondary station holds no event: forward the read to the primary.
        try:
            outcome = network.push_card(card)
        except Exception as err:  # network/HTTP failure
            log.error("could not forward card %s to primary: %s",
                      card.get("card_number"), err)
            _remember(when, None, card.get("card_number"), station_id, ok=False)
            # push_failed tells the reader loop not to acknowledge the card,
            # so the runner can read out again instead of the run being lost.
            return {"ok": False, "error": f"primary unreachable: {err}",
                    "card_number": card.get("card_number"), "push_failed": True}
        _remember(when, (outcome.get("competitor") or {}).get("name"),
                  card.get("card_number"), station_id, ok=bool(outcome.get("ok")))
        return outcome
    try:
        comp = store.apply_card_read(card)
    except StoreError as err:
        if auto_create:
            try:
                comp = store.auto_create_from_card(card)
            except StoreError as err2:
                err = err2
            else:
                _remember(when, comp["name"], comp["card_number"], station_id, ok=True,
                          competitor_id=comp["id"])
                events.publish("card_read", station_id=station_id)
                return {"ok": True, "competitor": comp, "auto_created": True}
        log.warning("unmatched card %s: %s", card.get("card_number"), err)
        _remember(when, None, card.get("card_number"), station_id, ok=False)
        # Keep the read: the operator attaches it to the right runner from the
        # download page (or it applies itself once that card is entered), so
        # nobody has to come back and read out again.
        read_id = None
        if card.get("card_number") is not None and store.has_open_event():
            read_id = store.record_unmatched_read(card)
        # The SSE feed is public (live/projector screens), so publish only the
        # signal to refresh -- not the card number or runner name.
        events.publish("card_unknown", station_id=station_id)
        return {"ok": False, "error": str(err), "card_number": card.get("card_number"),
                "read_id": read_id}

    _remember(when, comp["name"], comp["card_number"], station_id, ok=True,
              competitor_id=comp["id"])
    events.publish("card_read", station_id=station_id)
    return {"ok": True, "competitor": comp}


def _remember(when, name, card_number, station_id, *, ok, competitor_id=None):
    global _read_seq
    with _recent_lock:
        _read_seq += 1
        _recent.append({"seq": _read_seq, "time": when, "name": name,
                        "card_number": card_number, "station": station_id or "main",
                        "ok": ok, "competitor_id": competitor_id})


def latest_read() -> dict | None:
    """The most recent read (for the readout screen)."""
    with _recent_lock:
        return dict(_recent[-1]) if _recent else None


def simulate(card: dict, *, station_id: str | None = None,
             auto_create: bool | None = None) -> dict:
    """Synchronously push a card through the pipeline (used by the API/tests)."""
    return process_card(card, station_id=station_id, auto_create=auto_create)


# ---------------------------------------------------------------------------
# Real hardware loop (guarded; off unless reader_enabled)
# ---------------------------------------------------------------------------

def _on_event_date(value: datetime | None) -> datetime | None:
    """
    Re-pin a hardware time to the open event's date.

    SI cards store time of day only; the library attaches *today's* date. Typed
    and imported times use the event date, so mixing the two would put a
    finish on a different day from its start whenever the event file's date
    isn't today (e.g. results fixed up the day after).
    """
    if value is None:
        return None
    return datetime.combine(store.EVENT_DATE, value.time())


def _card_from_si(data: dict, station_id: str | None) -> dict:
    """Translate the sportident library's read into our card dict."""
    return {
        "card_number": data.get("card_number"),
        "start": _on_event_date(data.get("start")),
        "finish": _on_event_date(data.get("finish")),
        "check": _on_event_date(data.get("check")),
        "punches": [(code, _on_event_date(t)) for code, t in data.get("punches", [])],
        "station_id": station_id,
    }


def _card_from_emit(data: dict, station_id: str | None) -> dict:
    """Translate an Emit ECB read into our card dict (scaffold).

    Emit cards expose an 'ecard'/'series' number and a list of (code, time)
    punches; the field names differ from SportIdent but the card shape we need
    is identical, so the rest of the pipeline is unchanged.
    """
    return {
        "card_number": data.get("ecard") or data.get("card_number"),
        "start": _on_event_date(data.get("start")),
        "finish": _on_event_date(data.get("finish")),
        "punches": [(code, _on_event_date(t)) for code, t in data.get("punches", [])],
        "station_id": station_id,
    }


def _run_emit(port: str, station_id: str, stop_event: threading.Event) -> None:
    # Scaffold: a real Emit driver (e.g. an 'emit' serial library) goes here.
    # Lazy import so its absence doesn't affect SportIdent / the simulator.
    from emit import EmitReader  # type: ignore  # pragma: no cover - optional dep

    reader = EmitReader(port)
    _set_status(station_id, state="running", error="")
    try:
        while not stop_event.is_set():
            data = reader.poll()
            if data:
                process_card(_card_from_emit(data, station_id), station_id=station_id)
            else:
                time.sleep(0.5)
    finally:
        try:
            reader.close()
        except Exception:  # pragma: no cover
            pass


def _run(port: str, station_id: str, stop_event: threading.Event) -> None:
    if PUNCH_SYSTEM == "emit":
        log.info("opening Emit reader on %s (station %s)", port, station_id)
        _run_emit(port, station_id, stop_event)
        return

    # Imported lazily so the module (and the simulator) load without pyserial /
    # a physical reader present.
    from sportident import SIReaderReadout

    log.info("opening SI reader on %s (station %s)", port, station_id)
    si = SIReaderReadout(port)
    _set_status(station_id, state="running", error="")
    try:
        while not stop_event.is_set():
            if si.poll_sicard():
                # poll_sicard also reports a card being *removed*; reading
                # then raises "No card in the device", which used to look like
                # a reader failure and force a reconnect after every runner.
                if si.sicard is None:
                    continue
                data = si.read_sicard()
                outcome = process_card(_card_from_si(data, station_id),
                                       station_id=station_id)
                if outcome.get("push_failed"):
                    time.sleep(1.0)  # no ack: the station signals a failed read
                    continue
                si.ack_sicard()
                _set_status(station_id, last_read=datetime.now().strftime("%H:%M:%S"))
            else:
                time.sleep(0.5)
    finally:
        try:
            si.disconnect()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


# Per-station health for the operator: state is connecting | running | retrying
# | stopped, with the last error and when the last card came in.
_status: dict[str, dict] = {}
_status_lock = threading.Lock()

# Seconds to wait before reopening a reader after it fails (a bumped USB cable,
# a station unplugged and replugged), growing to the cap while it keeps failing.
RETRY_DELAYS = (2, 5, 10, 30)


def _set_status(station_id: str, **fields) -> None:
    with _status_lock:
        _status.setdefault(station_id, {"station": station_id, "port": "",
                                        "state": "stopped", "error": "",
                                        "last_read": ""}).update(fields)


def reader_status() -> list[dict]:
    """Health of every configured reader station (for the download page)."""
    with _status_lock:
        return [dict(v) for v in sorted(_status.values(), key=lambda v: v["station"])]


def _supervise(port: str, station_id: str, stop_event: threading.Event) -> None:
    """Keep one station's reader running: reopen it after any failure until
    told to stop, so a disconnect mid-event recovers by itself."""
    failures = 0
    while not stop_event.is_set():
        _set_status(station_id, port=port, state="connecting")
        try:
            _run(port, station_id, stop_event)
            failures = 0
        except Exception as err:  # pragma: no cover - hardware/serial errors
            delay = RETRY_DELAYS[min(failures, len(RETRY_DELAYS) - 1)]
            failures += 1
            log.error("SI reader %s on %s failed: %s (retrying in %ss)",
                      station_id, port, err, delay)
            _set_status(station_id, state="retrying", error=str(err))
            stop_event.wait(delay)
    _set_status(station_id, state="stopped")


def _start_one(port: str, station_id: str) -> bool:
    """Start a supervised reader thread for one station (no enable checks)."""
    with _start_lock:
        if station_id in _threads and _threads[station_id].is_alive():
            return False
        stop_event = threading.Event()
        _stops[station_id] = stop_event
        thread = threading.Thread(target=_supervise, args=(port, station_id, stop_event),
                                  name=f"si-reader-{station_id}", daemon=True)
        _threads[station_id] = thread
        thread.start()
        return True


def reader_enabled() -> bool:
    """Real reader on? (Settings -> SI reader, env BMEOS_READER as fallback.)"""
    return bool(config.get("reader_enabled"))


def start(port: str | None = None, station_id: str = "main") -> bool:
    """
    Start a single real reader thread if the reader is enabled.

    Returns True if a thread was started, False if the reader is disabled or that
    station is already running. Port-open errors are logged and retried, not
    raised, so a missing reader never takes down the web server.
    """
    if not reader_enabled():
        log.info("SI reader disabled (turn it on in Settings); running on simulated reads")
        return False
    return _start_one(port or store.EVENT.get("reader_port") or "COM5", station_id)


def start_all() -> int:
    """
    Start a reader thread per configured download station and return the count.

    The "Reader ports" setting (env ``BMEOS_READER_PORTS``) lists stations as
    comma-separated ``port[:station]`` (e.g. ``COM5:finish,COM6:start``); when
    unset, a single ``main`` reader on the event's port is started. No-op
    (returns 0) when the reader is disabled.
    """
    if not reader_enabled():
        log.info("SI reader disabled; running on simulated reads")
        return 0
    spec = config.get_str("reader_ports")
    if not spec:
        return 1 if start() else 0
    count = 0
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        port, _, station = item.partition(":")
        if _start_one(port.strip(), station.strip() or "main"):
            count += 1
    return count


def stop() -> None:
    """Signal all running reader threads to stop."""
    for stop_event in _stops.values():
        stop_event.set()


def stop_station(station_id: str) -> None:
    """Signal one station's reader thread to stop."""
    if station_id in _stops:
        _stops[station_id].set()
