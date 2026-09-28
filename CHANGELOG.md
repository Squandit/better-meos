# Changelog

Every release is tagged `vX.Y.Z` on main and built by GitHub Actions: the
installer (`better-meos-setup.exe`) and the bare exe are attached to the
release. The number lives in `version.py`.

- **Major** (2.0.0): operators have to relearn something, or event files
  change so older versions can't open them.
- **Minor** (1.1.0): new features.
- **Patch** (1.0.1): fixes only.

## 1.3.0 (2026-09-28)

better-meos is now called **Control**.

- The installer is `control-orienteering-setup.exe` and the app
  `control-orienteering.exe`. Installing it over better-meos upgrades that
  install and removes the old program folder and shortcuts.
- Events, settings and the runner database now live in
  `C:\Users\<you>\control-orienteering`. The old `better-meos` folder moves
  there by itself the first time Control starts (backups in local app data
  too). Nothing is deleted.
- Event files are now `.ctrl`. Old `.bmeos` events in the events folder are
  renamed the first time the start page lists them, and a `.bmeos` file
  anywhere else still opens. Backups are `.ctrl` too.
- A sample event: "Open a sample event" on the start page makes a club sprint
  with four courses, twelve classes and 130 entered runners, drawn and with
  bib numbers, ready for the finish. Everyone in it is made up.
- Simulate download brings in the entered runners from their real start
  times (it used to invent walk-ups), with sprint-like leg times when the
  course has leg lengths.
- The logo is a control flag.
- Sign in, Create & open, the import buttons and the export links look like
  buttons again (they had lost their background and showed as plain text).

## 1.2.0 (2026-09-28)

A stress suite now runs whole events against the app on every build, and
fixing what it found is most of this release.

- Relays: leg 1 now uses the team start; later legs on a mass-start course
  are timed from the changeover, not the gun.
- The start draw leaves relay and patrol classes alone, refuses (whole) to
  redraw classes that have finishers, and warns when classes sharing a
  course would start runners at the same minute.
- Multi-stage: a runner on a different card in a later stage is still one
  person; chase starts skip anyone who has already run.
- Score-O courses can be mass starts.
- A second download desk is now a setting ("Send card reads to another
  computer"), with its own status on the start page. Unknown cards and
  check times are handled properly there.
- Settings can be changed before any event is open.
- The public results pages show only public links.
- With logins on, a fresh install can log in.
- CSV files saved by Excel on Windows import correctly.
- Many inputs that used to cause a server error are now refused with a
  message; text fields are limited to 200 characters.
- The competitor editor's live preview works again.
- The close-out check lists everyone still out.
- The public results stay quick with a crowd of phones on them.
- One runner-database import box for CSV or IOF XML.
- Automatic backups no longer hold up card reads while they're written,
  and a backup that starts in the middle of a card read, import or draw
  waits for it instead of freezing the app.
- Pages are sent compressed: a big event's results reach phones about 15
  times smaller.
- A second desk shows its link to the main computer correctly when that
  computer has an admin password.

## 1.1.0 (2026-09-28)

- Split slips print straight to the printer with no print dialog: the app
  draws the slip itself and sends it to Windows. Auto-print works with no
  page open. New settings: Print slips, Slip printer (with a test print).
- Eventor: check the API key, pick the event, fetch entries (again for late
  entries without duplicates), load club members' SI cards, upload results.
- Relays: runners are placed against their own leg only, the results pages
  show team standings, prizes go to teams.
- Class editor: relay legs and the relay mass start are greyed out unless
  the class is a relay; patrol is gone for new classes.
- Start draw moved under More tools. Theme and other appearance settings
  apply straight away.
- Giving a stray card read to someone who already has a run now asks first.
- Saved API keys can be removed in Settings.
- The download simulator never replaces a finished run.

## 1.0.0 (2026-09-28)

The first numbered release: everything up to here.

- Event files (`.bmeos`, one per event), courses, classes, competitors,
  teams and relays, start draw, SI readout with a hardware-free simulator,
  secondary stations and radio controls.
- Results engine: linear and score courses, night events, max time, NC,
  runners still out shown as On course (never a false DNF), control
  statuses (bad / optional / no timing / alternates), forking.
- Results, splits, live projector screen, public results and runner split
  analysis, speaker, prizes with season rules, season standings,
  multi-stage and chase starts.
- Online entry with PayPal (server-priced and verified), economy with
  per-runner payments and club invoices.
- Settings for nearly everything (per event or per computer, a gear menu
  on each page), appearance (dark mode, accents), customisable home screen,
  command palette (Ctrl+K), race-day and close-out checklists, help panel
  with guided tours for people coming from MeOS.
- Windows installer; the app keeps its data in `C:\Users\<you>\better-meos`.
