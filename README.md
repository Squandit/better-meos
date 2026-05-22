# better-meos

A web-based orienteering event management system. Built as a hobby project to replace (or at least learn from) existing tools like MEOS.

> Early stages. Everything is subject to change.

## What it does (eventually)

The goal is a full event management tool covering:

- SI card readout and punch processing
- Result calculation (splits, total time, mispunch detection)
- Live leaderboard via WebSockets
- Event setup (courses, start lists, competitor entry)
- Splits display with leg rankings and time-behind-leader
- IOF XML import/export
- Multi-event series management
- AI split analysis (later)

Right now it's a Flask app with mock SI card data, a SQLite database schema, and the bones of a results page.

## Stack

- **Backend:** Python, Flask (moving to FastAPI)
- **Database:** SQLite
- **Frontend:** HTML / CSS / JS
- **Hardware:** SportIdent SI card reader via the `sportident` library (COM5 on Windows)
- **Real-time:** WebSockets (planned)

## Running it

```bash
# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate  # Windows

# Install dependencies
pip install flask sportident

# Run
python app.py
```

Then open `http://localhost:5000` in a browser.

The SI card reader is commented out by default. The app runs on mock data so you don't need hardware to develop.

## Project structure

```
better-meos/
├── app.py              # Main application
├── templates/
│   └── index.html      # Results page template
└── order.txt           # Feature roadmap
```
