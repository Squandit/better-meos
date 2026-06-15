# Punchcard — running a full event

A practical, start-to-finish walkthrough for operating an event, including a
real SportIdent reader. The same steps live in the app: click **Guide** (top
right of any page) for the step sidebar, or open **/guide**.

---

## 0. One-time setup (per computer)

1. **Get the code + dependencies** (once):
   ```
   python -m venv venv
   venv\Scripts\python -m pip install -r requirements.txt
   ```
2. **Run it:** double-click **`START.bat`**. It opens your browser at the event
   selection page. Admin console is on port 80 (`http://127.0.0.1/`).
3. **(Optional) Friendly name:** double-click **`setup-punchcard-name.bat`** once
   (approve the admin prompt) to use `http://punchcard/`.
4. **(Optional) Lock the console:** Settings → set an **Admin password**. It then
   asks for the password once per session (survives refreshes, re-asks after a day).
5. **(Optional) Fill in Settings:** PayPal, ngrok, email, fees, Eventor key/URL —
   all saved to `config.json`. Anything left blank falls back to environment vars.

> Two ports: the **admin console** (editing) is one port; the **public entry +
> results** page is another (default 8800), so sharing that URL never exposes
> event editing. On the same WiFi, others reach it at `http://<your-PC-name>:8800/`.

---

## 1. Before the event

**Create the event** — from the start page, *Create a new event* (name, date,
first start, type) or open an existing `.bmeos` file. You land on the **Setup**
hub, which shows the run-an-event checklist.

**Courses** (Courses page or import):
- Add each course's control sequence in order. For score events, give each control
  its points and set the time limit + penalty (or a custom scoring formula).
- Start mode: **clock** (timed start), **punch** (free start from a start control),
  **mass**, or **chase/handicap**.
- Forked/butterfly courses: list alternative accepted control orders under *Forked
  variants*.
- Or **Import courses** (IOF XML) on the Import/Export page.

**Classes** — create the categories (e.g. M21A, W18) and point each at its course.
Choose Individual, Relay or Patrol, and set any entry fee.

**Get competitors in**, any of:
- **Register** page — fast on-the-day entry (see §3), great for tapping cards.
- **Import start list** — CSV or IOF XML (Import/Export).
- **Eventor** — Settings → Eventor (API key, base URL, event ID), then
  *Fetch entries from Eventor* on Import/Export. New classes are auto-created
  (assign their course on the Classes page afterwards).
- **Online entry** — share the public `/enter` page (PayPal optional).

**Start list & bibs:**
- Entries → **Start-list draw**: choose order (alphabetical / random / spread
  clubs), interval, and optional reserve slots. Re-run later to place late entries.
- Import/Export → **Assign bib numbers**, then print **Start list** / **Bib labels**.

---

## 2. SI reader setup (real hardware)

1. **Plug the SportIdent USB station in.** It appears as a serial (COM) port.
2. **Which port?** You usually don't need to know — set the reader port to
   **`auto`** and Punchcard finds the SPORTident station itself (it looks for the
   Silicon Labs CP210x USB bridge). To see what's detected, open
   `/api/reader/ports` in the browser, or check the console window's log line
   ("auto-detected SI reader on COM5").
   - To force a specific port instead, set `BMEOS_READER_PORT=COM5` (or whichever).
3. **Enable the reader:** set `BMEOS_READER=1` before launching (e.g. in a copy of
   START.bat: `set BMEOS_READER=1` and `set BMEOS_READER_PORT=auto` before the
   python line). Without it, Punchcard runs on **simulated** reads — perfect for
   practice with no hardware.
4. **Multiple stations** (e.g. a separate start unit): `BMEOS_READER_PORTS=COM5:finish,COM6:start`.
5. **Test it:** open **Register**, tap a card — the SI number should fill in within
   ~1 second. If it does, the reader + port are working.

---

## 3. On the day — registration

Open **Register** (sidebar or Setup tile):
- Type a name, pick the class, add club — **or tap the SI card on the reader** and
  the SI number fills automatically. If the card has been seen at a past event, the
  name/club/usual class autofill too (from the shared runner database).
- Tick **Hire card** for rentals.
- **Register competitor** saves them and clears the form (keeping the class) for the
  next person. The "Just registered" list shows the session's entries.

Typing an SI number (instead of tapping) also autofills the known runner.

---

## 4. During the event

**Download finishes** — competitors download their cards at the finish:
- Real reader: just insert the card; the finish, splits and result compute and
  appear live. Optional auto-print of the splits slip (Download page).
- No hardware: **Overview → Simulate card read** pushes a random mock download.
- Unknown card? With auto-create on (`BMEOS_AUTO_CREATE=1`) it builds a course/class
  from the punches; otherwise it's flagged as unmatched — create the competitor and
  re-download.

**Watch results:**
- **Results** (standings) and **Splits** (leg-by-leg matrix: leg rank, time behind,
  best leg, velocity).
- **Live screen** — projector leaderboard (auto-refreshes).
- **Speaker** — who's still out, recent finishes.
- **Radio/online controls** stream intermediate splits live if configured.

**Safety:** Import/Export → **Still out — PDF** lists who started but hasn't
downloaded.

**Fixing things** (Competitors → click a runner):
- Force a status (DNS/DNF/DSQ/OK), edit start/finish or punches.
- **Time adjustment / credit**, mark **not competing** or a **vacant** slot.

---

## 5. After the event

- **Prizes** — per course, the top finisher who hasn't already won this season is
  recommended; click **Award**. Reset/relabel the season any time.
- **Series** — cumulative season points across all events in the folder; click a
  name for their full profile.
- **Export / publish** (Import/Export):
  - **Results** — IOF XML (also feeds SplitsBrowser/WinSplits) and PDF.
  - **Prize giving** and **Still out** PDFs; **Splits CSV**.
  - **Upload results to Eventor** (needs the API key/URL/event ID in Settings).
  - **Public results URL** — a permanent read-only page; share it (or host it via
    ngrok: Setup → Start remote entry).
- **Back up** — Import/Export → **Backup database** (one `.bmeos` snapshot file).
  The event file is also saved continuously as you work.

---

## 6. Multi-day / series

- Each day is its own event file. **Stages** page: tick the day files to see
  combined overall standings, or set **chase/handicap starts** on the next day
  from the accumulated deficit.
- **Series** aggregates points across every event in the folder automatically.

---

## 7. Simulating an event with a real reader + cards (your test plan)

1. `START.bat` with `BMEOS_READER=1` and `BMEOS_READER_PORT=auto`.
2. Create a test event; add one or two courses + classes.
3. **Register** a handful of people — tap each card to grab its number, pick a
   class, save. (Tapping confirms the reader + port work.)
4. (Optional) Draw start times and assign bibs.
5. "Run": for each card, **download** it at the reader. Watch Results/Splits/Live
   update. Try a deliberate mispunch (skip a control) to see MP handling.
6. Edit something (force a DSQ, add a time adjustment) and confirm ranks update.
7. Award a prize; export results (XML/PDF) and open the public results page.
8. Back up the event file.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Reader not reading | Confirm `BMEOS_READER=1`; check the console log for the port line; open `/api/reader/ports` to see detected ports; try `BMEOS_READER_PORT=COM#` explicitly. |
| Wrong/again COM port | Use `auto`, or set the exact `COMx`. Unplug/replug changes the port on some PCs. |
| `punchcard/` won't load | Run `setup-punchcard-name.bat` (admin) and make sure the server is running; `http://127.0.0.1/` should work regardless. |
| "Unknown card" on download | The competitor isn't registered to that SI number — register them (or turn on auto-create), then re-download. |
| Card tap doesn't autofill on Register | Reader must be enabled (`BMEOS_READER=1`); the page polls once a second — give it a moment. |
| Results look wrong after an edit | Results always recompute from punches; reload. Check the competitor's status isn't a manual override. |
| Need to undo a bad import/restore | Import/Export → **Restore** a backup `.bmeos`. |

> Reminder: hardware, live PayPal/Eventor/email are real but can only be proven at
> a real event with real credentials/devices — which is exactly what these
> practice runs are for.
