# better-meos — full project handoff

A complete record of everything built so you can continue on another computer.
Read top-to-bottom once; after that the **Architecture** and **Routes** sections
are the day-to-day reference.

---

## 0. What this is

A web-based orienteering event management system — a friendlier, simpler
alternative to **MeOS** that anyone in the club can operate, with more
functionality. Local Flask web app, SQLite storage, vanilla-JS frontend (no
build step), SI-card timing, live results, online entry, and a packaged Windows
`.exe`. Goal: clean interface first, broad functionality second.

- **Stack:** Python 3.12 / Flask (NOT FastAPI, despite an old note in CLAUDE.md),
  SQLite, server-rendered Jinja templates + a little vanilla JS, SSE for live
  updates, `sportident` lib for the SI reader, `reportlab` for PDFs, `waitress`
  to serve the packaged app, `pyngrok` for remote hosting.
- **Platform:** Windows. The repo lives in OneDrive at
  `C:\Users\quinn\Desktop\OneDrive - education.wa.edu.au\.better-meos`.
- **venv:** `venv/` (NOT `.venv/`). Run Python as `venv\Scripts\python.exe`.

---

## 1. ⚠️ CURRENT STATE — read this first

### 1.5 DONE: settings, flow and MeOS parity (2026-09-27, cloud session)
Committed straight to main, one commit per feature. Tests: **256 passing**
(`python -m pytest -q`); every new page checked in Chromium under waitress.

- **Settings v2** (`settings_schema.py` declares every setting, `config.py`
  resolves them): event file -> config.json -> env -> default. Scopes are
  `computer` and `event` (event values travel in the event file's `settings`
  table). `inherit=False` settings (season name, control statuses) have no
  "default for every event". Secrets are computer-only. Master page /config
  (search, "This event" vs "Defaults for every event", `?q=` deep link) and a
  gear drawer on every page with settings (`pages=` in the schema). Changes
  save as you make them; any save bumps `db.revision()` so cached pages and
  results refresh.
- **Appearance**: theme light/dark/auto, accent colour, density, text size
  (per computer), via `data-*` on `<html>` and CSS variables.
- **Home screen** (`dashboard.py`, `templates/widgets/`): 17 widgets, layout
  per computer, Customise mode (add, remove, drag, resize). Notes widget saves
  with the event. Checklist widget.
- **"Pending" status** (important): a runner with no card read and no finish
  is `pending` (shown On course / Not started / Not read), never DNF/DNS.
  `competitors.read_at` is set by every card read, auto-create, imports and a
  typed-in finish; old files are backfilled from existing runs. IOF export:
  Active / Inactive.
- **Results display** (`display.py` is now the view layer: `view_row`,
  `console_data`, `results_view`, time formats, class order, slip view,
  runner analysis). Per-event settings: time format (45:09 / 65:09 /
  01:05:09), results by class or course, which unplaced runners show, who's
  still out, time behind, clickable splits, custom class order (natural sort
  otherwise). `results_view(public=...)`: public views drop hidden classes.
- **Live screen**: pages through classes that don't fit, refreshes in place
  (`static/livescreen.js` + `window.BMSoftRefresh` hook in live.js), fresh
  finishers glow, ticker, clock, text size, `?classes=` per screen.
- **Download desk**: quick entry for unknown cards (class guessed from the
  punches, name from the runner DB), "Unknown cards" setting replaces
  BMEOS_AUTO_CREATE (still honoured). **Auto-print rewritten**: hidden print
  frames (`static/printing.js`), read-sequence based, rules off/all/OK/problems
  per computer. The old window.open version was popup-blocked. Slips: course
  order, leg places, place/behind, footer.
- **Engine fixes**: a check punch later than the start is ignored (stale
  check / night event used to wipe out whole runs).
- **Seasons + prizes** (`season.py`, /season, /prizes): events with the same
  Season name count together. Points table or time ratio, best N, minimum
  events, participation points. Prize rules: places, share of starters,
  classes, **one prize per season** (earlier winners passed over, earlier
  events judged by their own rules). Prize PDF follows the same list.
- **Control statuses** (`controls.py`, /controls): bad / optional / no timing /
  alternate codes per event (hidden json setting `control_config`), applied in
  the engine (`results.parse_control_config`, `linear_course_rules`) and in
  every split view (`store.split_controls`). Page shows punched/missed, first
  and last punch, typical leg, stray codes.
- **Class options**: results normal / names only / hidden, max entries,
  online entry on/off (enforced in online_entry, entry page class list).
- **Course import**: OCAD / Purple Pen ClassCourseAssignment creates or moves
  classes; re-import updates courses by name instead of duplicating.
- **Economy**: per-runner fee (blank = class fee), paid, method; hire card
  fee; one-click payments; club invoices PDF. Online entries arrive paid with
  exact shares (`payments._shares` spreads the family-cap discount).
- **Start clock**: call-up list N minutes ahead, SI-style beeps (per computer,
  one click to enable sound). **Entry page**: on/off switch, organiser message.
- **Command palette** (Ctrl+K or "/", `static/palette.js`, `/api/search`):
  runners by name/card/bib -> editor, classes, clubs, pages, settings, actions.
- **Checklists** (`checklist.py`, Setup page): race day, and close-out with
  runners still out by name and "mark the rest DNS".
- **Runner analysis** (`/public/<slug>/runner/<id>`): leg table, estimated time
  loss (vs the mean of the best three, scaled by the runner's median pace),
  behind-the-fastest graph. `/public/<slug>/results.json` for websites.
- **Sidebar**: Essentials (Overview, Competitors, Classes, Courses, Start draw,
  Download, Results) always shown; everything else under a collapsible "More
  tools" (remembered per browser, open when you're on one of its pages).
- **Help panel** (`static/help.js`, `?` / F1 or the ? button): per-page help with
  the MeOS equivalent and "Show me" buttons that spotlight controls, a MeOS tab
  -> page map, and guided tours that move page to page highlighting each
  button (state in sessionStorage). Tour steps are data at the top of help.js;
  a step whose element is missing shows as a centred card.
- **Installer + data folder**: the packaged app keeps everything in
  `%USERPROFILE%\better-meos` (events\, config.json, runners.db; backups stay in
  %LOCALAPPDATA%), wherever the exe runs from; data an older exe left beside
  itself is copied in once; `portable.txt` next to the exe keeps data beside it
  (USB stick). `installer/better-meos.iss` (Inno Setup) builds
  `better-meos-setup.exe` in CI: per-user install, no admin, shortcuts,
  uninstall leaves data alone. CI installs it silently and checks the app
  serves /start and writes to the user folder. Icon: installer/better-meos.ico.
- Small fixes: favicon (every page 404'd), numeric table headers right-aligned,
  macros imported `with context`.

**Still not done**: native .meos import; list designer; i18n; Emit/SRR radio
hardware; hire-card option on the online entry form (needs entries without a
card number); live PayPal / Eventor runs.

### 1.4 DONE — MeOS gap list (2026-09-27, cloud session)
One commit per item on `claude/project-review-roadmap-gi2jih` (then merged to
main). Tests: **194 passing**; every page checked in Chromium under waitress.
- **Night events**: times >12 h before the start count as after midnight
  (`results.after_midnight`); the editor only rejects small finish<start.
- **Max time** on linear courses (stored in `time_limit_minutes`) -> OOT;
  manual status **NC** (timed, never ranked); **vacant** start slots
  (`competitors.vacant`, hidden from results, filled by editing).
- **Start draw** (`draw.py`, /draw): random / clubs separated / alphabetical,
  vacants, classes on one course interleaved, late-entry mode. Entries draw
  uses club separation.
- **Automatic backups** (`backups.py`): every N min when changed, to local app
  data (+ optional 2nd folder), keep N; OneDrive warnings on start/setup.
- **Change log** (`audit_log` table, /audit): who did what, before -> after.
  Actor: login name / console IP / "SI reader (station)" / "online entry".
- **Reader fix**: `poll_sicard()` also fires on card *removal*; reading then
  raised and forced a reconnect after every runner. Removal is now skipped.
  Verified against sportident 1.2.8 source (keys card_number/start/finish/
  check/clear/punches).
- **Readout desk**: /readout big screen + OK/MP sounds; hire cards track
  `card_returned` (Economy lists outstanding); SI check time stored and punches
  before it ignored (download list flags "old punches").
- **Printing**: Settings "Print split slips without a dialog" -> launcher opens
  Edge/Chrome with `--kiosk-printing` (own profile); 80 mm slip by default.
- **Starter**: /starter start clock + now/next; starters-by-time PDF.
- **Relays/forking**: per-competitor `course_id` override; class
  `fork_courses` + "Assign forks" (rotation for relays); class `restart`;
  per-leg places; splits page one table per fork. Class kind `patrol` in UI.
- **Multi-stage** page (/stages): combined standings + chase starts (files
  picked by name from the events folder only).
- **IOF XML**: results SplitTimes in course order with Missing (WinSplits /
  Routegadget), default namespace; ResultList import (MeOS migration);
  CompetitorList -> runner DB.
- **Online results** (`publish.py`): results.html (self-contained) + .xml to a
  folder and/or FTPS on change. Prize list PDF (top N).
- **Speaker**: time to lead, radio split place/gap, predicted finish/place.
- **Fees**: late surcharge from a date; optional trusted junior/concession.
- **Eventor API** fetch (`/api/eventor/fetch`, key in Settings) — not tried
  against a live Eventor; IOF 2.0 answers give a clear error.
- **Logins** moved to the shared runners.db (fixes /start <-> /login loop with
  auth on and no event open; old per-event users adopted on open).
- **COM port detection** in Settings (SI / CP210x first).
- Sidebar scrolls (nav outgrew laptop screens).

**Still not done**: native .meos file import (use IOF XML export from MeOS);
list designer (fixed layouts + prize list only); i18n; Emit and SRR radio
hardware (untested / scaffold); real PayPal sandbox and live Eventor runs;
a hot-standby second PC (backups to a 2nd folder are the fallback).

### 1.3 DONE — performance pass (2026-09-26, cloud session)
Measured on a 1500-runner / 40-class / 15-control event (scratch benchmark, not
in the repo). Card reads were already fast (~2 ms); the cost was redoing the
same work for every viewer and committing row by row.
- `db.revision()` bumps on every write/open/close/restore (`db._commit()`
  wraps every commit). It is the single cache-invalidation signal.
- `store.evaluate()` caches its result per revision (35 ms -> ~0 on a hit).
  Callers get shared objects: never mutate them.
- `app.cached_page` caches the rendered HTML/JSON of `/results`, `/splits`,
  `/live`, `/clubs`, `/teams`, `/speaker`, `/public/<slug>`, `/get-results`
  per revision (+ path/query, port, user, lock/reader state), one render per
  page per revision even under a reload burst. 1 card read + 50 phones
  reloading `/results`: ~10 s CPU -> 0.19 s.
- `store.batch()` / `db.transaction()` = one commit for bulk work: imports,
  the draw, bib numbers (1.4 s -> 0.1 s here; far more on a Windows disk),
  course import, an online-entry cart, runner-DB CSV import.
- Splits leg/split ranks use `bisect` (was a linear count per cell).
- Removed dead code: `db.is_empty/all_events/next_event_id`, series + members
  functions, `store.evaluate_event/get_team` (legacy tables stay in the schema).
- Cold render of a huge event is still ~200 ms (/results) and ~330 ms (/splits):
  that's HTML volume. A per-class splits view would be the next step if needed.

### 1.2 DONE — security + payments hardening (2026-09-26, cloud session)
Branch `claude/project-review-roadmap-gi2jih`, on top of `main` (PR #1 merged, so
the 1.0/1.1 work below IS committed now; the "uncommitted" notes there are stale).
Tests: **151 passing** (`tests/test_payments.py` + `tests/test_hardening.py` new).
Also driven end to end in Chromium under waitress (free entry, duplicate card,
paid-mode "not set up" message, unmatched-card assign, stream cap fallback).

- **Session key:** no more hard-coded `"dev-insecure-key"` (it let anyone forge an
  unlocked admin session). `config.secret_key()` = `BMEOS_SECRET`, else a random
  key generated once and stored in `config.json`.
- **Secrets write-only:** password-type settings (`smtp_pass`, `ngrok_authtoken`,
  new `paypal_client_secret`, new `station_token`) are never returned by
  `GET /api/config`; blank on save = keep.
- **Console off the LAN by default:** launcher binds admin to 127.0.0.1 unless
  Settings "Allow the console from other computers" (`admin_lan`) is on, and
  `security.py` refuses non-loopback admin requests when no admin password is
  set. Dev server binds 127.0.0.1.
- **CSRF:** `security.py` refuses POST/PUT/PATCH/DELETE whose `Sec-Fetch-Site`
  isn't same-origin (or whose `Origin` host differs). Session cookie SameSite=Lax.
- **Open redirects:** `security.safe_next` on `/login` and `/unlock`.
- **Public port:** `/slip/`, `/api/entries`, `/submit-entry`, `/log-entries` gone
  from the allowlist (the last two are deleted). New public paths:
  `/api/online-entry/{order,capture}`, `/api/version`.
- **Station token:** `/api/station/push` + `/api/radio/punch` accept header
  `X-Station-Token` (Settings → Network) instead of a session; `network.py`
  sends it. A secondary's REAL reader now forwards too (only simulate did), and
  a failed push doesn't ack the card so the runner can read out again.
- **PayPal rebuilt server-side** (`online_entry.py` + `payments.py`): server
  validates + prices the cart (Decimal, family cap, everyone at the senior fee;
  client type ignored), freezes it in the new `online_orders` table, creates
  the PayPal order (Orders v2 REST) for that amount, then on approve captures
  it and verifies order id / COMPLETED / amount / currency / reference before
  creating competitors from the frozen cart. Idempotent capture (PayPal-Request-Id,
  new key per attempt after a decline), stale cart re-checked before capture
  (buyer not charged), "Pay again" supersedes the unpaid order, cards held
  while money is moving, entry-close enforced, per-client rate limit, receipt
  email built from server data only. Needs the PayPal **client secret** in
  Settings; without it paid entry is refused ("enter on the day"). Free events
  (fee 0) enter directly. Operator view on `/entries` ("Online payments"):
  statuses, "Check with PayPal" (`POST /api/orders/<id>/reconcile`), "Mark
  refunded" (`POST /api/orders/<id>/refunded`). Refunds are done in PayPal.
  **Not yet tested against the real PayPal sandbox** (no creds here) — do a
  sandbox run before going live.
- **Formulas:** `rules.py` caps powers (≤10), length, magnitude; every error is a
  `RuleError` (runtime falls back to base points), validation runs 3 sample runs.
- **SSE:** streams capped per port (`events.MAX_STREAMS`=12), 10 s keep-alive
  frees closed tabs, `live.js` falls back to polling `/api/version`. Waitress
  pools: public 32, admin 24 threads.
- **Punch window:** engine ignores punches before start / after finish.
- **Unmatched cards kept:** new `card_reads` table; download page lists them with
  Assign/Discard (`/api/card-reads/<id>/assign`, `DELETE /api/card-reads/<id>`);
  creating/editing a competitor with that card applies the read automatically.
- **Reader:** enable + ports now in Settings ("SI reader"; env fallback), started
  on event open/create and on Settings save; supervised thread reopens after a
  failure with backoff; per-station status on the download page. Hardware times
  re-pinned to the event date.
- **Relays:** legs with no start are timed from the previous leg's finish (leg 1
  from team start / mass start).
- `open_event` only accepts `.bmeos`; restore re-applies schema for old backups;
  editor team dropdown + entry page `escHtml` escape properly.

**Still open (not done this session):** events crossing midnight; relay mass
restart time; membership types for junior/concession pricing (everyone pays
senior); OneDrive-synced live SQLite files; automatic backups; Stripe path is
still a scaffold (operator-only now).

### 1.0 IN PROGRESS — MeOS-parity build + rename to "Punchcard" (2026-06-15, on the laptop)
Mid-task, paused to switch back to the PC. **Nothing here is committed and the
new code has NOT been run through pytest yet** — treat it as a work-in-progress
checkpoint, not a known-good state.

- **New product name chosen: `Punchcard`** (replaces the "better-meos" *display*
  name in the UI/docs). Decision: keep internal identifiers — the `.bmeos` file
  extension, `BMEOS_*` env vars, `better-meos.exe`, the GitHub repo slug — AS-IS
  for compatibility; only the human-facing name changes. **The rename has NOT
  been applied yet** (templates/README still say better-meos).

- **Laptop Python setup (so the OneDrive-synced `venv\` from the PC isn't touched):**
  - The committed/synced `venv\` is built for the PC (its base interpreter is
    `C:\Users\quinn\...`), so it does **not** run on the laptop.
  - Laptop uses a **separate venv OUTSIDE OneDrive**:
    `C:\Users\Quinn L School\.better-meos-venv` (won't sync back to the PC).
  - Run on the laptop via `START-laptop.bat`, or
    `"%USERPROFILE%\.better-meos-venv\Scripts\python" -m pytest -q`.
  - **On the PC: keep using `venv\` + `START.bat` exactly as before — unchanged.**
  - `START-laptop.bat` is a new untracked file (safe to commit or ignore).

- **Six MeOS-parity features being added** (the gaps from the feature comparison).
  Backend is written for all but UI/tests are pending:
  1. **Mass start** — engine support (`results.build_result`) + course `start_mode`
     `mass` with a `mass_start` time; **chase/handicap (Gundersson) start** via
     `stages.apply_chase_starts`.
  2. **Patrol classes** — new class `kind='patrol'`; combined-run result in
     `store._patrol_team` / `team_results`.
  3. **Multi-day / multi-stage** — new `stages.py`: `combined_results(paths)` sums
     per-competitor stage times across `.bmeos` files (matched by card, then
     name+club); `apply_chase_starts`. Backed by new `db.load_event_file(path)`.
  4. **Custom scoring rules** — new `rules.py`: safe AST expression evaluator;
     wired as course `score_formula` in the engine.
  5. **Auto course/class creation** from an unknown card — `store.auto_create_from_card`
     + `si_reader.process_card(auto_create=…)` (env `BMEOS_AUTO_CREATE`).
  6. **Multi-computer operation** — new `network.py` (secondary stations POST reads
     to a primary; env `BMEOS_PRIMARY`) + `POST /api/station/push` + secondary-push
     wired into `/api/reader/simulate`.

- **Files touched in this in-progress work:**
  - New: `rules.py`, `stages.py`, `network.py`, `START-laptop.bat`.
  - Edited: `db.py` (mass_start/score_formula cols + `_MIGRATIONS` + `save_course`
    /`load` + `load_event_file`/`_load_event_conn` refactor), `results.py`
    (mass-start + formula scoring; now `import rules`), `store.py` (`import rules`;
    engine_course/`_validated_course_fields`/`course_json`/`_insert_course`/
    `create_course` carry mass_start+score_formula; `_class_kind` accepts patrol;
    `create_team` allows patrol; `team_results` split into `_relay_team`/
    `_patrol_team`; new `auto_create_from_card`), `si_reader.py` (`AUTO_CREATE`,
    `process_card`/`simulate` gain `auto_create`), `app.py` (`import network`,
    `import stages`; `/api/station/push`; secondary push + `auto` flag in
    `/api/reader/simulate`).

- **REMAINING TO DO (resume here):**
  1. **Run pytest** with the (PC) venv and fix any breakage — the new code is
     UNVERIFIED. Likely watch-points: `results.py` now imports `rules` (ensure no
     import cycle); score-branch refactor must keep old penalty behaviour when no
     formula; `db._load_event_conn` indentation/rename; foreign-file reads.
  2. **UI**: add a `/stages` route + `templates/stages.html` (combined standings)
     + a nav link in `base.html`; add editor fields for the new course options
     (mass-start time, score formula) and the `patrol` class kind.
  3. **Apply the `Punchcard` rename** to user-facing strings (base.html title +
     sidebar, `start.html`, `setup.html`, `entry.html`, `README.md`). Leave
     `.bmeos`/`BMEOS_*`/exe/repo identifiers unchanged.
  4. **Write tests** for all six features (suggest `tests/test_meos_parity.py`);
     re-run pytest.
  5. **Verify `.gitignore`** still covers all secrets before committing
     (`config.json`, `*.db`, `.env`/`*.env` already listed — confirm nothing
     sensitive is staged, incl. `.claude/`).
  6. **Commit + push** (this overhaul *and* the still-uncommitted Efforts 3 & 4).

- **Git branch:** `feature/complete-order-features`
- **Remote:** `origin` = https://github.com/Squandit/better-meos.git
- **Commits on the branch:**
  - `71fbc6b` Initial commit (only the original app.py + templates/index.html)
  - `62695c6` Implement full order.txt feature set across 7 phases
  - `5e11256` Remove AI layer; port PayPal entry page; add MEOS-parity features
- **UNCOMMITTED:** the entire **MeOS-style workflow overhaul** (file-per-event,
  start page, runner DB, simulator, exe, ngrok, UI overhaul) **plus** a padding
  CSS fix are sitting in the **working tree, not committed** (~33 changed files).
  They have NOT been pushed. To move machines safely you must either:
  1. **Commit + push** (recommended):
     `git add -A && git commit -m "..." && git push -u origin feature/complete-order-features`
     then `git pull` on the other computer; OR
  2. Rely on **OneDrive sync** (the folder is in OneDrive, so source files sync —
     but `venv/`, `dist/`, `*.bmeos`, `runners.db` are gitignored, not OneDrive-
     ignored, so they sync too and can bloat/conflict; prefer git).
- **Tests:** 99 passing (`venv\Scripts\python -m pytest -q`).
- **The exe** (`dist/better-meos.exe`, ~20 MB) is built locally and gitignored;
  rebuild it on the other machine.

### 1.1 DONE — port-surface split, admin unlock, Settings dashboard (2026-06-15, PC)
Added on top of the above (tests now **110 passing**):
- **Two ports** (`launcher.py` serves both from one process / shared store):
  the **admin** console on `admin_port` (default **8799**, env `BMEOS_PORT`) and
  a **public** surface on `public_port` (default **8800**, env
  `BMEOS_PUBLIC_PORT`) serving only *results + the entry form*. New `security.py`
  classes each request by `SERVER_PORT`; the public port 404s anything outside a
  small allowlist (`/results`, `/splits`, `/clubs`, `/live`, `/enter` + entry
  backend, `/api/entries`, `/api/stream`, `/static`, `/public`, `/slip`) and
  redirects `/` → `/results`. Other ports (dev server, test client) = full admin
  surface, so the suite is unaffected. ngrok now tunnels the **public** port.
- **Admin unlock gate** (`security.py` + `/unlock`,`/lock` in `app.py`): when an
  admin password is set, the admin surface needs unlocking once per session
  (permanent session, **1-day** inactivity lifetime → survives refreshes,
  re-prompts on a fresh/idle session). No password set = no gate.
- **Settings dashboard** (`/config` + `templates/config.html`, `GET/POST
  /api/config`): edits the interchangeable values into a **`config.json`**.
  New `config.py` resolves **config.json → env → default**, so old `BMEOS_*`
  env vars still work as fallbacks. The admin password is stored only as a
  Werkzeug hash (`admin_password_hash`); `config.json` is gitignored.
  `payments.py`/`remote.py`/`notify.py`/`app._entry_config` now read via
  `config.get`. Nav "Settings" link + topbar "Lock" link + Setup tile added.
- **New files:** `config.py`, `security.py`, `templates/{config,unlock}.html`,
  `tests/test_security.py`. **conftest** points `BMEOS_CONFIG` at a temp file.

---

## 2. Continue on another computer (setup)

```bat
REM 1. Get the code (after you commit+push from this machine)
git clone https://github.com/Squandit/better-meos.git
cd better-meos
git checkout feature/complete-order-features

REM 2. Recreate the venv + install deps (venv is NOT in git)
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt

REM 3. Run it (dev) — opens the browser at the start page
venv\Scripts\python launcher.py
REM   ...or START.bat, or for the raw Flask dev server: venv\Scripts\python app.py

REM 4. Tests
venv\Scripts\python -m pytest -q

REM 5. Build the operator exe -> dist\better-meos.exe
venv\Scripts\pyinstaller better-meos.spec
```

Requirements (in `requirements.txt`): Flask, waitress, sportident, pyngrok,
reportlab, stripe, pytest, pyinstaller.

---

## 3. Architecture

### 3.1 The big model shift (file-per-event)
Originally one SQLite DB held everything (with an in-DB "events" table + a
switcher). **Now each event is its own `.bmeos` SQLite file** in an events folder
(`BMEOS_EVENTS_DIR`, default `./events`), opened from a start page — like MeOS
`.meos` files. Within a file the single event row is `id = 1`; all the per-event
engine/store code is unchanged because a file looks exactly like the old
single-event DB. A **separate** `runners.db` (the competitor database) persists
across all events. The app **opens no event on import**; it boots to the start
page and a `before_request` guard redirects everything else there until an event
is open.

### 3.2 Python modules (all in the repo root)
- **`app.py`** — Flask app: all routes, the open-event guard, context processors,
  the rendered-page cache. The orchestration layer.
- **`display.py`** — results as people see them: status labels, time formats,
  `view_row`, `console_data`, `results_view` (grouping, filtering, behind,
  on course, public/hidden), `slip_view`, `runner_analysis`, public JSON.
- **`settings_schema.py` / `config.py`** — every setting declared once; lookup
  event -> computer -> env -> default; `settings_view` for the UI.
- **`dashboard.py`** (home screen widgets), **`season.py`** (standings + prize
  rules across event files), **`controls.py`** (control statuses + report),
  **`checklist.py`** (race day / close-out), **`search.py`** (command palette).
- **`store.py`** — the in-memory event model (courses/classes/competitors/teams
  as dicts keyed by id) + all validation and mutations, **write-through** to the
  open event file via `db`. Owns: `open_event/new_event/close_event/
  events_in_folder/events_dir/has_open_event/current_event_path`, `evaluate`
  (run the engine over the event), `create/update/delete_*`, `apply_card_read`,
  `add_radio_punch`, `coerce_card`, `find_by_card`, team CRUD + `team_results`,
  `economy_summary`, `assign_bibs`, `seed_demo` (tests only), `parse_clock`/
  `format_clock`, module globals `EVENT` (dict the templates read) + `EVENT_DATE`
  (the day wall-clock times are pinned to). `_lock` is the RLock guarding it.
- **`db.py`** — SQLite persistence for one event file: schema, `_migrate` (ALTERs
  new columns onto existing files), connect/close, `read_event_meta` (peek another
  file without disturbing the open one), per-entity save/load, online backup/
  restore. Holds the connection + its own lock. (Dead-but-present: the old
  `members` table + `all_events`/`series`/`next_event_id` funcs from the retired
  multi-event design.)
- **`results.py`** — the **pure result engine** (no Flask/DB): `build_result`
  (status OK/MP/DNS/DNF/DSQ/OOT, total time, splits; punch-start support),
  `validate_linear`, `score_points`, `calculate_splits`, `aligned_splits`
  (positional alignment that handles butterfly/repeated controls + mispunches),
  `build_splits_matrix` (per-leg rank, time-behind, best-leg, velocity),
  `rank_results`, `format_duration`/`format_split`. Also the mock roster
  (`mock_classes`) used by `seed_demo` and the `python results.py` terminal demo.
- **`runners.py`** — the shared competitor database (`runners.db`): card →
  name/club + a class-history tally → `usual_class`; `lookup`/`lookup_by_name`/
  `search`/`record`/`record_competitor`/`import_csv`. Powers entry autofill.
- **`simulator.py`** — hardware-free **download simulator**: a 12-person mock
  POOL; `simulate_one()` picks a random person, makes them an on-the-day entry if
  new, generates a run for their class's course (~15% mispunch), pushes it through
  the real read path, records to runners. Requires ≥1 class (never injects demo
  data into a real event).
- **`si_reader.py`** — SI download station: the real `SIReaderReadout` COM-port
  loop (guarded; off unless `BMEOS_READER` set) + Emit scaffold
  (`BMEOS_PUNCH_SYSTEM=emit`), multi-station (`BMEOS_READER_PORTS`), `process_card`
  (→ store + publish), `simulate` (single card), a recent-reads ring buffer.
- **`events.py`** — SSE pub/sub hub (`subscribe`/`publish`); drives live page
  refresh. (Note: different thing from event *files*.)
- **`entries.py`** — pre-event registration entries + the **start-list draw**
  (assign start times per class). `notify.py` (SMTP confirmation, scaffold),
  `payments.py` (Stripe + PayPal config + entry-fee pricing, scaffold).
- **`importers.py`** (CSV start lists), **`iofxml.py`** (IOF XML v3 course/start-
  list/entry-list parse + results export, incl. course Length/LegLength geometry),
  **`eventor.py`** (Eventor IOF EntryList import + API-fetch stub),
  **`pdf.py`** (results, splits slip, start list, bib labels).
- **`auth.py`** — optional login (off unless `BMEOS_AUTH`): users table, login
  guard, roles (operator can mutate; club is read-only). `ensure_admin` seeds an
  admin after an event opens.
- **`remote.py`** — ngrok tunnel (pyngrok) for remote entry hosting.
- **`launcher.py`** — serves the app with **waitress** + opens the browser; the
  entry point baked into the exe. **`better-meos.spec`** — PyInstaller one-file
  build. **`START.bat`** — double-click dev launcher.

### 3.3 Data model (inside one `.bmeos` file)
`events`(1 row: name, date_iso, first_start, type, reader_port, slug, …) ·
`courses`(+`controls` with sequence/points/leg_length_m; course start_mode/
start_control/length_m) · `classes`(kind individual|relay, legs, fee) ·
`teams` · `competitors`(card_number unique-per-event, start/finish,
manual_status, bib, hired, team_id, leg) + `punches`(code, time, station_id) ·
`entries` · `users`. Results are **always computed**, never stored.
`runners.db` (separate): `runners`(card→name/club) + `run_history`(card,
class_name, count).

### 3.4 Frontend
- `templates/base.html` — operator shell (sidebar nav + topbar). Pages extend it.
- `static/style.css` — all styling (CSS variables; system sans-serif).
- `static/editor.js` — the competitor (centered **modal**) + class/course
  (modal) editors: CRUD via the JSON API, live result preview, **row-click opens
  the editor** (Edit button kept, clicking outside cancels), card→runner
  autofill, relay team/leg picker.
- `static/app.js` — splits expand/collapse + table filters.
- `static/live.js` — SSE subscriber; reloads read-only pages on change (or calls
  `window.BMSoftRefresh` when a page defines it; holds while
  `body.dataset.editing` / `printing` is set).
- `static/settings.js` (settings rows + gear drawer), `static/dashboard.js`
  (customise mode), `static/livescreen.js` (paging + in-place refresh),
  `static/printing.js` (hidden-frame slip printing), `static/palette.js`
  (Ctrl+K).
- `templates/start.html` (standalone event-selection page) + `setup.html`
  (per-event hub). `templates/entry.html` — the ported OWA PayPal PWA (now
  sans-serif; no-DB entry; results tab expands splits). `slip.html` (printable
  splits, `?print=1` auto-prints). `live.html`/`public.html` (standalone).

### 3.5 Key flows
- **Boot:** no event open → `/start`. Create/open → `/setup` → operate.
- **Download:** Simulate button (or real reader) → `process_card` →
  `apply_card_read` → recompute results → SSE → live pages refresh; optional
  auto-print of the splits slip.
- **Entry (remote):** open event → Start remote (ngrok) → share the URL → people
  enter on phones (`/enter`); results tab shows live standings + splits.

---

## 4. Everything built this session (chronological)

### Effort 1 — implement the full `order.txt` feature set (committed `62695c6`)
Seven phases, each tested + code-reviewed:
1. **Foundation:** SQLite persistence, real SI reader (guarded) + simulator,
   card→competitor lookup, backup/restore, SSE hub.
2. **Splits + live:** the positional splits matrix (leg rank, time-behind,
   best-leg), printable slip, SSE live results + projector `/live`.
3. **Import/export:** IOF XML course/results, CSV import, public results URL,
   PDF, club archive.
4. **Registration:** entry form, entries DB + dedupe, start-list draw; Stripe +
   email scaffolds.
5. **Multi-event:** events table, series points, competitor profiles (later
   retired — see Effort 3).
6. **AI layer:** analytics + Anthropic scaffold (later removed — see Effort 2).
7. **Ops/auth:** dashboard, optional login + roles, multi-station, offline-sync
   scaffold.
   - **Critical bugs fixed:** `INSERT OR REPLACE` on the events row cascade-
     deleted children (data loss on every restart) → switched to UPSERT;
     forgeable session secret with auth on → fail-closed random key.

### Effort 2 — remove AI, port PayPal entry page, MEOS-parity (committed `5e11256`)
- **A — remove AI:** deleted `ai.py`/`analytics.py` + routes/UI; stripped the AI
  section from `order.txt`; dropped `anthropic`. Styled previously-default form
  controls.
- **B — entry page:** added a members database + **ported the MeOS-Newest
  `entry.html` PayPal PWA** into Flask; secrets via env (never committed); XSS-
  safe config injection; hardened `/log-entries`.
- **C — MEOS-parity:** relay/team events, free/punch start, **course geometry**
  (leg lengths → min/km velocity), hire cards + class fees + `/economy`, speaker
  view, bib numbers + start-list/bib-label PDFs. DB migration upgrades files in
  place.
- **D — scaffolds:** Emit decode path, **live radio/online controls**
  (`/api/radio/punch`), Eventor EntryList import.

### Effort 3 — MeOS-style workflow + UI overhaul (UNCOMMITTED — current work)
Five phases, all done + tested (99 tests). Decisions confirmed with the user:
file-per-event; ngrok; PyInstaller exe; row-click-to-edit + outside-click-cancel;
competitor editor → centered modal; simulate = random mock pool; runner DB learns
usual class; print button + auto-print toggle; new events start empty.
1. **File-per-event + start/setup:** `*.bmeos` files in a folder; `/start`
   (open/create with name/date/first-start/type + import entries); `/setup` hub;
   boot guard. **Retired** the in-DB event switcher + series/profile pages
   (deleted those templates; the store/db series functions are now dead).
   Review fixes: validate-before-connect in `open_event` (no writing to a foreign
   file on a failed open); `ensure_admin` off import; PWA `/sw.js`/`/manifest.json`
   exempt from the guard.
2. **Competitor DB + entry/download:** `runners.py` (learns usual class);
   autofill in the editor (`/api/runners/lookup`); the **mock-pool simulate**
   button (fixes the empty-event 404; requires ≥1 class — never injects demo
   data); per-download Print + **auto-print toggle** (keyed id@finish); `/slip?
   print=1`; "still out" count; entry.html **no-DB entry** + **results-tab
   splits**. Migrated the old `members` module → runners (deleted `members.py`).
3. **UI overhaul:** competitor editor → **centered modal**; **row-click-to-edit**
   on classes + course cards; **all fonts sans-serif** (entry.html DM Serif →
   DM Sans); relay **team member assignment** (`/api/teams?class_id=` + Team/Leg
   fields in the editor).
4. **Packaging + remote:** `launcher.py` (waitress + open browser); `better-
   meos.spec` → `dist/better-meos.exe` (one file, verified it serves);
   `START.bat`; `remote.py` (pyngrok) + `/api/remote/start|stop` + Remote card on
   Setup.
5. **Review + finalize:** all 19 request points verified by a full-flow smoke.

### Effort 4 — padding fix (UNCOMMITTED)
Loose `.tool-form`/`.tool-note`/`.tool-links` blocks sat flush against panel
edges (panels have no padding). Added centralized CSS so they get the same side
padding as `.panel-body`.

---

## 5. Routes reference (operator unless noted)
- **Event lifecycle:** `GET /start`, `GET /setup`, `POST /api/events/new`
  (multipart: name/date/first_start/type + optional entries file),
  `POST /api/events/open` ({path}), `POST /api/events/close`.
- **Operate:** `GET /` (overview), `/competitors`, `/classes`, `/courses`,
  `/download`, `/results`, `/splits`, `/live`, `/teams`, `/speaker`, `/economy`,
  `/clubs`, `/tools`. `GET /slip/<id>[?print=1]`, `GET /slip/<id>.pdf`.
- **Reader:** `POST /api/reader/simulate` (no body = random pool download; body =
  one explicit card). `POST /api/radio/punch`.
- **CRUD APIs:** `/api/competitors`, `/api/classes`, `/api/courses`, `/api/teams`
  (+ `GET /api/teams?class_id=`), `/api/entries` (+ `/draw`, `/<id>/paid`),
  `/api/preview`, `/api/runners/lookup?card=`, `/api/bibs/assign`.
- **Import/export:** `/api/import/{courses,startlist,members,eventor}`,
  `/export/{results.xml,results.pdf,startlist.pdf,bibs.pdf}`, `/api/backup`,
  `/api/restore`, `/api/sync/{export,import}`.
- **Live + remote:** `GET /api/stream` (SSE), `POST /api/remote/{start,stop}`.
- **Public (no login even when auth on):** `GET /enter` (the PWA), `/get-classes`,
  `/get-result-classes`, `/get-results`, `/search-competitors`,
  `/lookup-competitor`, `/check-entered`, `/api/online-entry/{order,capture}`,
  `/manifest.json`, `/sw.js`, `/public/<slug>`, `/api/stream`, `/api/version`,
  `/login`, `/logout`.
- **Online payments (operator):** `POST /api/orders/<id>/reconcile`,
  `POST /api/orders/<id>/refunded`. **Unmatched reads:**
  `POST /api/card-reads/<id>/assign`, `DELETE /api/card-reads/<id>`.
- **Auth:** `GET/POST /login`, `GET /logout`.
- **Admin unlock + Settings:** `GET/POST /unlock`, `GET /lock`, `GET /config`
  (`?target=`, `?q=`), `GET/POST /api/settings`, `GET/POST /api/config` (old
  shape). (Admin surface; `/unlock` is exempt from the gate.)
- **Added 2026-09-27:** `/controls` + `POST /api/controls/<code>`, `/prizes`,
  `/season`, `GET /api/search`, `POST /api/dashboard` (+ `/reset`, `/notes`),
  `POST /api/card-reads/<id>/enter` (quick entry), `POST /api/competitors/<id>/
  payment`, `/export/invoices.pdf[?club=]`, `POST /api/close-out/remaining`,
  public `/public/<slug>/runner/<id>` and `/public/<slug>/results.json`,
  `/favicon.ico`.

---

## 6. Environment variables (complete)
- `BMEOS_EVENTS_DIR` — events folder (default `./events`).
- `BMEOS_RUNNERS_DB` — shared competitor DB path (default `runners.db`).
- `BMEOS_PORT` — admin console port for launcher/exe (default `8799`).
- `BMEOS_PUBLIC_PORT` — public results+entry port; what ngrok tunnels (default `8800`).
- `BMEOS_CONFIG` — path to the Settings `config.json` (default `./config.json`).
  Values saved there override the matching env vars below (env stays a fallback).
- `BMEOS_DB` — legacy single-DB path (only `db.DEFAULT_PATH` fallback; unused in
  the file-per-event flow).
- **SI reader:** `BMEOS_READER` (truthy = enable real reader; now a Settings
  field), `BMEOS_READER_PORT` (default COM5), `BMEOS_READER_PORTS` (Settings field)
  (`COM5:finish,COM6:start` multi-station), `BMEOS_PUNCH_SYSTEM` (`sportident`|
  `emit`).
- **Remote:** `NGROK_AUTHTOKEN` (your ngrok token), `NGROK_DOMAIN` (reserved
  static domain for a stable URL).
- **Auth:** `BMEOS_AUTH` (enable login), `BMEOS_SECRET` (session key; if unset a
  random one is generated and kept in config.json), `BMEOS_ADMIN_USER` /
  `BMEOS_ADMIN_PASS` (seed first operator).
- **Network:** `BMEOS_ADMIN_LAN` (console on the LAN; needs an admin password),
  `BMEOS_STATION_TOKEN` (secondary stations / radio controls), `BMEOS_PRIMARY`
  (on a secondary: the primary's URL).
- **Payments (entry page):** `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` (required
  for paid online entry), `PAYPAL_SANDBOX` (default
  sandbox), `BMEOS_CURRENCY`, `BMEOS_FEE_SENIOR/JUNIOR/CONCESSION`,
  `BMEOS_FAMILY_CAP`, `STRIPE_SECRET_KEY` (generic flow), `BMEOS_ENTRY_FEE_CENTS`.
- **Email:** `BMEOS_SMTP_HOST/PORT/USER/PASS/FROM`.
- **Entry page misc:** `BMEOS_CLUBS` (override club dropdown), `BMEOS_ENTRY_CLOSE`.
- **AI:** removed (no longer used).
- **Secrets are never committed.** The MeOS-Newest `config.json` (a real PayPal
  client id + Gmail app password) is gitignored; `config.json` is in `.gitignore`.

---

## 7. Test suite (`tests/`, 256 passing)
`conftest.py` points the events folder + runners DB at temp paths, creates +
opens a temp event, and calls `store.seed_demo()` (mock roster: M21A + Score-O,
Test Runner card 8635918, etc.) so data-dependent tests work. Files:
`test_results` (engine), `test_persistence` (file persistence + restart +
backup/restore), `test_api` (routes), `test_import_export`, `test_entries`
(registration/draw), `test_entry` (runner DB + entry page), `test_workflow`
(runners/simulator/autofill/print/teams), `test_meos_features` (relay/geometry/
economy/bibs/punch-start), `test_integrations` (Emit/radio/Eventor), `test_ops`
(auth/multi-station/sync), `test_start` (file-per-event lifecycle + guard),
`test_packaging` (launcher + remote), plus (2026-09-27) `test_settings`,
`test_dashboard`, `test_display`, `test_readout_desk`, `test_season`,
`test_controls`, `test_class_options`, `test_economy`, `test_checklist`.
Run: `venv\Scripts\python -m pytest -q`. Tests that need their own data
create it and clean up (see the `scratch` fixtures); don't assert on the
shared seeded runners' exact places, other tests add runners to M21A.
Note: tests share one process/store, so several assert membership rather than
exact counts; tests that switch/close the event restore it.

---

## 8. Decisions + rationale
- **Flask + SSE, not FastAPI + WebSockets** (order.txt asked FastAPI) — keep the
  working stack; SSE is enough for one-way live updates.
- **One file per event** (over one DB + picker) — matches "open events in a
  filepath", portable/movable like MeOS files.
- **Runner DB separate from event files** — it must persist across events.
- **Simulator never seeds a real event** — requires ≥1 class instead of injecting
  a demo course/class (avoids polluting real events).
- **Outside-click cancels** (not save) — user chose standard behaviour; row-click
  opens the editor.
- **ngrok** for remote (the club's prior tool) + **PyInstaller** one-file exe.
- **Results always computed**, never stored — single source of truth.

---

## 9. Known limitations / deferred
- **Seasons** scan the events folder and match runners by name (case and
  spacing ignored); two different people with the same name merge.
- **SSE under waitress** can buffer on some setups; the dev server (`python
  app.py`) is unaffected. If the live screen lags in the exe, this is why.
- **Dead code (functions removed 2026-09-26; tables kept):** the old `members` table + `db.all_events/series/next_event_id` +
  `store.evaluate_event` are present but unused — safe to remove later.
- **Score points are not in the IOF results XML** (no valid v3 home; they're in
  HTML/PDF).
- **Hardware/external untested here:** real SI/Emit readers, live radio controls,
  Eventor API, Stripe/PayPal live capture, SMTP, ngrok — all behind config; code
  paths exist with safe fallbacks.
- The **Setup hub** is best-effort, pending the **OWA guide**.

---

## 10. Pending / next steps
1. Try it at a real event: SI reader on the finish PC, auto-print with silent
   printing, the live screen on a projector, the start clock with sound.
2. Hire cards on the online entry form (entries with no card number yet).
3. Native .meos import (today: export IOF XML from MeOS and import that).
4. A list designer (custom result / start list layouts) and translations.
5. Live runs against PayPal sandbox and a real Eventor.
6. Each change: run `pytest`, check pages in a browser, one commit per feature.

---

## 11. Project conventions (from CLAUDE.md)
Long-term project — prioritise correctness + readability over cleverness.
Git: commit and push straight to main; a separate branch only for a feature
that might break something. New settings go in `settings_schema.py` (with
`pages=` for the gear menus) and are read with `config.get`; never put
secrets in event scope.
Timestamps are `datetime` objects, not seconds-since-midnight. The operator knows
orienteering; skip basic explanations. Reuse the engine/store adapters; match
existing template/JS idioms. Keep the mock/simulated path working hardware-free.
