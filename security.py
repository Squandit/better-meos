"""
Two protections layered on top of the Flask app:

1. **Port-surface split.** The operator console and the public entry/results
   pages are served on two different ports (see ``launcher.py``). A request is
   classed by the port it arrived on: the **public** port (``config.public_port``)
   serves only a curated allowlist (results + the entry form); every other path
   on that port answers ``404``, so the editing pages aren't even reachable
   without the admin port number. Any other port (the dev server, the test
   client) is treated as the **admin** surface = full access, so development and
   the test suite are unaffected.

2. **Admin unlock gate.** When an admin password is configured (Settings
   dashboard -> ``config.json``), the admin surface requires unlocking once per
   session. The unlock is stored in a *permanent* session that lasts one day of
   inactivity, so a refresh keeps you in but a fresh/idle session re-prompts. If
   no password is set the gate is a no-op.

Registered before the other ``before_request`` guards so it runs first.
"""

from __future__ import annotations

from datetime import timedelta

from flask import abort, jsonify, redirect, request, session, url_for

import config

# What the PUBLIC (results + entry) port is allowed to serve. Everything else on
# that port -> 404. Kept deliberately small and explicit.
_PUBLIC_EXACT = {
    # Public result views.
    "/results", "/splits", "/clubs", "/live",
    # The online entry PWA + its backend (the ported PayPal page).
    "/enter", "/get-classes", "/get-result-classes", "/get-results",
    "/search-competitors", "/lookup-competitor", "/check-entered",
    "/submit-entry", "/log-entries", "/manifest.json", "/sw.js",
    # Public entry submission (creates the entry + payment) and the live feed.
    "/api/entries", "/api/stream",
    "/favicon.ico",
}
_PUBLIC_PREFIX = ("/static/", "/public/", "/slip/")

# Always reachable on the admin surface even while locked.
_UNLOCK_EXEMPT = {"/unlock", "/lock", "/favicon.ico"}


def is_public_path(path: str) -> bool:
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIX)


def _on_public_port() -> bool:
    return request.environ.get("SERVER_PORT") == str(config.public_port())


def install(app) -> None:
    """Wire the surface split + unlock gate into a Flask app."""
    # Permanent sessions expire after a day of inactivity (Flask refreshes the
    # cookie on each request), which is what gives us "survives refresh, re-asks
    # after a day / on a brand-new session".
    app.permanent_session_lifetime = timedelta(days=1)

    @app.before_request
    def _guard():
        if _on_public_port():
            # Public/results port: serve only the allowlist; hide everything else.
            if request.path == "/":
                return redirect(url_for("results"))
            if not is_public_path(request.path):
                abort(404)
            return None  # public pages never hit the admin unlock gate

        # Admin surface: require unlock when a password is configured.
        if not config.admin_password_set():
            return None
        if request.path in _UNLOCK_EXEMPT or request.path.startswith("/static/"):
            return None
        if session.get("admin_ok"):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Admin unlock required"}), 401
        return redirect(url_for("unlock", next=request.path))
