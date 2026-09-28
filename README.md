# Control

Orienteering event software for club events and championships: entries,
start draw, SI card download, live results, splits, relays, score-O,
multi-stage and season standings. It runs on one Windows laptop at the
finish, and phones on the same WiFi get live results and online entry.
Built as a friendlier alternative to MeOS.

(Until version 1.3.0 it was called better-meos, which is still the repo name.)

## Install

Download `control-orienteering-setup.exe` from the
[latest release](https://github.com/Squandit/better-meos/releases/latest) and
run it. No admin rights needed. It installs to
`%LOCALAPPDATA%\Programs\control-orienteering` and keeps events, settings and
the runner database in `C:\Users\<you>\control-orienteering`, which
uninstalling never touches. An older better-meos install is upgraded in place
and its folder moves across the first time Control starts.

The app opens in your browser. The operator console is on port 8799 (this
laptop only, unless you allow other computers in Settings) and the public
results and entry pages are on port 8800.

For a USB stick that moves between laptops, put the bare
`control-orienteering.exe` on it with an empty `portable.txt` next to it, and
everything stays on the stick.

## Hardware

A SportIdent BSM7/BSM8 station on USB for downloads (pick the COM port in
Settings), and any Windows printer for split slips, which print straight away
with no dialog. A second download desk can send its reads to the main
computer over the network.

## Developing

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
python launcher.py               # or START.bat
```

No SI hardware is needed: the Download page can simulate card reads.

Tests:

```bash
python -m pytest -q              # unit and route tests
python scripts/stress/run.py     # whole events against real app processes
```

The stress suite runs eight scenarios (a big sprint, score/relay/patrol/night
formats, multi-stage and seasons, second desks, security, fuzzing, a
2,000-runner load test and a browser pass) and checks every result against an
independent calculation. CI runs both on Windows for every push, then builds
the exe and the installer (`control-orienteering.spec`,
`installer/control-orienteering.iss`).

Releases: bump `version.py`, add a `CHANGELOG.md` entry, then push a `vX.Y.Z`
tag or run the "Build Windows exe" workflow with "release" ticked.

`HANDOFF.md` is the full record of how everything works and what changed
recently.
