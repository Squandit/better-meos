"""
SI card download station.

Two ways in, one way out. The *real* path drives a SportIdent base station on a
COM port (the hardware the original commented-out block in ``app.py`` targeted);
the *simulator* feeds the same processing path a hand-made card dict so the whole
read pipeline can be exercised with no hardware. Both end in
:func:`process_card`, which records the card against its competitor
(``store.apply_card_read``) and notifies live pages (``events.publish``).

The real reader runs on a background daemon thread and is **off by default** --
it only starts when ``store.EVENT["reader_enabled"]`` is set (env ``BMEOS_READER``),
so development and tests never block waiting for a serial port.

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

import events
import store
from store import StoreError

log = logging.getLogger("si_reader")

# Running reader threads + their stop signals, keyed by station id (supports
# several download units that can be started/stopped independently).
_threads: dict[str, threading.Thread] = {}
_stops: dict[str, threading.Event] = {}
_start_lock = threading.Lock()

# A small ring buffer of recent downloads, for the operator dashboard.
_recent: deque = deque(maxlen=25)
_recent_lock = threading.Lock()


def recent_reads() -> list[dict]:
    """Most-recent-first list of recent card reads (for the dashboard)."""
    with _recent_lock:
        return list(reversed(_recent))


# ---------------------------------------------------------------------------
# Shared processing path
# ---------------------------------------------------------------------------

def process_card(card: dict, *, station_id: str | None = None) -> dict:
    """
    Record one downloaded card and broadcast the result.

    Returns ``{"ok": True, "competitor": ...}`` on success or
    ``{"ok": False, "error": msg, "card_number": n}`` when no competitor is
    registered for the card -- the caller (API or reader loop) decides what to do,
    but either way a live event is published so the operator sees the read.
    """
    if station_id is not None:
        card = {**card, "station_id": station_id}
    when = datetime.now().strftime("%H:%M:%S")
    try:
        comp = store.apply_card_read(card)
    except StoreError as err:
        log.warning("unmatched card %s: %s", card.get("card_number"), err)
        _remember(when, None, card.get("card_number"), station_id, ok=False)
        # The SSE feed is public (live/projector screens), so publish only the
        # signal to refresh -- not the card number or runner name.
        events.publish("card_unknown", station_id=station_id)
        return {"ok": False, "error": str(err), "card_number": card.get("card_number")}

    _remember(when, comp["name"], comp["card_number"], station_id, ok=True)
    events.publish("card_read", station_id=station_id)
    return {"ok": True, "competitor": comp}


def _remember(when, name, card_number, station_id, *, ok):
    with _recent_lock:
        _recent.append({"time": when, "name": name, "card_number": card_number,
                        "station": station_id or "main", "ok": ok})


def simulate(card: dict, *, station_id: str | None = None) -> dict:
    """Synchronously push a card through the pipeline (used by the API/tests)."""
    return process_card(card, station_id=station_id)


# ---------------------------------------------------------------------------
# Real hardware loop (guarded; off unless reader_enabled)
# ---------------------------------------------------------------------------

def _card_from_si(data: dict, station_id: str | None) -> dict:
    """Translate the sportident library's read into our card dict."""
    return {
        "card_number": data.get("card_number"),
        "start": data.get("start"),
        "finish": data.get("finish"),
        "punches": list(data.get("punches", [])),
        "station_id": station_id,
    }


def _run(port: str, station_id: str, stop_event: threading.Event) -> None:
    # Imported lazily so the module (and the simulator) load without pyserial /
    # a physical reader present.
    from sportident import SIReaderReadout

    log.info("opening SI reader on %s (station %s)", port, station_id)
    si = SIReaderReadout(port)
    try:
        while not stop_event.is_set():
            if si.poll_sicard():
                data = si.read_sicard()
                process_card(_card_from_si(data, station_id), station_id=station_id)
                si.ack_sicard()
            else:
                time.sleep(0.5)
    finally:
        try:
            si.disconnect()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


def _start_one(port: str, station_id: str) -> bool:
    """Start a reader thread for one station (no enable/guard checks)."""
    with _start_lock:
        if station_id in _threads and _threads[station_id].is_alive():
            return False
        stop_event = threading.Event()
        _stops[station_id] = stop_event

        def runner():
            try:
                _run(port, station_id, stop_event)
            except Exception as err:  # pragma: no cover - hardware/serial errors
                log.error("SI reader %s stopped: %s", station_id, err)

        thread = threading.Thread(target=runner, name=f"si-reader-{station_id}",
                                  daemon=True)
        _threads[station_id] = thread
        thread.start()
        return True


def start(port: str | None = None, station_id: str = "main") -> bool:
    """
    Start a single real reader thread if the event has the reader enabled.

    Returns True if a thread was started, False if the reader is disabled or that
    station is already running. Port-open errors are logged, not raised, so a
    missing reader never takes down the web server.
    """
    if not store.EVENT.get("reader_enabled"):
        log.info("SI reader disabled (set BMEOS_READER to enable); running on simulated reads")
        return False
    return _start_one(port or store.EVENT.get("reader_port"), station_id)


def start_all() -> int:
    """
    Start a reader thread per configured download station and return the count.

    ``BMEOS_READER_PORTS`` lists stations as comma-separated ``port[:station]``
    (e.g. ``COM5:finish,COM6:start``); when unset, a single ``main`` reader on
    the event's port is started. No-op (returns 0) when the reader is disabled.
    """
    if not store.EVENT.get("reader_enabled"):
        log.info("SI reader disabled; running on simulated reads")
        return 0
    spec = os.environ.get("BMEOS_READER_PORTS")
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
