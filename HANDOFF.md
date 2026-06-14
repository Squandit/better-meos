# better-meos — build handoff

Rolling status for the "implement everything in `order.txt`" effort. Plan file:
`C:\Users\quinn\.claude\plans\can-you-please-add-hazy-stallman.md`.

## How to run / test
- venv (Python 3.12), Flask app: `venv\Scripts\python app.py` then open http://127.0.0.1:5000
- Tests: `venv\Scripts\python -m pytest -q`  (uses a throwaway temp DB; never touches `meos.db`)
- Data persists to `meos.db` (gitignored). Delete it to reseed from mock data.
- SI reader is OFF by default; set env `BMEOS_READER=1` (+ `BMEOS_READER_PORT`) to enable real COM-port reads. Otherwise use `POST /api/reader/simulate`.

## Decisions locked in
- Stay on **Flask**; live updates via **SSE** (`/api/stream`), not FastAPI/WebSockets.
- Scope = **everything** in order.txt, phased.
- Stripe / email / AI: **scaffold behind env config**, no live keys.
- SI reader: real COM5 path (guarded) **+** simulator.

## DONE — Phase 1 (persistence + reader + card lookup)
- `db.py` — SQLite persistence (events, courses+controls, classes, competitors+punches). Online-backup API for backup/restore. Results stay computed, not stored.
- `store.py` — now **write-through** to `db.py`; loads on boot, seeds a fresh DB from mock data. Added `coerce_card`, `find_by_card`, `apply_card_read`, `reload`, `restore`, `active_event_id`. Per-kind id counters resume from global table max (multi-event-safe).
- `events.py` — SSE pub/sub hub (`subscribe`/`publish`).
- `si_reader.py` — real `SIReaderReadout` loop (daemon thread, off unless `reader_enabled`) + `simulate()` for hardware-free reads; both go through `process_card` → store + publish.
- `app.py` — removed dead `seed_db`/commented reader block. New routes: `/api/stream` (SSE), `/api/reader/simulate`, `/api/backup`, `/api/restore`. Mutations publish live events. `threaded=True`.
- Tests: `tests/test_results.py`, `tests/test_persistence.py`, `tests/test_api.py` (23 passing). `requirements.txt`, `pytest.ini` added.
- Code review done; fixed: consistent-snapshot backup, safe in-place restore (no Windows file-lock), reuse `_coerce_punches` in the simulate payload (also fixed a 500-on-malformed-punch).

### Known deferred (from Phase 1 review)
- **station_id** is recorded on card-read punches but `_coerce_punches` (editor path) drops it, so editing a competitor's punches nulls station_id. Fine for now (station_id isn't surfaced until Phase 7 multi-station) — fix when wiring multi-station.

## DONE — Phase 2 (splits matrix + slip + live results)
- `results.py`: `format_split` (compact M:SS), `aligned_splits(start,punches,finish,required)` (tolerant positional alignment — handles butterfly/repeated controls AND missed-middle controls), `build_splits_matrix(results,controls)` (leg/cum time, leg & split rank, time-behind-leader, best-leg flag). `build_result` now attaches `result["punches"]` so the matrix can align.
- `templates/splits.html` (linear → matrix; score → per-runner rows), `slip.html` (standalone printable finish slip, `@media print`), `live.html` (projector leaderboard, dark, auto-updating).
- `app.py`: routes `/splits`, `/slip/<comp_id>`, `/live`; `_splits_data()`.
- `base.html`: Splits + Live-screen nav, `data-page` on body, includes `live.js`.
- `static/live.js`: EventSource on `/api/stream`, throttled reload of read-only pages only (editing pages excluded via LIVE_PAGES). `static/style.css`: matrix/slip/live styles.
- Late-start handling: covered by the existing editable start field in the competitor drawer (edit recomputes).
- Tests: matrix ranks/best-leg, missing control, **butterfly repeated control**, **missed-middle recovery**; slip + new pages render. 30 passing.
- Review done; fixed the by-code splits-collapse bug (the important one). Left as acceptable: best-leg flag highlights all co-fastest (correct); station_id-on-edit deferred to Phase 7.

## DONE — Phase 3 (import/export/publishing)
- `iofxml.py`: IOF XML v3 `parse_courses`, `parse_startlist`, `export_results` (schema-correct Result element order; no non-standard Score element; `_require_v3` rejects wrong-version files clearly).
- `importers.py`: forgiving CSV start-list parse + `import_competitors` (resolves class by name, collects skipped rows with reasons).
- `pdf.py` (reportlab): `splits_slip_pdf`, `class_results_pdf`; names XML-escaped (no crash on `&`/`<`).
- `app.py` routes: `/tools`, `/clubs`, `/public/<slug>`, `/api/import/courses`, `/api/import/startlist`, `/export/results.xml`, `/export/results.pdf`, `/slip/<id>.pdf`. `_club_archive`, `_uploaded_text`.
- Templates: `tools.html` (upload UI + inline fetch JS), `clubs.html`, `public.html`. Nav: Clubs + Import/Export. CSS for both.
- **Duplicate SI card now rejected at store level** (`_check_card_unique` in create + update) — guards editor and import.
- Tests: IOF parse/export (+ element order, wrong-version), CSV + IOF import, PDF special chars, route checks. 46 passing.
- Deferred (noted): score points aren't in the IOF results XML (no valid v3 home — they're in HTML/PDF); missing-Class-Name startlist rows skip with a slightly opaque reason.

## DONE — Phase 4 (registration)
- db `entries` table (UNIQUE(event_id, card_number)). `entries.py`: `create` (validates, dedupes by card/name AND against existing competitors, catches the UNIQUE race), `list_entries`, `delete`, `mark_paid`, `draw_startlist` (per-class alphabetical, seeds each class after its last assigned slot so late re-draws don't collide).
- `notify.py` (SMTP confirmation email scaffold, logged no-op when unset), `payments.py` (Stripe checkout scaffold, no-op when unset/zero fee).
- `app.py` routes: `/enter` (public), `/entries` (operator), `/api/entries` POST, `/api/entries/<id>` DELETE, `/api/entries/<id>/paid` POST, `/api/entries/draw` POST.
- Templates: `enter.html` (public form), `entries.html` (operator: draw + paid/delete). Entries nav link. CSS.
- Tests: dedupe, valid-class, draw ordering/start times, **late-redraw no collision**, **reject card already on a competitor**, routes. 52 passing.

## DONE — Phase 5 (multi-event)
- `events` table (multiple events), active-event pointer, `store.set_active_event` (atomic load-then-swap), event switcher in the top bar. `series` table.
- `store`: `_evaluate_model` / `evaluate_event(eid)` (evaluate any event, loading non-active ones from DB without touching live state); `_apply_event` syncs EVENT + the `EVENT_DATE` that `parse_clock` pins times to; `create_event/list_events`, `create_series/set_event_series/list_series`, `series_standings` (person = name+club, 100/−5 points), `competitor_profile` (results across events).
- `app.py` routes: `/events`, `/series/<id>`, `/profile?card=|name=`, `/api/events`, `/api/events/active`, `/api/events/<id>/series`, `/api/series`. `format_secs` Jinja filter. Templates: `events.html`, `series.html`, `profile.html`.
- **CRITICAL fix**: `db.save_event`/`save_series` were `INSERT OR REPLACE`; with `ON DELETE CASCADE` FKs that wiped an event's children on every event save — including on each app restart (`_init` re-saves the active event). Now UPSERT. Test `test_restart_via_init_preserves_data` guards it.
- Other review fixes: `coerce_card` holds `_lock` (EVENT_DATE race), atomic event switch, series identity by name+club (cards change per event), DB `UNIQUE(event_id, card_number)` on competitors.
- Known low-risk (single-operator): `next_event_id`/`next_series_id` are MAX+1 (race → UPSERT overwrite); `inject_event` queries events per render.
- 57 tests passing.

## DONE — Phase 6 (AI layer)
- `analytics.py` (pure, tested): `class_leg_stats`, `competitor_legs` (per-leg vs class avg + weak-leg flag, div-by-zero guarded), `split_trends`, `shared_legs` (legs shared between courses), `course_checks` (rule-based course-setting validations).
- `ai.py` (Anthropic scaffold, `ANTHROPIC_API_KEY`-gated, model `BMEOS_AI_MODEL` default claude-sonnet-4-6): `training_advice`, `course_review`; lazy import + rule-based fallback (and the rule output is the AI prompt context).
- `app.py`: `/api/profile/advice`, `/api/courses/<id>/review`, `/api/classes/<id>/legs`. Surfaces: Review button per course (courses.html), Coaching panel on profile.html.
- 64 tests passing. Phase 6 review returned **no issues**.

## DONE — Phase 7 (ops/auth/polish)
- Operator dashboard (`overview.html`): reader status pill, "awaiting download" count, **Simulate card read** button, live **Recent downloads** list (server-side ring buffer in `si_reader.recent_reads`, refreshed by the SSE auto-reload).
- Club accounts: `users` table + `auth.py`. **Optional** (gated only when `BMEOS_AUTH` set; off in tests/default). Login/logout, Werkzeug password hashing, `before_request` guard. `login.html`, top-bar sign-out.
- Multi-station: per-station reader threads (`si_reader.start_all`, `BMEOS_READER_PORTS="COM5:finish,COM6:start"`), `station_id` recorded on punches + shown on the dashboard. Per-station stop events.
- Offline sync: `/api/sync/export` + `/api/sync/import` (= backup/restore) as the documented seam; full multi-node sync intentionally out of scope.
- Security review fixes: **fail-closed session secret** (random per-process key when `BMEOS_AUTH` on and `BMEOS_SECRET` unset — was a forgeable-cookie auth bypass), **operator-role enforcement** for mutations (club role read-only), **public SSE feed no longer leaks names/card numbers**, per-station stop + start lock.
- 69 tests passing; full-app page sweep green.

## ALL PHASES COMPLETE
Everything in `order.txt` is implemented. The app runs on Flask + SQLite, seeded from mock data, hardware-free via the simulator.

### Enabling the scaffolded/optional features (env vars)
- Real SI reader: `BMEOS_READER=1` (+ `BMEOS_READER_PORT=COM5`, or `BMEOS_READER_PORTS=COM5:finish,COM6:start`).
- Auth: `BMEOS_AUTH=1` + `BMEOS_SECRET=<random>` + `BMEOS_ADMIN_USER`/`BMEOS_ADMIN_PASS` (seeds first operator).
- Email: `BMEOS_SMTP_HOST` (+ PORT/USER/PASS/FROM).
- Payments: `STRIPE_SECRET_KEY` + `BMEOS_ENTRY_FEE_CENTS` (+ `BMEOS_CURRENCY`).
- AI: `ANTHROPIC_API_KEY` (+ `BMEOS_AI_MODEL`, default claude-sonnet-4-6).
- DB path: `BMEOS_DB` (default `meos.db`).

### Known low-priority items (documented, not blocking)
- `next_event_id`/`next_series_id` are MAX+1 (race under truly concurrent creates → UPSERT overwrite). Fine for a single-operator console.
- `inject_event` runs a small events query per render.
- `auth` `/public/` is a path-prefix (only `/public/<slug>` exists today); consider endpoint-name gating if more `/public/*` routes are added.
- `station_id` is dropped if a card-read competitor's punches are later re-entered via the editor (editor punches carry no station). Only matters with multi-station + manual edits.
- Series/profile person identity is name+club (cards change per event); same-name-same-club distinct people would merge (rare).

### Test layout
`tests/`: test_results, test_persistence, test_api, test_import_export, test_entries, test_multievent, test_analytics, test_ops. Run `venv\Scripts\python -m pytest -q`.

## Notes
- Nothing committed yet (user commits when ready). Lots of pre-existing untracked files too (store.py, results.py, templates, static) from the initial state.
