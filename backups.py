"""
Automatic backups of the open event.

A laptop dying mid-event, a corrupted file, a OneDrive sync conflict: all of
them lose the event unless there's a recent copy somewhere else. A background
thread snapshots the open event file every few minutes *if anything changed*
(db.revision), using SQLite's online backup so the copy is consistent while the
app keeps serving. Snapshots are ordinary ``.bmeos`` files, so recovering is
just opening one from the start page (or restoring it via Import / Export).

Settings (see config.py): folder (default outside OneDrive, in the user's
local app data), an optional second folder (a USB stick or network share),
interval and how many to keep per event.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime

import config
import db
import store

log = logging.getLogger("backups")

_state = {"revision": None, "at": 0.0, "last_file": None, "last_time": None,
          "last_error": None}
_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()

CHECK_EVERY = 15.0   # seconds between "anything changed?" checks


def default_dir() -> str:
    """Local app data (never synced by OneDrive), else ~/.better-meos."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return os.path.join(local, "better-meos", "backups")
    return os.path.join(os.path.expanduser("~"), ".better-meos", "backups")


def folders() -> list[str]:
    out = [config.get_str("backup_dir") or default_dir()]
    second = config.get_str("backup_dir_2")
    if second:
        out.append(second)
    return out


def in_synced_folder(path: str) -> bool:
    """True when a path sits inside a OneDrive (or Dropbox/iCloud) folder, where a
    live SQLite file can be locked or duplicated by the sync client mid-event."""
    text = os.path.abspath(path).lower()
    return any(tag in text for tag in ("onedrive", "dropbox", "icloud"))


def _prune(folder: str, slug: str) -> None:
    keep = max(1, int(config.get("backup_keep") or 30))
    pattern = re.compile(re.escape(slug) + r"-\d{8}-\d{6}\.bmeos$")
    mine = sorted(f for f in os.listdir(folder) if pattern.match(f))
    for old in mine[:-keep]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError as err:
            log.warning("could not prune %s: %s", old, err)


def backup_now() -> list[str]:
    """Snapshot the open event to every backup folder; returns the files written."""
    if not store.has_open_event():
        return []
    slug = store.EVENT.get("slug") or "event"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    written, errors = [], []
    revision = db.revision()
    for folder in folders():
        try:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{slug}-{stamp}.bmeos")
            db.backup_to(path)
            _prune(folder, slug)
            written.append(path)
        except Exception as err:  # a missing USB stick mustn't stop the rest
            errors.append(f"{folder}: {err}")
            log.error("backup to %s failed: %s", folder, err)
    with _lock:
        _state.update(revision=revision, at=time.monotonic(),
                      last_file=written[0] if written else _state["last_file"],
                      last_time=datetime.now().strftime("%H:%M:%S") if written
                      else _state["last_time"],
                      last_error="; ".join(errors) or None)
    return written


def maybe_backup() -> bool:
    """Back up if the event changed and the interval has passed."""
    if not store.has_open_event():
        return False
    interval = max(1, int(config.get("backup_interval_minutes") or 3)) * 60
    with _lock:
        unchanged = _state["revision"] == db.revision()
        too_soon = time.monotonic() - _state["at"] < interval
    if unchanged or too_soon:
        return False
    return bool(backup_now())


def status() -> dict:
    with _lock:
        return {"folders": folders(), "last_file": _state["last_file"],
                "last_time": _state["last_time"], "error": _state["last_error"]}


def _loop() -> None:
    while not _stop.wait(CHECK_EVERY):
        try:
            maybe_backup()
        except Exception as err:  # pragma: no cover - never kill the thread
            log.error("backup check failed: %s", err)


def start() -> None:
    """Start the background backup thread (launcher / dev server)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="backups", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
