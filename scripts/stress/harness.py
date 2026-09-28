"""
The stress suite's plumbing: start real app processes with their own data,
talk to them over HTTP, and record what was checked.

Every scenario gets a fresh app (its own events folder, settings, runner
database and ports) so scenarios never lean on each other's data. Slips go
to a PNG folder (``BMEOS_PRINT_DIR``) so printing is checked without a
printer.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The events run "yesterday" by default: every run is over, nobody is still out.
YESTERDAY = date.today() - timedelta(days=1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class App:
    """One running Control: ``admin`` / ``public`` base URLs, ``dir``."""

    def __init__(self, name: str, env: dict | None = None, keep: bool = False):
        self.name = name
        self.dir = tempfile.mkdtemp(prefix=f"bmeos-stress-{name}-")
        self.keep = keep
        self.admin_port, self.public_port = free_port(), free_port()
        self.admin = f"http://127.0.0.1:{self.admin_port}"
        self.public = f"http://127.0.0.1:{self.public_port}"
        self.printed = os.path.join(self.dir, "printed")
        os.makedirs(os.path.join(self.dir, "events"))
        self.env = {**os.environ, "PYTHONPATH": ROOT,
                    "BMEOS_CONFIG": os.path.join(self.dir, "config.json"),
                    "BMEOS_EVENTS_DIR": os.path.join(self.dir, "events"),
                    "BMEOS_RUNNERS_DB": os.path.join(self.dir, "runners.db"),
                    "BMEOS_PORT": str(self.admin_port),
                    "BMEOS_PUBLIC_PORT": str(self.public_port),
                    "BMEOS_PRINT_DIR": self.printed, **(env or {})}
        self.proc = None
        self.log_path = os.path.join(self.dir, "app.log")

    def start(self) -> "App":
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "import webbrowser; webbrowser.open = lambda *a, **k: None; "
                                   "import launcher; launcher.main()"],
            cwd=self.dir, env=self.env, stdout=self._log, stderr=subprocess.STDOUT)
        for _ in range(80):
            try:
                urllib.request.urlopen(self.admin + "/start", timeout=2)
                return self
            except OSError:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.25)
        raise RuntimeError(f"app {self.name} didn't start:\n{self.log()}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "_log", None):
            self._log.close()

    def restart(self) -> "App":
        self.stop()
        return self.start()

    def last_traceback(self) -> str:
        """The end of the most recent traceback in the app's log."""
        text = self.log()
        i = text.rfind("Traceback")
        return text[i:][-900:] if i >= 0 else ""

    def log(self) -> str:
        try:
            with open(self.log_path, "rb") as f:
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def cleanup(self) -> None:
        self.stop()
        if not self.keep:
            shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.cleanup()


class Check:
    """Collects check results for one scenario."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.passes = 0
        self.problems: list[str] = []
        self.section = ""

    def part(self, name: str) -> None:
        self.section = name
        print(f"  -- {name}", flush=True)

    def __call__(self, ok, what: str, detail="") -> bool:
        if ok:
            self.passes += 1
        else:
            msg = f"[{self.section}] {what}" + (f": {detail}" if detail != "" else "")
            self.problems.append(msg)
            print("  PROBLEM", msg, flush=True)
        return bool(ok)

    def equal(self, got, want, what: str) -> bool:
        return self(got == want, what, f"want {want!r}, got {got!r}")


class Client:
    """HTTP to one app, with every call checked for server errors."""

    def __init__(self, app: App, check: Check):
        self.app, self.check = app, check
        self.s = requests.Session()

    def _url(self, path, public=False):
        return (self.app.public if public else self.app.admin) + path

    def call(self, method, path, body=None, *, public=False, expect=None, files=None,
             data=None, headers=None, raw=False):
        url = self._url(path, public)
        if files is not None:
            r = self.s.request(method, url, files=files, data=data or {}, headers=headers)
        elif body is not None or method != "GET":
            r = self.s.request(method, url, json=body if body is not None else {},
                               headers=headers)
        else:
            r = self.s.request(method, url, headers=headers)
        if r.status_code >= 500 and r.status_code != expect:
            self.check(False, f"{method} {path} server error {r.status_code}",
                       self.app.last_traceback() or r.text[:400])
        elif expect is not None and r.status_code != expect:
            self.check(False, f"{method} {path} -> {r.status_code}, wanted {expect}",
                       r.text[:300])
        if raw:
            return r
        try:
            return r.json()
        except ValueError:
            return r

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body, **kw)

    def put(self, path, body=None, **kw):
        return self.call("PUT", path, body, **kw)

    def delete(self, path, **kw):
        return self.call("DELETE", path, **kw)

    def upload(self, path, filename, text, expect=200, field="file", data=None):
        return self.call("POST", path, files={field: (filename, text)}, data=data,
                         expect=expect)

    def page(self, path, public=False, expect=200, contains=None) -> str:
        r = self.s.get(self._url(path, public))
        bad = r.status_code != expect or "Traceback" in r.text or \
            "Internal Server Error" in r.text
        self.check(not bad, f"page {path}", f"-> {r.status_code}")
        if contains:
            for text in ([contains] if isinstance(contains, str) else contains):
                self.check(text in r.text, f"page {path} shows {text!r}")
        return r.text

    def setting(self, target="event", **values):
        return self.post("/api/settings", {"target": target, "values": values}, expect=200)

    # ---- things nearly every scenario needs ------------------------------------

    def new_event(self, name, day: date = None, first_start="10:00:00", **extra):
        day = day or YESTERDAY
        out = self.post("/api/events/new", {"name": name, "date": str(day),
                                            "first_start": first_start, **extra}, expect=201)
        self._slug = out["event"]["slug"]
        return out["event"]

    def results(self) -> dict:
        """Public results by class name -> {name: row}."""
        data = self.get(f"/public/{self.slug()}/results.json", public=True)
        return {c["name"]: {r["name"]: r for r in c["results"]} for c in data["classes"]}

    def slug(self) -> str:
        return self._slug

    def read(self, card, start=None, finish=None, punches=(), check_time=None,
             station=None, expect_ok=True, public=False, headers=None):
        """A card read: times as datetimes (or clock strings)."""
        body = {"card_number": card, "start": clock(start), "finish": clock(finish),
                "punches": [{"code": c, "time": clock(t)} for c, t in punches]}
        if check_time is not None:
            body["check"] = clock(check_time)
        if station:
            body["station_id"] = station
        r = self.call("POST", "/api/reader/simulate", body, public=public, headers=headers,
                      raw=True)
        out = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if expect_ok:
            self.check(r.status_code == 200 and out.get("ok"), f"read {card}", r.text[:200])
        return out

    def classes(self) -> dict:
        """Class name -> id (from the Classes page)."""
        import re
        html = self.s.get(self.app.admin + "/classes").text
        return {m.group(2): int(m.group(1)) for m in re.finditer(
            r'data-class-row data-id="(\d+)" data-name="([^"]+)"', html)}

    def competitors(self) -> list[dict]:
        """Every competitor's editable JSON."""
        import re
        html = self.s.get(self.app.admin + "/competitors").text
        ids = [int(i) for i in re.findall(r'class="entry-row" data-id="(\d+)"', html)]
        return [self.get(f"/api/competitors/{i}")["competitor"] for i in ids]

    def courses(self) -> dict:
        out = {}
        for i in range(1, 200):
            r = self.s.get(f"{self.app.admin}/api/courses/{i}")
            if r.status_code == 200:
                c = r.json().get("course", r.json())
                out[c["name"]] = c
            elif i > 40 and not out:
                break
        return out


def clock(t) -> str | None:
    if t is None:
        return None
    if isinstance(t, datetime):
        return t.strftime("%H:%M:%S")
    return str(t)


def at(day: date, hms: str) -> datetime:
    return datetime.combine(day, datetime.strptime(hms, "%H:%M:%S").time())
