"""The whole-event simulation (scripts/simulate_event.py) against a real app
process: its own events folder, settings and ports, slips saved as PNGs."""

import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_whole_event(tmp_path):
    admin, public = free_port(), free_port()
    env = {**os.environ, "PYTHONPATH": ROOT,
           "BMEOS_CONFIG": str(tmp_path / "config.json"),
           "BMEOS_EVENTS_DIR": str(tmp_path / "events"),
           "BMEOS_RUNNERS_DB": str(tmp_path / "runners.db"),
           "BMEOS_PORT": str(admin), "BMEOS_PUBLIC_PORT": str(public),
           "BMEOS_PRINT_DIR": str(tmp_path / "printed")}
    (tmp_path / "events").mkdir()
    server = subprocess.Popen(
        [sys.executable, "-c", "import webbrowser; webbrowser.open = lambda *a, **k: None; "
                               "import launcher; launcher.main()"],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{admin}/start", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("the app didn't start")
        run = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "simulate_event.py")],
            env={**env, "BMEOS_SIM_ADMIN": f"http://127.0.0.1:{admin}",
                 "BMEOS_SIM_PUBLIC": f"http://127.0.0.1:{public}",
                 "BMEOS_SIM_PRINTED": str(tmp_path / "printed")},
            capture_output=True, text=True, timeout=280)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
    assert " 0 problems" in run.stdout and "checks passed" in run.stdout
    print(run.stdout.splitlines()[-1])
