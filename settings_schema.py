"""
The catalogue of every operator-editable setting.

Each :class:`Setting` says what it is (type, choices, limits), where it lives
(``scope``) and where it shows up (``group`` on the master Settings page,
``pages`` for the gear menu on individual pages). ``config.py`` does the storage
and lookup; this module is only the declarations, so adding a setting is one
entry here plus the code that reads it with ``config.get(key)``.

Scopes:

* ``computer``: stored in ``config.json`` on this PC (ports, printers,
  credentials, look and feel). Same for every event run here.
* ``event``: stored inside the event file, so it travels with the event
  (fees, results display, prize rules). An event that hasn't set a value uses
  the computer's value, which is edited on the Settings page as "Defaults for
  every event".

Keep secrets (``password`` type) computer-scoped: an event file gets copied
around (backups, other PCs), so it must never carry credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TYPES = ("bool", "int", "float", "str", "text", "password", "choice", "time",
         "datetime", "date", "json")
SCOPES = ("computer", "event")


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    type: str = "str"
    default: Any = ""
    group: str = ""
    help: str = ""
    env: str | None = None
    scope: str = "computer"
    pages: tuple[str, ...] = ()
    choices: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    restart: bool = False       # takes effect after restarting the app
    hidden: bool = False        # stored + read by code, never shown as a field

    def __post_init__(self):
        assert self.type in TYPES, self.type
        assert self.scope in SCOPES, self.scope
        assert not (self.type == "password" and self.scope == "event"), \
            f"{self.key}: secrets must stay on this computer"
        assert self.type != "choice" or self.choices, f"{self.key}: choice needs choices"


# Order of sections on the master Settings page.
GROUPS = (
    "Appearance",
    "Home screen",
    "Results & live screen",
    "Readout & printing",
    "Start",
    "Entries & fees",
    "Online payments (PayPal)",
    "Economy",
    "Prizes & season",
    "Speaker",
    "SI reader",
    "Backups",
    "Online results",
    "Email",
    "Remote hosting (ngrok)",
    "Eventor",
    "Network & security",
)

S = Setting

SCHEMA: list[Setting] = [
    # ---- Appearance (this computer) --------------------------------------------
    S("theme", "Theme", "choice", "light", "Appearance",
      "Auto follows the computer's light/dark setting.",
      pages=("overview",),
      choices=(("light", "Light"), ("dark", "Dark"), ("auto", "Auto"))),
    S("accent", "Accent colour", "choice", "forest", "Appearance",
      "Buttons, highlights and the active menu item.", pages=("overview",),
      choices=(("forest", "Forest green"), ("ocean", "Ocean blue"), ("violet", "Violet"),
               ("sunset", "Sunset orange"), ("berry", "Berry"), ("teal", "Teal"),
               ("graphite", "Graphite"))),
    S("density", "Density", "choice", "comfortable", "Appearance",
      "Compact fits more rows on a small screen.", pages=("overview",),
      choices=(("comfortable", "Comfortable"), ("compact", "Compact"))),
    S("text_size", "Text size", "choice", "100", "Appearance",
      "Scales the whole console (handy on a projector or a tiny laptop).",
      pages=("overview",),
      choices=(("90", "Small"), ("100", "Normal"), ("110", "Large"), ("125", "Extra large"))),

    # ---- Home screen -----------------------------------------------------------
    S("dashboard_layout", "Home screen layout", "json", "", "Home screen",
      "Which widgets show, in what order and size (set with Customise).",
      hidden=True),
    S("dashboard_notes", "Notes", "text", "", "Home screen", "", scope="event", hidden=True),
    S("overdue_after_minutes", "Overdue after (minutes)", "int", 0, "Home screen",
      "Flag runners still out after this long when their course has no max time "
      "(0 = only use max times).", scope="event", pages=("overview", "speaker"),
      minimum=0, maximum=1440),

    # ---- Entries & fees (per event) ----------------------------------------
    S("currency", "Currency", "str", "AUD", "Entries & fees",
      "ISO currency code, e.g. AUD. Must match your PayPal account.",
      env="BMEOS_CURRENCY", pages=("entries", "economy")),
    S("fee_senior", "Senior fee", "float", 10.0, "Entries & fees", "",
      env="BMEOS_FEE_SENIOR", scope="event", pages=("entries", "economy"), minimum=0),
    S("fee_junior", "Junior fee", "float", 5.0, "Entries & fees", "",
      env="BMEOS_FEE_JUNIOR", scope="event", pages=("entries", "economy"), minimum=0),
    S("fee_concession", "Concession fee", "float", 5.0, "Entries & fees", "",
      env="BMEOS_FEE_CONCESSION", scope="event", pages=("entries", "economy"), minimum=0),
    S("family_cap", "Family cap", "float", 25.0, "Entries & fees",
      "Most one online checkout is charged, however many people are in it (0 = no cap).",
      env="BMEOS_FAMILY_CAP", scope="event", pages=("entries", "economy"), minimum=0),
    S("late_fee", "Late entry surcharge", "float", 0.0, "Entries & fees",
      "Added per person from the late-entry time on.",
      env="BMEOS_LATE_FEE", scope="event", pages=("entries", "economy"), minimum=0),
    S("late_fee_from", "Late entries from", "datetime", "", "Entries & fees",
      "When the surcharge starts.", env="BMEOS_LATE_FEE_FROM", scope="event",
      pages=("entries",)),
    S("trust_member_type", "Let entrants choose junior / concession", "bool", False,
      "Entries & fees",
      "Off = everyone pays the senior fee (nothing checks what they pick). "
      "Turn on if you're happy to trust entrants.",
      env="BMEOS_TRUST_MEMBER_TYPE", scope="event", pages=("entries",)),
    S("entry_close", "Online entries close", "datetime", "", "Entries & fees",
      "After this, the entry page stops taking entries.",
      env="BMEOS_ENTRY_CLOSE", scope="event", pages=("entries",)),
    S("clubs", "Club list", "text", "", "Entries & fees",
      "Comma-separated clubs for the entry page's club dropdown "
      "(blank = clubs already in the event).", env="BMEOS_CLUBS", pages=("entries",)),

    # ---- Online payments -----------------------------------------------------
    S("paypal_client_id", "PayPal client ID", "str", "", "Online payments (PayPal)",
      "Public client id from your PayPal app.", env="PAYPAL_CLIENT_ID",
      pages=("entries",)),
    S("paypal_client_secret", "PayPal client secret", "password", "",
      "Online payments (PayPal)",
      "Secret from the same PayPal app. The server needs it to create and verify "
      "every payment, so paid online entry stays off without it.",
      env="PAYPAL_CLIENT_SECRET", pages=("entries",)),
    S("paypal_sandbox", "Use PayPal sandbox", "bool", True, "Online payments (PayPal)",
      "On = test money only. Turn off to take real payments.",
      env="PAYPAL_SANDBOX", pages=("entries",)),

    # ---- Printing -------------------------------------------------------------
    S("silent_print", "Print split slips without a dialog", "bool", False,
      "Readout & printing",
      "Opens the console in Edge/Chrome with kiosk printing, so auto-print goes "
      "straight to the default printer.", env="BMEOS_SILENT_PRINT",
      pages=("download",), restart=True),
    S("slip_paper", "Split slip paper", "choice", "80mm", "Readout & printing", "",
      env="BMEOS_SLIP_PAPER", pages=("download",),
      choices=(("80mm", "80 mm receipt printer"), ("a4", "A4 / Letter"))),

    # ---- Prizes ---------------------------------------------------------------
    S("prize_places", "Prizes per class", "int", 3, "Prizes & season",
      "How many places get a prize (prize list PDF).", env="BMEOS_PRIZE_PLACES",
      scope="event", pages=("results",), minimum=1, maximum=50),

    # ---- SI reader -----------------------------------------------------------
    S("reader_enabled", "Use a real SI reader", "bool", False, "SI reader",
      "Off = simulated downloads only.", env="BMEOS_READER", pages=("download",)),
    S("reader_ports", "Reader ports", "str", "", "SI reader",
      "e.g. COM5, or COM5:finish,COM6:start for several stations.",
      env="BMEOS_READER_PORTS", pages=("download",)),

    # ---- Backups -------------------------------------------------------------
    S("backup_dir", "Backup folder", "str", "", "Backups",
      "Blank = this PC's local app data (not synced by OneDrive).",
      env="BMEOS_BACKUP_DIR", pages=("setup",)),
    S("backup_dir_2", "Second backup folder", "str", "", "Backups",
      "Optional: a USB stick or network share.", env="BMEOS_BACKUP_DIR_2",
      pages=("setup",)),
    S("backup_interval_minutes", "Back up every (minutes)", "int", 3, "Backups",
      "Only when something changed.", env="BMEOS_BACKUP_MINUTES", pages=("setup",),
      minimum=1, maximum=120),
    S("backup_keep", "Backups to keep per event", "int", 30, "Backups", "",
      env="BMEOS_BACKUP_KEEP", pages=("setup",), minimum=1, maximum=1000),

    # ---- Online results ------------------------------------------------------
    S("publish_dir", "Publish folder", "str", "", "Online results",
      "Write results.html + results.xml here whenever results change (blank = off).",
      env="BMEOS_PUBLISH_DIR", pages=("setup", "results")),
    S("ftp_host", "FTP host", "str", "", "Online results",
      "Also upload them to the club website (blank = off).", env="BMEOS_FTP_HOST",
      pages=("setup",)),
    S("ftp_port", "FTP port", "int", 21, "Online results", "", env="BMEOS_FTP_PORT",
      pages=("setup",), minimum=1, maximum=65535),
    S("ftp_user", "FTP user", "str", "", "Online results", "", env="BMEOS_FTP_USER",
      pages=("setup",)),
    S("ftp_pass", "FTP password", "password", "", "Online results", "",
      env="BMEOS_FTP_PASS", pages=("setup",)),
    S("ftp_dir", "FTP folder", "str", "", "Online results", "e.g. public_html/results",
      env="BMEOS_FTP_DIR", pages=("setup",)),
    S("ftp_tls", "Use FTPS (encrypted)", "bool", True, "Online results",
      "Leave on unless the host only speaks plain FTP.", env="BMEOS_FTP_TLS",
      pages=("setup",)),
    S("publish_interval_seconds", "Publish at most every (seconds)", "int", 60,
      "Online results", "", env="BMEOS_PUBLISH_SECONDS", pages=("setup",),
      minimum=10, maximum=3600),

    # ---- Email ---------------------------------------------------------------
    S("smtp_host", "SMTP host", "str", "", "Email", "Leave blank to disable email.",
      env="BMEOS_SMTP_HOST"),
    S("smtp_port", "SMTP port", "int", 587, "Email", "", env="BMEOS_SMTP_PORT",
      minimum=1, maximum=65535),
    S("smtp_user", "SMTP user", "str", "", "Email", "", env="BMEOS_SMTP_USER"),
    S("smtp_pass", "SMTP password", "password", "", "Email",
      "e.g. a Gmail app password.", env="BMEOS_SMTP_PASS"),
    S("smtp_from", "From address", "str", "", "Email", "Defaults to the SMTP user.",
      env="BMEOS_SMTP_FROM"),

    # ---- Remote hosting --------------------------------------------------------
    S("ngrok_authtoken", "ngrok authtoken", "password", "", "Remote hosting (ngrok)",
      "Your ngrok token; without it tunnels are rate-limited.", env="NGROK_AUTHTOKEN",
      pages=("setup",)),
    S("ngrok_domain", "ngrok domain", "str", "", "Remote hosting (ngrok)",
      "Reserved/static domain so the public URL never changes.", env="NGROK_DOMAIN",
      pages=("setup",)),

    # ---- Eventor ---------------------------------------------------------------
    S("eventor_base_url", "Eventor address", "str", "", "Eventor",
      "e.g. https://eventor.orienteering.asn.au", env="EVENTOR_BASE_URL",
      pages=("tools",)),
    S("eventor_api_key", "Eventor API key", "password", "", "Eventor",
      "Your club's API key (Eventor -> club admin).", env="EVENTOR_API_KEY",
      pages=("tools",)),

    # ---- Network & security ------------------------------------------------------
    S("admin_port", "Admin port", "int", 8799, "Network & security",
      "Operator console port.", env="BMEOS_PORT", restart=True,
      minimum=1, maximum=65535),
    S("public_port", "Public port", "int", 8800, "Network & security",
      "Results + entry port (share this one).", env="BMEOS_PUBLIC_PORT", restart=True,
      minimum=1, maximum=65535),
    S("admin_lan", "Allow the console from other computers", "bool", False,
      "Network & security",
      "Off = the operator console only answers on this PC. Turn on for a second "
      "operator laptop or secondary download stations (needs an admin password).",
      env="BMEOS_ADMIN_LAN", restart=True),
    S("station_token", "Station token", "password", "", "Network & security",
      "Shared secret that secondary download stations and radio controls send with "
      "each punch. Set the same value on every station.", env="BMEOS_STATION_TOKEN"),
]

BY_KEY: dict[str, Setting] = {}
for _s in SCHEMA:
    assert _s.key not in BY_KEY, f"duplicate setting {_s.key}"
    assert _s.group in GROUPS, f"{_s.key}: unknown group {_s.group!r}"
    BY_KEY[_s.key] = _s
