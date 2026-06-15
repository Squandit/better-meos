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

### 1.2 DONE — MeOS-parity completion, season prizes, MeOS gaps (2026-06-15, PC)
Built in 7 committed/pushed phases on top of 1.1 (tests now **133 passing**):
- **Stages UI** (`/stages`): combine `.bmeos` files into multi-day standings +
  set chase/handicap starts (over the existing `stages.py`).
- **Editor fields** for already-built engine features: course start modes
  mass/chase + mass-start time + score formula + forked **variants**; **patrol**
  class kind.
- **Season prizes** (`prizes.py` + `prizes.db`, `/prizes`): one prize per course
  for the top placement, one prize per person per season, **cascades** to the
  next eligible; operator confirms via Award; season label in config; ledger
  reset. Identity = card else name+club.
- **Series + profiles** (`series.py`, `/series`, `/profile`): cross-event season
  points (config `series_points_base/step`) + per-person history, scanned from
  the events folder. Re-delivers the retired multi-event block.
- **Richer draw**: `entries.draw_startlist` gains `method`
  (alpha/random/club_spread) + `vacancy_every` reserve slots.
- **Reports/exports**: `pdf.podium_pdf` (prize giving), `pdf.still_out_pdf`,
  `/export/{podium,stillout}.pdf`, `/export/splits.csv` (results.xml already
  carries SplitTimes for SplitsBrowser).
- **Eventor upload** (`eventor.upload_results`, `/api/eventor/upload`): POSTs IOF
  ResultList, gated by `eventor_api_key`/`eventor_base_url` (untestable live).
- **Time edits**: competitor `time_adjustment`/`credit`/`not_competing`/`vacant`
  (engine + DB columns + editor). **Forked/variant courses**: a linear course may
  list alternative accepted control orders (`variants`), OK if punches match any
  — validation-level forking, not full MeOS leg-forking. **SIAC** (SI-Air+):
  scaffold in `si_reader._run` (`BMEOS_PUNCH_SYSTEM=siac`), reads via the SI
  protocol; no live test.
- **New files:** `prizes.py`, `series.py`, `templates/{stages,prizes,series,
  profile}.html`, `tests/test_{meos_parity,prizes,series,eventor}.py`. New config
  settings: `prize_season`, `series_points_*`, `eventor_*`. New DB columns
  (competitors time edits, courses `variants`) with migrations.

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
  status/label helpers, view-row shaping. The orchestration layer.
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
- `static/live.js` — SSE subscriber; reloads read-only pages on change.
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
  `/lookup-competitor`, `/check-entered`, `/submit-entry`, `/log-entries`,
  `/manifest.json`, `/sw.js`, `/public/<slug>`, `/api/stream`, `/login`, `/logout`.
- **Auth:** `GET/POST /login`, `GET /logout`.
- **Admin unlock + Settings:** `GET/POST /unlock`, `GET /lock`, `GET /config`,
  `GET/POST /api/config`. (Admin surface; `/unlock` is exempt from the gate.)

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
- **SI reader:** `BMEOS_READER` (truthy = enable real reader),
  `BMEOS_READER_PORT` (default COM5), `BMEOS_READER_PORTS`
  (`COM5:finish,COM6:start` multi-station), `BMEOS_PUNCH_SYSTEM` (`sportident`|
  `emit`).
- **Remote:** `NGROK_AUTHTOKEN` (your ngrok token), `NGROK_DOMAIN` (reserved
  static domain for a stable URL).
- **Auth:** `BMEOS_AUTH` (enable login), `BMEOS_SECRET` (session key — set it when
  auth is on), `BMEOS_ADMIN_USER` / `BMEOS_ADMIN_PASS` (seed first operator).
- **Payments (entry page):** `PAYPAL_CLIENT_ID`, `PAYPAL_SANDBOX` (default
  sandbox), `BMEOS_CURRENCY`, `BMEOS_FEE_SENIOR/JUNIOR/CONCESSION`,
  `BMEOS_FAMILY_CAP`, `STRIPE_SECRET_KEY` (generic flow), `BMEOS_ENTRY_FEE_CENTS`.
- **Email:** `BMEOS_SMTP_HOST/PORT/USER/PASS/FROM`.
- **Entry page misc:** `BMEOS_CLUBS` (override club dropdown), `BMEOS_ENTRY_CLOSE`.
- **AI:** removed (no longer used).
- **Secrets are never committed.** The MeOS-Newest `config.json` (a real PayPal
  client id + Gmail app password) is gitignored; `config.json` is in `.gitignore`.

---

## 7. Test suite (`tests/`, 99 passing)
`conftest.py` points the events folder + runners DB at temp paths, creates +
opens a temp event, and calls `store.seed_demo()` (mock roster: M21A + Score-O,
Test Runner card 8635918, etc.) so data-dependent tests work. Files:
`test_results` (engine), `test_persistence` (file persistence + restart +
backup/restore), `test_api` (routes), `test_import_export`, `test_entries`
(registration/draw), `test_entry` (runner DB + entry page), `test_workflow`
(runners/simulator/autofill/print/teams), `test_meos_features` (relay/geometry/
economy/bibs/punch-start), `test_integrations` (Emit/radio/Eventor), `test_ops`
(auth/multi-station/sync), `test_start` (file-per-event lifecycle + guard),
`test_packaging` (launcher + remote). Run: `venv\Scripts\python -m pytest -q`.
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
- **Cross-event series + competitor profiles** were retired by file-per-event;
  revisit as a cross-file feature (scan event files in the folder).
- **Auth users live in the open event file** (per-event) — a limitation; auth is
  off by default. A shared users DB (like runners.db) would fix it.
- **SSE under waitress** can buffer on some setups; the dev server (`python
  app.py`) is unaffected. If the live screen lags in the exe, this is why.
- **Dead code:** the old `members` table + `db.all_events/series/next_event_id` +
  `store.evaluate_event` are present but unused — safe to remove later.
- **Score points are not in the IOF results XML** (no valid v3 home; they're in
  HTML/PDF).
- **Hardware/external untested here:** real SI/Emit readers, live radio controls,
  Eventor API, Stripe/PayPal live capture, SMTP, ngrok — all behind config; code
  paths exist with safe fallbacks.
- The **Setup hub** is best-effort, pending the **OWA guide**.

---

## 10. Pending / next steps
1. **Commit + push** the uncommitted overhaul (Efforts 3 & 4) so it's on GitHub
   and portable. Suggested message: "MeOS-style workflow: file-per-event start
   page, runner DB + download simulator, exe + ngrok, UI overhaul".
2. Send the **OWA operator guide** → refine the Setup screen around it.
3. Provide **`NGROK_AUTHTOKEN`** (+ optional `NGROK_DOMAIN`) to make remote entry
   live; provide live **PayPal/Stripe/SMTP** creds via env to exercise those.
4. Optional polish: shared users DB (fix per-event auth); remove the dead
   multi-event/members code; a cross-file series/profile feature; verify SSE
   under waitress (or serve the exe with a streaming-friendly server).
5. Each new change: run `pytest`, and consider `/code-review` per phase (that's
   the pattern used throughout).

---

## 11. Project conventions (from CLAUDE.md)
Long-term project — prioritise correctness + readability over cleverness.
Timestamps are `datetime` objects, not seconds-since-midnight. The operator knows
orienteering; skip basic explanations. Reuse the engine/store adapters; match
existing template/JS idioms. Keep the mock/simulated path working hardware-free.
