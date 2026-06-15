"""
Shared test setup.

Events are now one SQLite file per event (opened from the start page); the app no
longer seeds on import. So here we point the events folder at a throwaway temp
directory, create + open a test event, and populate it with the mock roster via
``store.seed_demo()`` so the data-dependent tests keep working.
"""

import os
import shutil
import tempfile

# Must be set before `store`/`app`/`db`/`runners` import anything.
_events_dir = tempfile.mkdtemp(prefix="bmeos_events_")
os.environ["BMEOS_EVENTS_DIR"] = _events_dir
_runners_fd, _runners_path = tempfile.mkstemp(prefix="bmeos_runners_", suffix=".db")
os.close(_runners_fd)
os.environ["BMEOS_RUNNERS_DB"] = _runners_path
# Keep the Settings dashboard / config.json out of the repo during tests.
_config_fd, _config_path = tempfile.mkstemp(prefix="bmeos_config_", suffix=".json")
os.close(_config_fd)
os.remove(_config_path)  # config.py treats a missing file as empty
os.environ["BMEOS_CONFIG"] = _config_path
# Season prize ledger in a throwaway DB (never the repo's prizes.db).
_prizes_fd, _prizes_path = tempfile.mkstemp(prefix="bmeos_prizes_", suffix=".db")
os.close(_prizes_fd)
os.environ["BMEOS_PRIZES_DB"] = _prizes_path

import store  # noqa: E402

store.new_event({"name": "Test Event", "date": "2026-05-17",
                 "first_start": "09:00:00", "type": "linear"})
store.seed_demo()


def pytest_sessionfinish(session, exitstatus):
    try:
        store.close_event()
    except Exception:
        pass
    shutil.rmtree(_events_dir, ignore_errors=True)
    for path in (_runners_path, _config_path, _prizes_path):
        try:
            os.remove(path)
        except OSError:
            pass
