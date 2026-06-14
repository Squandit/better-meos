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
_lock = threading.Lock()

_MAX_BACKLOG = 100


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=_MAX_BACKLOG)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        _subscribers.discard(q)


def publish(kind: str, **data) -> None:
    """
    Broadcast an event to every open client.

    ``kind`` is a short topic string (e.g. "card_read", "competitor",
    "course") so the browser can decide whether it needs to refresh. Extra
    keyword data is JSON-serialised alongside it. A full client queue is drained
    of its oldest item rather than blocking the caller.
    """
    payload = json.dumps({"kind": kind, **data})
    with _lock:
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
