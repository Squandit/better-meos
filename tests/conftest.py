"""
Shared test setup.

The store seeds itself from the mock roster on import and writes through to the
SQLite database named by ``BMEOS_DB``. We point that at a throwaway temp file
*before* anything imports the store, so tests never touch the real ``meos.db``.
"""

import os
import tempfile

# Must be set before `store`/`app`/`db` are imported anywhere.
_fd, _path = tempfile.mkstemp(prefix="bmeos_test_", suffix=".db")
os.close(_fd)
os.environ["BMEOS_DB"] = _path


def pytest_sessionfinish(session, exitstatus):
    try:
        os.remove(_path)
    except OSError:
        pass
