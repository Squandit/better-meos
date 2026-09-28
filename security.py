"""
Protections layered on top of the Flask app:

1. **Cross-site request check.** Every state-changing request (POST/PUT/PATCH/
   DELETE) must come from this app's own pages. Browsers label cross-site
   requests with ``Sec-Fetch-Site`` / ``Origin``; anything from another site
   (or from the public port to the admin port) is refused, so a web page the
   operator happens to visit can't drive the console behind their back.

2. **Port-surface split.** The operator console and the public entry/results
   pages are served on two different ports (see ``launcher.py``). A request is
   classed by the port it arrived on: the **public** port (``config.public_port``)
   serves only a curated allowlist (results + the entry form); every other path
   on that port answers ``404``, so the editing pages aren't even reachable
   without the admin port number. Any other port (the dev server, the test
   client) is treated as the **admin** surface = full access, so development and
   the test suite are unaffected.

3. **Console stays on this PC unless locked.** The admin surface answers other
   computers only when an admin password is set. (The launcher also binds it to
   loopback unless "Allow the console from other computers" is on.)

4. **Admin lock.** When an admin password is configured (Settings dashboard ->
   ``config.json``), the admin surface requires the password once per session.
   The session is *permanent* and lasts one day of inactivity, so a refresh
   keeps you in but a fresh/idle session re-prompts. If no password is set the
   gate is a no-op.

5. **Station token.** Secondary download stations and radio controls are
   machines, not browsers, so they authenticate with a shared token header
   instead of a session.

Registered before the other ``before_request`` guards so it runs first.
"""

from __future__ import annotations

import hmac
from datetime import timedelta
from urllib.parse import urlparse

from flask import abort, jsonify, redirect, request, session, url_for

import config

# What the PUBLIC (results + entry) port is allowed to serve. Everything else on
# that port -> 404. Kept deliberately small and explicit.
_PUBLIC_EXACT = {
    # Public result views.
    "/results", "/splits", "/clubs", "/live",
    # The online entry PWA + its read-only helpers.
    "/enter", "/get-classes", "/get-result-classes", "/get-results",
    "/search-competitors", "/lookup-competitor", "/check-entered",
    "/manifest.json", "/sw.js",
    # Online entry: create a (PayPal or free) order, then capture it. Every
    # entry is priced and verified server-side (see online_entry.py).
    "/api/online-entry/order", "/api/online-entry/capture",
    # The live feed + its polling fallback.
    "/api/stream", "/api/version",
    "/favicon.ico",
}
_PUBLIC_PREFIX = ("/static/", "/public/")

# Always reachable on the admin surface even while locked.
_UNLOCK_EXEMPT = {"/unlock", "/lock", "/favicon.ico"}

# Machine endpoints that accept the station token instead of a session.
STATION_PATHS = {"/api/station/push", "/api/station/ping", "/api/radio/punch"}
STATION_HEADER = "X-Station-Token"

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def is_public_path(path: str) -> bool:
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIX)


def on_public_port() -> bool:
    """This request came in on the public (results + entry) port."""
    return _on_public_port()


def _on_public_port() -> bool:
    return request.environ.get("SERVER_PORT") == str(config.public_port())


def _is_loopback() -> bool:
    addr = request.remote_addr or ""
    return addr == "::1" or addr.startswith("127.")


def is_cross_site() -> bool:
    """True when a browser tells us the request came from another site/port.

    ``Sec-Fetch-Site`` is sent by every current browser; ``Origin`` is the
    fallback. Requests with neither (scripts, the station push, the test client)
    aren't browser-driven and are judged by the other guards instead."""
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:
        return fetch_site not in ("same-origin", "none")
    origin = request.headers.get("Origin")
    if origin:
        # Compare host:port only: behind ngrok the browser sees https while
        # Flask sees plain http, but the host is the same.
        return urlparse(origin).netloc != request.host
    return False


def has_station_token() -> bool:
    """True when the request carries the configured station token."""
    expected = config.get_str("station_token")
    supplied = request.headers.get(STATION_HEADER, "")
    return bool(expected) and hmac.compare_digest(
        expected.encode("utf-8"), supplied.encode("utf-8"))


def safe_next(target: str | None, default: str) -> str:
    """Only follow a ``next`` redirect to a path on this site (no open redirect
    to ``//evil.example`` or ``https://...``)."""
    if not target or not target.startswith("/") or target.startswith(("//", "/\\")):
        return default
    if urlparse(target).netloc or "\\" in target:
        return default
    return target


def install(app) -> None:
    """Wire the checks above into a Flask app."""
    # Permanent sessions expire after a day of inactivity (Flask refreshes the
    # cookie on each request), which is what gives us "survives refresh, re-asks
    # after a day / on a brand-new session".
    app.permanent_session_lifetime = timedelta(days=1)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

    @app.before_request
    def _guard():
        if request.method in _MUTATING and is_cross_site():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Cross-site request refused"}), 403
            abort(403)

        if _on_public_port():
            # Public/results port: serve only the allowlist; hide everything else.
            if request.path == "/":
                return redirect(url_for("results"))
            if not is_public_path(request.path):
                abort(404)
            return None  # public pages never hit the admin gate

        # Machines (secondary stations, radio controls) prove themselves with
        # the shared token; they can't hold a browser session.
        if request.path in STATION_PATHS and has_station_token():
            return None

        password_set = config.admin_password_set()
        if not password_set:
            # Without a password the console is only for whoever sits at this PC.
            if not _is_loopback():
                msg = ("Set an admin password in Settings before using the "
                       "console from another computer.")
                if request.path.startswith("/api/"):
                    return jsonify({"error": msg}), 403
                return msg, 403
            return None

        if request.path in _UNLOCK_EXEMPT or request.path.startswith("/static/"):
            return None
        if session.get("admin_ok"):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Admin password required"}), 401
        return redirect(url_for("unlock", next=request.path))
