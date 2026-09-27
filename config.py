"""
Settings storage and lookup (the catalogue itself is settings_schema.py).

Two places hold values:

* ``config.json`` on this computer (path ``BMEOS_CONFIG``, else the working
  directory): computer-scoped settings, plus the *defaults* for event-scoped
  ones. Secrets live here only; the admin password only as a Werkzeug hash.
* the open event file's ``settings`` table: event-scoped values that travel
  with the event.

:func:`get` resolves **event file -> config.json -> environment -> default**
(the event file only for event-scoped settings, and only while an event is
open), so an old deployment configured through ``BMEOS_*`` env vars keeps
working, and an event that never touched a setting follows the computer.

Saving any setting bumps ``db.revision`` so cached results and pages refresh.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from datetime import date, datetime

from werkzeug.security import check_password_hash, generate_password_hash

import db
import settings_schema

log = logging.getLogger("config")

# The catalogue lives in settings_schema.py; kept importable here as before.
SCHEMA = settings_schema.SCHEMA
_BY_KEY = settings_schema.BY_KEY

_lock = threading.RLock()
_cache: dict | None = None  # lazily loaded contents of config.json


def _path() -> str:
    return os.environ.get("BMEOS_CONFIG") or os.path.abspath("config.json")


def _load() -> dict:
    """Return the parsed config.json (cached). A missing/broken file = {}."""
    global _cache
    with _lock:
        if _cache is None:
            try:
                with open(_path(), encoding="utf-8") as f:
                    loaded = json.load(f)
                _cache = loaded if isinstance(loaded, dict) else {}
            except FileNotFoundError:
                _cache = {}
            except (OSError, ValueError) as err:
                log.warning("could not read %s: %s", _path(), err)
                _cache = {}
        return _cache


def reload() -> None:
    """Drop the in-memory cache so the next read re-parses the file (tests)."""
    global _cache
    with _lock:
        _cache = None


def _coerce(value, type):
    """Loose coercion for reading stored/env values (never raises)."""
    if type == "bool":
        if isinstance(value, str):
            return value.strip().lower() not in ("", "0", "false", "no", "off")
        return bool(value)
    if type == "int":
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
    if type == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if type == "json":
        return value
    return str(value)


class SettingError(ValueError):
    """A value the settings page can't accept; the message is shown as-is."""


def validate(spec, raw):
    """Strictly validate one incoming value for ``spec`` (raises SettingError)."""
    if spec.type == "bool":
        return _coerce(raw, "bool")
    if spec.type == "json":
        return raw
    text = raw.strip() if isinstance(raw, str) else raw
    if spec.type in ("int", "float"):
        try:
            number = int(float(text)) if spec.type == "int" else float(text)
        except (TypeError, ValueError):
            raise SettingError(f"{spec.label}: enter a number")
        if spec.minimum is not None and number < spec.minimum:
            raise SettingError(f"{spec.label}: at least {spec.minimum:g}")
        if spec.maximum is not None and number > spec.maximum:
            raise SettingError(f"{spec.label}: at most {spec.maximum:g}")
        return number
    text = "" if text is None else str(text)
    if spec.type == "choice":
        if text not in {value for value, _ in spec.choices}:
            raise SettingError(f"{spec.label}: pick one of the options")
        return text
    if spec.type == "time":
        try:
            datetime.strptime(text, "%H:%M:%S" if text.count(":") == 2 else "%H:%M")
        except ValueError:
            raise SettingError(f"{spec.label}: a time like 10:00:00")
        return text
    if spec.type == "datetime":
        try:
            datetime.fromisoformat(text)
        except ValueError:
            raise SettingError(f"{spec.label}: a date and time like 2026-10-01T18:00")
        return text
    if spec.type == "date":
        try:
            date.fromisoformat(text)
        except ValueError:
            raise SettingError(f"{spec.label}: a date like 2026-10-01")
        return text
    if len(text) > 5000:
        raise SettingError(f"{spec.label}: too long")
    return text


# Cache of the open event's settings, keyed by db revision (any write bumps
# it, including saving a setting, so this can never go stale).
_event_cache: dict = {"revision": None, "values": {}}


def _event_values() -> dict:
    if not db.is_open():
        return {}
    revision = db.revision()
    with _lock:
        if _event_cache["revision"] != revision:
            try:
                values = db.event_settings()
            except Exception as err:  # pragma: no cover - never break a page on this
                log.warning("could not read event settings: %s", err)
                values = {}
            _event_cache.update(revision=revision, values=values)
        return _event_cache["values"]


def source(key: str, *, include_event: bool = True) -> str:
    """Where the effective value comes from: event, computer, env or default."""
    spec = _BY_KEY.get(key)
    if spec is None:
        return "default"
    if include_event and spec.scope == "event" and key in _event_values():
        return "event"
    if _load().get(key) not in (None, ""):
        return "computer"
    if spec.env and os.environ.get(spec.env) not in (None, ""):
        return "env"
    return "default"


def get(key: str, *, include_event: bool = True):
    """Resolved value: event file -> config.json -> environment -> default."""
    spec = _BY_KEY.get(key)
    type = spec.type if spec else "str"
    if include_event and spec is not None and spec.scope == "event":
        values = _event_values()
        if key in values:
            coerced = _coerce(values[key], type)
            if coerced is not None:
                return coerced
    stored = _load().get(key)
    if stored not in (None, ""):
        coerced = _coerce(stored, type)
        if coerced is not None:
            return coerced
    if spec and spec.env:
        env = os.environ.get(spec.env)
        if env not in (None, ""):
            coerced = _coerce(env, type)
            if coerced is not None:
                return coerced
    return spec.default if spec else None


def get_str(key: str) -> str:
    value = get(key)
    return "" if value is None else str(value)


def admin_port() -> int:
    return int(get("admin_port") or 8799)


def public_port() -> int:
    return int(get("public_port") or 8800)


# --- Session signing key ------------------------------------------------------

def secret_key() -> str:
    """
    The Flask session signing key: ``BMEOS_SECRET`` if set, else a random key
    generated once and kept in config.json so logins survive a restart.

    Never a fixed default: the admin lock lives in the session cookie, so a key
    anyone can read in the source would let them forge an unlocked session.
    """
    env = os.environ.get("BMEOS_SECRET")
    if env:
        return env
    with _lock:
        stored = _load().get("secret_key")
        if isinstance(stored, str) and len(stored) >= 32:
            return stored
        key = secrets.token_hex(32)
        _write({"secret_key": key})
        return key


# --- Admin password (single shared password; hashed, never stored plaintext) ---

def admin_password_set() -> bool:
    return bool(_load().get("admin_password_hash"))


def set_admin_password(password: str) -> None:
    _write({"admin_password_hash": generate_password_hash(password)})


def clear_admin_password() -> None:
    _write({"admin_password_hash": ""})


def check_admin_password(password: str) -> bool:
    stored = _load().get("admin_password_hash")
    return bool(stored) and check_password_hash(stored, password or "")


# --- Saving ----------------------------------------------------------------

def _write(updates: dict) -> None:
    """Merge ``updates`` into config.json (atomic replace) and refresh the cache."""
    global _cache
    with _lock:
        data = dict(_load())
        data.update(updates)
        path = _path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
        _cache = data


def save(updates: dict, *, target: str = "auto", reset: list | tuple = ()) -> None:
    """
    Save settings. ``target``:

    * ``"auto"``: event-scoped keys go to the open event (or to this computer's
      defaults when no event is open); computer-scoped keys to config.json.
    * ``"event"``: event-scoped keys to the open event (error if none open).
    * ``"computer"``: everything to config.json (the defaults for every event).

    A blank value clears the setting at that target so it falls back (to the
    computer default for an event, to env/default for the computer), except a
    blank password, which means "keep". ``reset`` clears keys the same way.
    ``admin_password`` is hashed, never stored in the clear. Unknown keys are
    ignored; invalid values raise :class:`SettingError` before anything is saved.
    """
    to_event = target in ("auto", "event") and db.is_open()
    if target == "event" and not db.is_open():
        raise SettingError("Open an event to change its settings")
    computer: dict = {}
    event: dict = {}
    event_reset: list = []
    admin_password = None
    for key, raw in updates.items():
        if key == "admin_password":
            if isinstance(raw, str) and raw.strip():
                admin_password = raw
            continue
        spec = _BY_KEY.get(key)
        if spec is None:
            continue
        into_event = to_event and spec.scope == "event"
        blank = raw is None or (isinstance(raw, str) and raw.strip() == "")
        if spec.type == "password" and blank:
            continue  # write-only: blank means "keep the current value"
        if blank and spec.type != "bool":
            if into_event:
                event_reset.append(key)
            else:
                computer[key] = ""
            continue
        value = validate(spec, raw)
        if into_event:
            event[key] = value
        else:
            computer[key] = value
    for key in reset:
        spec = _BY_KEY.get(key)
        if spec is None or spec.type == "password":
            continue
        if to_event and spec.scope == "event":
            event_reset.append(key)
        else:
            computer[key] = ""
    if admin_password is not None:
        set_admin_password(admin_password)
    if computer:
        _write(computer)
    if event or event_reset:
        db.save_event_settings(event, event_reset)
    # Everything derived from settings (cached pages, results) refreshes.
    db.mark_changed()


def _field(spec, *, include_event: bool) -> dict:
    value = get(spec.key, include_event=include_event)
    field = {
        "key": spec.key, "label": spec.label, "help": spec.help, "type": spec.type,
        "scope": spec.scope, "restart": spec.restart,
        "choices": [{"value": v, "label": l} for v, l in spec.choices],
        "min": spec.minimum, "max": spec.maximum,
        "value": value, "default": spec.default,
        "source": source(spec.key, include_event=include_event),
    }
    if spec.type == "password":
        # Write-only: report whether it's set, never the value itself.
        field["is_set"] = bool(value)
        field["value"] = ""
    return field


def settings_view(*, page: str | None = None, target: str = "event",
                  query: str = "") -> list[dict]:
    """
    Grouped, display-ready settings for the Settings page / a page's gear menu.

    ``target="event"`` shows what the open event actually uses (event values
    first); ``"computer"`` shows the defaults kept on this computer. ``page``
    limits to the settings tagged for that page; ``query`` filters by text.
    """
    include_event = target == "event" and db.is_open()
    needle = query.strip().lower()
    groups: dict[str, list] = {g: [] for g in settings_schema.GROUPS}
    for spec in SCHEMA:
        if spec.hidden or (page and page not in spec.pages):
            continue
        if needle and needle not in f"{spec.label} {spec.help} {spec.group}".lower():
            continue
        groups[spec.group].append(_field(spec, include_event=include_event))
    security = {"key": "admin_password", "label": "Admin password",
                "help": "Locks the operator console. Leave blank to keep the current one.",
                "type": "password", "scope": "computer", "restart": False, "choices": [],
                "min": None, "max": None, "value": "", "default": "",
                "source": "computer" if admin_password_set() else "default",
                "is_set": admin_password_set()}
    if not page and (not needle or needle in "admin password security lock"):
        groups["Network & security"].append(security)
    return [{"group": g, "fields": f} for g, f in groups.items() if f]


def dashboard_values() -> list[dict]:
    """The full listing in the older shape (a separate "Security" group holding
    the admin password), kept for /api/config and older callers."""
    out = []
    for group in settings_view():
        fields = [f for f in group["fields"] if f["key"] != "admin_password"]
        if fields:
            out.append({"group": group["group"], "fields": fields})
    admin = next(f for g in settings_view(query="admin password") for f in g["fields"]
                 if f["key"] == "admin_password")
    out.append({"group": "Security", "fields": [admin]})
    return out


def page_has_settings(page: str | None) -> bool:
    return bool(page) and any(page in s.pages and not s.hidden for s in SCHEMA)
