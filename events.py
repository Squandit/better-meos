"""
Server-Sent Events hub for live updates.

The operator console is server-rendered, so "live" means: when anything changes
(a card is read, a competitor edited, a class added) the server pushes a small
notification and open pages re-fetch what they show. This module is the tiny
pub/sub behind that -- a set of per-client queues plus :func:`publish`.

It is deliberately framework-light: ``app.py`` exposes ``GET /api/stream`` which
calls :func:`subscribe` and streams whatever lands on that client's queue as an
SSE ``data:`` line. Any thread (a request handler, the SI-reader thread) may call
:func:`publish` safely.
"""

from __future__ import annotations

import json
import queue
import threading

# Open subscriber queues. Guarded by _lock; each client gets its own bounded
# queue so one slow/abandoned browser tab can't grow memory without limit.
_subscribers: set[queue.Queue] = set()
_per_port: dict[str, int] = {}
_lock = threading.Lock()
_version = 0

_MAX_BACKLOG = 100

# Every open stream holds a server worker thread for as long as the tab is open,
# so cap them per port well below the launcher's thread pool (see launcher.py);
# anyone over the cap polls /api/version instead.
MAX_STREAMS = 12
# Seconds between keep-alive comments. A write to a closed tab fails, which is
# how the server notices the tab went away and frees the thread.
KEEPALIVE = 10


def subscribe(port: str = "") -> queue.Queue | None:
    """Open a subscriber queue, or None when this port is at its stream cap."""
    with _lock:
        if _per_port.get(port, 0) >= MAX_STREAMS:
            return None
        _per_port[port] = _per_port.get(port, 0) + 1
        q: queue.Queue = queue.Queue(maxsize=_MAX_BACKLOG)
        _subscribers.add(q)
    return q


def unsubscribe(q: queue.Queue, port: str = "") -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.discard(q)
            _per_port[port] = max(0, _per_port.get(port, 0) - 1)


def next_message(q: queue.Queue, timeout: float | None = None) -> str | None:
    """The next payload for a subscriber, or None after ``timeout`` seconds
    (default :data:`KEEPALIVE`) so the caller can send a keep-alive."""
    try:
        return q.get(timeout=KEEPALIVE if timeout is None else timeout)
    except queue.Empty:
        return None


def version() -> int:
    """Bumps on every publish; lets pages without a stream poll for changes."""
    return _version


def publish(kind: str, **data) -> None:
    """
    Broadcast an event to every open client.

    ``kind`` is a short topic string (e.g. "card_read", "competitor",
    "course") so the browser can decide whether it needs to refresh. Extra
    keyword data is JSON-serialised alongside it. A full client queue is drained
    of its oldest item rather than blocking the caller.
    """
    global _version
    payload = json.dumps({"kind": kind, **data})
    with _lock:
        _version += 1
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            try:
                q.get_nowait()       # drop the oldest, make room for the newest
                q.put_nowait(payload)
            except queue.Empty:
                pass
