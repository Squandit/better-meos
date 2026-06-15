"""
Operator-editable configuration (the Settings dashboard) backed by a JSON file.

Until now every interchangeable value (PayPal client id, ngrok domain, SMTP
credentials, entry fees, ...) lived only in environment variables. This module
adds a single ``config.json`` the operator can edit from the in-app Settings
page, while keeping the old env vars working as a fallback so nothing breaks for
existing deployments or the test suite.

Resolution order for :func:`get` is **config.json -> environment -> default**, so
a value typed into the dashboard wins, but an unset value still falls back to the
env var (and then the built-in default). Secrets are written to the file, which
is gitignored (see ``.gitignore``); the admin password is only ever stored as a
Werkzeug hash, never in plaintext.

Path: ``BMEOS_CONFIG`` env var, else ``config.json`` in the working directory.
"""

from __future__ import annotations

import json
import logging
import os
import threading

from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger("config")

# One known setting. ``env`` is the legacy environment-variable fallback; ``type``
# drives coercion + the dashboard input kind; ``group``/``label``/``help`` drive
# the dashboard layout. ``default`` is the final fallback.
class _Setting:
    __slots__ = ("key", "env", "type", "group", "label", "help", "default")

    def __init__(self, key, env, type="str", group="", label="", help="", default=""):
        self.key, self.env, self.type = key, env, type
        self.group, self.label, self.help, self.default = group, label, help, default


# Order here is the order shown on the dashboard. ``admin_password`` is special
# (write-only; stored hashed as ``admin_password_hash``) and handled separately.
SCHEMA: list[_Setting] = [
    _Setting("paypal_client_id", "PAYPAL_CLIENT_ID", "str", "Payments (PayPal)",
             "PayPal client ID", "Public client id from your PayPal app."),
    _Setting("paypal_sandbox", "PAYPAL_SANDBOX", "bool", "Payments (PayPal)",
             "Use PayPal sandbox", "On = test money only. Turn off to take real payments.",
             default=True),
    _Setting("currency", "BMEOS_CURRENCY", "str", "Pricing",
             "Currency", "ISO currency code, e.g. AUD.", default="AUD"),
    _Setting("fee_senior", "BMEOS_FEE_SENIOR", "float", "Pricing",
             "Senior fee", "", default=10.0),
    _Setting("fee_junior", "BMEOS_FEE_JUNIOR", "float", "Pricing",
             "Junior fee", "", default=5.0),
    _Setting("fee_concession", "BMEOS_FEE_CONCESSION", "float", "Pricing",
             "Concession fee", "", default=5.0),
    _Setting("family_cap", "BMEOS_FAMILY_CAP", "float", "Pricing",
             "Family cap", "Max total charged to one family.", default=25.0),
    _Setting("entry_close", "BMEOS_ENTRY_CLOSE", "str", "Entry page",
             "Entry close time", "Shown on the entry page (free text)."),
    _Setting("clubs", "BMEOS_CLUBS", "str", "Entry page",
             "Clubs", "Comma-separated list to override the club dropdown."),
    _Setting("ngrok_authtoken", "NGROK_AUTHTOKEN", "str", "Remote hosting (ngrok)",
             "ngrok authtoken", "Your ngrok token; without it tunnels are rate-limited."),
    _Setting("ngrok_domain", "NGROK_DOMAIN", "str", "Remote hosting (ngrok)",
             "ngrok domain", "Reserved/static domain so the public URL never changes."),
    _Setting("smtp_host", "BMEOS_SMTP_HOST", "str", "Email (receipts)",
             "SMTP host", "Leave blank to disable email."),
    _Setting("smtp_port", "BMEOS_SMTP_PORT", "int", "Email (receipts)",
             "SMTP port", "", default=587),
    _Setting("smtp_user", "BMEOS_SMTP_USER", "str", "Email (receipts)", "SMTP user", ""),
    _Setting("smtp_pass", "BMEOS_SMTP_PASS", "password", "Email (receipts)",
             "SMTP password", "e.g. a Gmail app password."),
    _Setting("smtp_from", "BMEOS_SMTP_FROM", "str", "Email (receipts)",
             "From address", "Defaults to the SMTP user."),
    _Setting("prize_season", "BMEOS_PRIZE_SEASON", "str", "Prizes",
             "Season label", "e.g. 2026. Blank = the open event's year. "
             "Change it to start a new prize season (resets eligibility)."),
    _Setting("series_points_base", "BMEOS_SERIES_BASE", "int", "Series",
             "Series points (1st place)", "Points the winner gets each event.",
             default=100),
    _Setting("series_points_step", "BMEOS_SERIES_STEP", "int", "Series",
             "Series points step", "Points lost per place below 1st.", default=2),
    _Setting("admin_port", "BMEOS_PORT", "int", "Network (ports)",
             "Admin port", "Operator console port. Restart to apply.", default=8799),
    _Setting("public_port", "BMEOS_PUBLIC_PORT", "int", "Network (ports)",
             "Public port", "Results + entry port (share this one). Restart to apply.",
             default=8800),
]

_BY_KEY = {s.key: s for s in SCHEMA}

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
    if type == "bool":
        if isinstance(value, str):
            return value.strip().lower() not in ("", "0", "false", "no", "off")
        return bool(value)
    if type == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if type == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return str(value)


def get(key: str):
    """Resolved value: config.json -> environment -> default (coerced by type)."""
    spec = _BY_KEY.get(key)
    type = spec.type if spec else "str"
    stored = _load().get(key)
    if stored not in (None, ""):
        return _coerce(stored, type)
    if spec and spec.env:
        env = os.environ.get(spec.env)
        if env not in (None, ""):
            return _coerce(env, type)
    return spec.default if spec else None


def get_str(key: str) -> str:
    value = get(key)
    return "" if value is None else str(value)


def admin_port() -> int:
    return int(get("admin_port") or 8799)


def public_port() -> int:
    return int(get("public_port") or 8800)


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


def save(updates: dict) -> None:
    """
    Save dashboard fields. Each known key is coerced to its type and stored;
    ``admin_password`` (when non-empty) is hashed via :func:`set_admin_password`
    and never written in the clear. Unknown keys are ignored.
    """
    clean: dict = {}
    for key, raw in updates.items():
        if key == "admin_password":
            if isinstance(raw, str) and raw.strip():
                set_admin_password(raw)
            continue
        spec = _BY_KEY.get(key)
        if spec is None:
            continue
        if spec.type == "bool":
            clean[key] = bool(raw)
        elif raw in (None, ""):
            clean[key] = ""  # cleared field -> fall back to env/default again
        else:
            coerced = _coerce(raw, spec.type)
            clean[key] = "" if coerced is None else coerced
    if clean:
        _write(clean)


def dashboard_values() -> list[dict]:
    """Grouped, display-ready settings for the dashboard form (no secrets leaked
    as hashes; the admin password is reported only as set/not-set)."""
    groups: dict[str, list] = {}
    for spec in SCHEMA:
        groups.setdefault(spec.group, []).append({
            "key": spec.key,
            "label": spec.label,
            "help": spec.help,
            "type": spec.type,
            "value": get(spec.key),
        })
    out = [{"group": g, "fields": fields} for g, fields in groups.items()]
    out.append({"group": "Security", "fields": [{
        "key": "admin_password",
        "label": "Admin password",
        "help": "Locks the operator console. Leave blank to keep the current one.",
        "type": "password",
        "value": "",
        "is_set": admin_password_set(),
    }]})
    return out
