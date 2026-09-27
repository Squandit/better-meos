"""
Publish results online while the event runs.

MeOS-style "results on the web" without depending on this laptop being
reachable: whenever the results change (db.revision), write a self-contained
``results.html`` plus the IOF ``results.xml`` into a folder, and optionally
upload both to the club website over FTP / FTPS. The folder alone also works
with anything that syncs a folder to the web.

The HTML/XML come from a renderer that app.py registers (it owns Flask and
the templates); this module only decides *when* to publish and *where to*.
"""

from __future__ import annotations

import ftplib
import io
import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable

import config
import db
import store

log = logging.getLogger("publish")

_renderer: Callable[[], dict[str, bytes]] | None = None
_state = {"revision": None, "at": 0.0, "last_time": None, "last_error": None, "files": []}
_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None

CHECK_EVERY = 10.0


def set_renderer(fn: Callable[[], dict[str, bytes]]) -> None:
    """Register the function that renders ``{filename: bytes}`` to publish."""
    global _renderer
    _renderer = fn


def enabled() -> bool:
    return bool(config.get_str("publish_dir") or config.get_str("ftp_host"))


def _write_folder(folder: str, files: dict[str, bytes]) -> None:
    os.makedirs(folder, exist_ok=True)
    for name, data in files.items():
        tmp = os.path.join(folder, name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(folder, name))   # readers never see half a file


def _upload_ftp(files: dict[str, bytes]) -> None:
    host = config.get_str("ftp_host")
    ftp = ftplib.FTP_TLS() if config.get("ftp_tls") else ftplib.FTP()
    ftp.connect(host, int(config.get("ftp_port") or 21), timeout=20)
    try:
        ftp.login(config.get_str("ftp_user") or "anonymous", config.get_str("ftp_pass"))
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()   # encrypt the data channel too
        if config.get_str("ftp_dir"):
            ftp.cwd(config.get_str("ftp_dir"))
        for name, data in files.items():
            # Upload under a temp name then rename, so visitors never load a
            # half-uploaded page.
            ftp.storbinary(f"STOR {name}.tmp", io.BytesIO(data))
            try:
                ftp.delete(name)
            except ftplib.error_perm:
                pass   # first upload: nothing to replace
            ftp.rename(f"{name}.tmp", name)
    finally:
        try:
            ftp.quit()
        except Exception:  # pragma: no cover - best effort
            ftp.close()


def publish_now() -> list[str]:
    """Render and publish immediately; returns where it went."""
    if _renderer is None or not store.has_open_event():
        return []
    revision = db.revision()
    files = _renderer()
    targets, errors = [], []
    folder = config.get_str("publish_dir")
    if folder:
        try:
            _write_folder(folder, files)
            targets.append(folder)
        except OSError as err:
            errors.append(f"folder: {err}")
    if config.get_str("ftp_host"):
        try:
            _upload_ftp(files)
            targets.append(f"ftp://{config.get_str('ftp_host')}/{config.get_str('ftp_dir')}")
        except ftplib.all_errors as err:
            errors.append(f"FTP: {err}")
    for e in errors:
        log.error("publishing results failed: %s", e)
    with _lock:
        _state.update(revision=revision, at=time.monotonic(),
                      last_time=datetime.now().strftime("%H:%M:%S") if targets
                      else _state["last_time"],
                      last_error="; ".join(errors) or None, files=sorted(files))
    return targets


def maybe_publish() -> bool:
    if not enabled() or not store.has_open_event():
        return False
    interval = max(10, int(config.get("publish_interval_seconds") or 60))
    with _lock:
        if _state["revision"] == db.revision() or time.monotonic() - _state["at"] < interval:
            return False
    return bool(publish_now())


def status() -> dict:
    with _lock:
        return {"enabled": enabled(), "last_time": _state["last_time"],
                "error": _state["last_error"], "folder": config.get_str("publish_dir"),
                "ftp": config.get_str("ftp_host")}


def _loop() -> None:
    while not _stop.wait(CHECK_EVERY):
        try:
            maybe_publish()
        except Exception as err:  # pragma: no cover - never kill the thread
            log.error("publish check failed: %s", err)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="publish", daemon=True)
    _thread.start()
