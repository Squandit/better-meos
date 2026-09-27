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
  every event". ``inherit=False`` settings have no such default: each event
  says for itself (which season an event belongs to, say).

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
    inherit: bool = True        # event scope: False = no "default for every event"

    def __post_init__(self):
        assert self.type in TYPES, self.type
        assert self.scope in SCOPES, self.scope
        assert not (self.type == "password" and self.scope == "event"), \
            f"{self.key}: secrets must stay on this computer"
        assert self.type != "choice" or self.choices, f"{self.key}: choice needs choices"
        assert self.inherit or self.scope == "event", f"{self.key}: only event settings can opt out"


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
    S("control_config", "Control statuses", "json", "", "Readout & printing",
      "Per control code: bad / optional / no timing, and alternate codes (Controls page).",
      scope="event", hidden=True, inherit=False),
    S("overdue_after_minutes", "Overdue after (minutes)", "int", 0, "Home screen",
      "Flag runners still out after this long when their course has no max time "
      "(0 = only use max times).", scope="event", pages=("overview", "speaker"),
      minimum=0, maximum=1440),

    # ---- Results & live screen (per event) ------------------------------------
    S("time_format", "Time format", "choice", "auto", "Results & live screen",
      "How running times are written: results, live screen, splits, slips and PDFs.",
      scope="event", pages=("results", "splits"),
      choices=(("auto", "45:09 and 1:05:09"), ("minutes", "Minutes only: 65:09"),
               ("hms", "Always with hours: 01:05:09"))),
    S("results_group_by", "Results by", "choice", "class", "Results & live screen",
      "By course ranks everyone on a course together, whatever their class "
      "(common at small local events).", scope="event", pages=("results",),
      choices=(("class", "Class"), ("course", "Course"))),
    S("results_unplaced", "Unplaced runners", "choice", "no_dns", "Results & live screen",
      "Who is listed under the placed runners.", scope="event", pages=("results",),
      choices=(("all", "Everyone: MP, DNF, DSQ and DNS"),
               ("no_dns", "MP, DNF and DSQ (leave out DNS)"),
               ("placed", "Nobody, only placed runners"))),
    S("results_show_on_course", "Show who's still out", "bool", True,
      "Results & live screen",
      "Lists runners who have started but not read out yet under their class.",
      scope="event", pages=("results",)),
    S("results_show_behind", "Show time behind the winner", "bool", True,
      "Results & live screen", "", scope="event", pages=("results",)),
    S("results_show_splits", "Click a runner to see their splits", "bool", True,
      "Results & live screen", "On the results pages, including the public one.",
      scope="event", pages=("results",)),
    S("class_order", "Class order", "text", "", "Results & live screen",
      "Class names in the order to list them, separated by commas (e.g. M21E, W21E, "
      "M20E). Classes you leave out follow in name order, with W8 before W10.",
      scope="event", pages=("results", "classes")),
    S("live_title", "Live screen heading", "str", "", "Results & live screen",
      "Blank = the event name.", scope="event", pages=("results",)),
    S("live_rows", "Live screen: runners per class", "int", 8, "Results & live screen",
      "", scope="event", pages=("results",), minimum=1, maximum=100),
    S("live_classes", "Live screen: classes", "text", "", "Results & live screen",
      "Blank = all. For a second screen with its own classes, open "
      "/live?classes=M21E,W21E on it instead.", scope="event", pages=("results",)),
    S("live_page_seconds", "Live screen: seconds per page", "int", 15,
      "Results & live screen",
      "When the classes don't fit on the screen it pages through them "
      "(0 = scroll instead).", scope="event", pages=("results",),
      minimum=0, maximum=600),
    S("live_show_latest", "Live screen: latest finishers ticker", "bool", True,
      "Results & live screen", "", scope="event", pages=("results",)),
    S("live_show_clock", "Live screen: clock", "bool", True, "Results & live screen", "",
      scope="event", pages=("results",)),
    S("live_text_size", "Live screen: text size", "choice", "100", "Results & live screen",
      "", scope="event", pages=("results",),
      choices=(("80", "Small"), ("100", "Normal"), ("125", "Large"), ("150", "Huge"),
               ("200", "Across the field"))),

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

    # ---- Readout & printing ----------------------------------------------------
    S("unknown_card_action", "Unknown cards", "choice", "keep", "Readout & printing",
      "When a card nobody has entered is read.", scope="event", pages=("download",),
      choices=(("keep", "Keep the read until I say whose it is"),
               ("auto_create", "Enter it automatically (course and class from its punches)"))),
    S("auto_print", "Print split slips automatically", "choice", "off", "Readout & printing",
      "Prints on this computer while the Download page is open.", pages=("download",),
      choices=(("off", "Off"), ("all", "Every card read"), ("ok", "Only OK runs"),
               ("not_ok", "Only mispunches and other problems"))),
    S("slip_show_place", "Slip: place and time behind", "bool", True, "Readout & printing",
      "", scope="event", pages=("download",)),
    S("slip_leg_places", "Slip: place on each leg", "bool", True, "Readout & printing",
      "The runner's place in their class for each leg.", scope="event", pages=("download",)),
    S("slip_footer", "Slip: footer text", "str", "", "Readout & printing",
      "e.g. Results at example.org. Thanks for running!", scope="event",
      pages=("download",)),
    S("readout_sound", "Readout screen: beep", "bool", True, "Readout & printing",
      "Two short beeps for OK, one long low tone for anything else.", pages=("download",)),
    S("readout_show_place", "Readout screen: show the place", "bool", True,
      "Readout & printing", "", scope="event", pages=("download",)),
    S("silent_print", "Print split slips without a dialog", "bool", False,
      "Readout & printing",
      "Opens the console in Edge/Chrome with kiosk printing, so auto-print goes "
      "straight to the default printer.", env="BMEOS_SILENT_PRINT",
      pages=("download",), restart=True),
    S("slip_paper", "Split slip paper", "choice", "80mm", "Readout & printing", "",
      env="BMEOS_SLIP_PAPER", pages=("download",),
      choices=(("80mm", "80 mm receipt printer"), ("a4", "A4 / Letter"))),

    # ---- Prizes & season ------------------------------------------------------
    S("prize_places", "Prizes per class", "int", 3, "Prizes & season",
      "How many places get a prize.", env="BMEOS_PRIZE_PLACES",
      scope="event", pages=("results", "prizes"), minimum=1, maximum=50),
    S("prize_share", "At most this share of starters", "int", 0, "Prizes & season",
      "Small classes get fewer prizes: e.g. 33 gives a third of the starters a "
      "prize, rounded up (0 = always the full number).", scope="event",
      pages=("prizes",), minimum=0, maximum=100),
    S("prize_classes", "Classes with prizes", "text", "", "Prizes & season",
      "Comma separated, e.g. juniors only (blank = every class).", scope="event",
      pages=("prizes",)),
    S("prize_one_per_season", "One prize per season", "bool", False, "Prizes & season",
      "Someone who won a prize at an earlier event this season is passed over "
      "and the prize goes to the next runner. Needs the event's season set.",
      scope="event", pages=("prizes", "season")),
    S("season_name", "Season", "str", "", "Prizes & season",
      "Events with the same season name count together for standings and prizes, "
      "e.g. Summer Series 2026. Set it on each event.", scope="event", inherit=False,
      pages=("prizes", "season", "setup")),
    S("season_scoring", "Season points", "choice", "points_table", "Prizes & season",
      "", scope="event", pages=("season",),
      choices=(("points_table", "By place, from the points table"),
               ("time_ratio", "Winner's time ÷ your time × the top score"))),
    S("season_points", "Points table", "str", "25,20,16,13,11,10,9,8,7,6,5,4,3,2,1",
      "Prizes & season", "Points for 1st, 2nd, 3rd…", scope="event", pages=("season",)),
    S("season_max_points", "Top score (time ratio)", "int", 100, "Prizes & season",
      "The winner's points; everyone else gets a share by time (score courses: "
      "by points).", scope="event", pages=("season",), minimum=1, maximum=10000),
    S("season_finish_points", "Points for other finishers", "int", 0, "Prizes & season",
      "OK runs past the end of the points table.", scope="event", pages=("season",),
      minimum=0, maximum=10000),
    S("season_start_points", "Points for MP / DNF", "int", 0, "Prizes & season",
      "For turning up and trying (0 = none).", scope="event", pages=("season",),
      minimum=0, maximum=10000),
    S("season_best_of", "Best results that count", "int", 0, "Prizes & season",
      "Only each runner's best N events count (0 = all).", scope="event",
      pages=("season",), minimum=0, maximum=100),
    S("season_min_events", "Events needed to be ranked", "int", 0, "Prizes & season",
      "", scope="event", pages=("season",), minimum=0, maximum=100),

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
