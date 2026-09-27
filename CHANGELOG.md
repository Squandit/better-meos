# Changelog

Every release is tagged `vX.Y.Z` on main and built by GitHub Actions: the
installer (`better-meos-setup.exe`) and the bare exe are attached to the
release. The number lives in `version.py`.

- **Major** (2.0.0): operators have to relearn something, or event files
  change so older versions can't open them.
- **Minor** (1.1.0): new features.
- **Patch** (1.0.1): fixes only.

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
