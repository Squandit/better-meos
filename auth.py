"""
Optional user accounts and login gating.

Auth is **off by default** so the operator console and the test suite run open,
exactly as before. Set ``BMEOS_AUTH=1`` to require login for operator pages; the
public surfaces (entry form, public results, live screen) stay open either way.

When enabled, :func:`install` registers a ``before_request`` guard that redirects
un-authenticated browsers to ``/login`` and answers protected API calls with 401.
Passwords are hashed with Werkzeug. A first admin can be seeded from
``BMEOS_ADMIN_USER`` / ``BMEOS_ADMIN_PASS``.

Roles: ``operator`` (full control) and ``club`` (a club manager). Role-specific
gating beyond "logged in" is a thin layer on :func:`current_user` that views can
extend as the club-portal grows.
"""

from __future__ import annotations

import logging
import os

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db

log = logging.getLogger("auth")

# Paths reachable without logging in (public-facing surfaces + auth itself).
_PUBLIC_EXACT = {
    "/login", "/logout", "/unlock", "/api/entries", "/api/stream", "/live", "/enter",
    # Ported entry page (public PWA) and its backend.
    "/get-classes", "/get-result-classes", "/get-results", "/search-competitors",
    "/lookup-competitor", "/check-entered", "/submit-entry", "/log-entries",
    "/manifest.json", "/sw.js",
}
_PUBLIC_PREFIX = ("/static/", "/public/")


def is_enabled() -> bool:
    return bool(os.environ.get("BMEOS_AUTH"))


def create_user(username: str, password: str, role: str = "operator",
                club: str | None = None) -> int:
    return db.insert_user(username, generate_password_hash(password), role, club)


def verify(username: str, password: str) -> dict | None:
    user = db.get_user(username or "")
    if user and check_password_hash(user["password_hash"], password or ""):
        return user
    return None


def login_user(user: dict) -> None:
    session["user"] = {"username": user["username"], "role": user["role"],
                       "club": user.get("club")}


def logout_user() -> None:
    session.pop("user", None)


def current_user() -> dict | None:
    return session.get("user")


def ensure_admin() -> None:
    """Seed a first admin from env when auth is on and no users exist yet."""
    if not is_enabled() or db.count_users() > 0:
        return
    username = os.environ.get("BMEOS_ADMIN_USER")
    password = os.environ.get("BMEOS_ADMIN_PASS")
    if username and password:
        create_user(username, password, role="operator")
        log.info("seeded admin user %r", username)
    else:
        log.warning("BMEOS_AUTH is on but no users exist; set BMEOS_ADMIN_USER/"
                    "BMEOS_ADMIN_PASS to seed an operator")


def _is_public(path: str) -> bool:
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIX)


_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def install(app) -> None:
    """Wire the login guard into a Flask app (no-op effect until auth is on)."""
    @app.before_request
    def _require_login():
        if not is_enabled() or _is_public(request.path):
            return None
        user = current_user()
        if user is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Login required"}), 401
            return redirect(url_for("login", next=request.path))
        # Only operators may change data. 'club' accounts are read-only for now;
        # per-club event ownership is a future extension of this gate.
        if user.get("role") != "operator" and request.method in _MUTATING:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Operator role required"}), 403
            return ("Operator role required", 403)
        return None
