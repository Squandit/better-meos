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

# Which punching system the real reader speaks. SportIdent is fully wired;
# 'siac' (SI-Air+) reads out over the same protocol (scaffolded in _run); 'emit'
# is scaffolded behind this flag -- the read loop maps its card structure through
# the same process_card path. The simulator is system-agnostic, so it works
# regardless. Set via BMEOS_PUNCH_SYSTEM=sportident|siac|emit.
PUNCH_SYSTEM = os.environ.get("BMEOS_PUNCH_SYSTEM", "sportident").lower()

# Running reader threads + their stop signals, keyed by station id (supports
# several download units that can be started/stopped independently).
_threads: dict[str, threading.Thread] = {}
_stops: dict[str, threading.Event] = {}
_start_lock = threading.Lock()

# A small ring buffer of recent downloads, for the operator dashboard.
_recent: deque = deque(maxlen=25)
_recent_lock = threading.Lock()

# The most recently seen card number (any read), so the registration page can
# autofill the SI number the moment a card touches the reader. ``seq`` rises on
# every read so the page can tell a fresh tap from a repeat.
_last_seen: dict = {"card_number": None, "name": "", "club": "", "seq": 0}
_seen_lock = threading.Lock()


def recent_reads() -> list[dict]:
    """Most-recent-first list of recent card reads (for the dashboard)."""
    with _recent_lock:
        return list(reversed(_recent))


def last_seen() -> dict:
    """The most recently read card number + any known name/club (registration)."""
    with _seen_lock:
        return dict(_last_seen)


def _note_seen(card: dict) -> None:
    """Record a just-read card number (+ runner-DB name/club) for registration."""
    num = card.get("card_number")
    if not num:
        return
    name = club = ""
    try:
        import runners  # local import: separate DB, avoid any import cycle
        r = runners.lookup(num)
        if r:
            name, club = r.get("name", ""), r.get("club", "")
    except Exception:  # pragma: no cover - lookup is best-effort
        pass
    with _seen_lock:
        _last_seen.update({"card_number": num, "name": name, "club": club,
                           "seq": _last_seen["seq"] + 1})


# ---------------------------------------------------------------------------
# Serial-port discovery (so the operator needn't know which COM port)
# ---------------------------------------------------------------------------

def list_serial_ports() -> list[dict]:
    """Available serial ports as ``[{device, description, hwid}]`` (empty if
    pyserial isn't present)."""
    try:
        from serial.tools import list_ports
    except Exception:  # pragma: no cover - pyserial missing
        return []
    return [{"device": p.device, "description": p.description or "",
             "hwid": p.hwid or ""} for p in list_ports.comports()]


def autodetect_port() -> str | None:
    """Best guess at the SI reader's COM port. SPORTident USB stations use a
    Silicon Labs CP210x bridge (USB VID 10C4); fall back to the only/first port."""
    ports = list_serial_ports()
    for p in ports:
        blob = (p["hwid"] + " " + p["description"]).upper()
        if "10C4" in blob or "CP210" in blob or "SPORTIDENT" in blob:
            return p["device"]
    return ports[0]["device"] if ports else None


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
    _note_seen(card)  # surface the card number for the registration page
    when = datetime.now().strftime("%H:%M:%S")
    try:
        comp = store.apply_card_read(card)
    except StoreError as err:
        if auto_create:
            try:
                comp = store.auto_create_from_card(card)
            except StoreError as err2:
                err = err2
            else:
                _remember(when, comp["name"], comp["card_number"], station_id, ok=True)
                events.publish("card_read", station_id=station_id)
                return {"ok": True, "competitor": comp, "auto_created": True}
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


def simulate(card: dict, *, station_id: str | None = None,
             auto_create: bool | None = None) -> dict:
    """Synchronously push a card through the pipeline (used by the API/tests)."""
    return process_card(card, station_id=station_id, auto_create=auto_create)


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


def _card_from_emit(data: dict, station_id: str | None) -> dict:
    """Translate an Emit ECB read into our card dict (scaffold).

    Emit cards expose an 'ecard'/'series' number and a list of (code, time)
    punches; the field names differ from SportIdent but the card shape we need
    is identical, so the rest of the pipeline is unchanged.
    """
    return {
        "card_number": data.get("ecard") or data.get("card_number"),
        "start": data.get("start"),
        "finish": data.get("finish"),
        "punches": list(data.get("punches", [])),
        "station_id": station_id,
    }


def _run_emit(port: str, station_id: str, stop_event: threading.Event) -> None:
    # Scaffold: a real Emit driver (e.g. an 'emit' serial library) goes here.
    # Lazy import so its absence doesn't affect SportIdent / the simulator.
    from emit import EmitReader  # type: ignore  # pragma: no cover - optional dep

    reader = EmitReader(port)
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

    # SIAC (SI-Air+) scaffold: contactless punches are delivered by the SAME
    # readout protocol and produce the identical card shape, so the standard SI
    # download loop below handles them unchanged. A fuller SIAC integration
    # (beacon/AIR+ mode toggling, battery/health checks) would extend this branch.
    mode = "SIAC (SI-Air+)" if PUNCH_SYSTEM == "siac" else "SportIdent"
    log.info("opening %s reader on %s (station %s)", mode, port, station_id)
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
    resolved = port or store.EVENT.get("reader_port")
    # Blank or "auto" -> find the SPORTident USB port ourselves.
    if not resolved or str(resolved).strip().lower() == "auto":
        resolved = autodetect_port()
        if resolved:
            log.info("auto-detected SI reader on %s", resolved)
        else:
            log.warning("no serial port found to auto-detect the SI reader")
            return False
    return _start_one(resolved, station_id)


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
