"""
Security, the way an attacker on the same WiFi would poke at it: every route
on the public port, cross-site requests, the admin password, logins, open
redirects, path tricks, and names that are really HTML or script.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from harness import ROOT, YESTERDAY, App, Check, Client

EVIL = ['<script>alert("x1")</script>', '"><img src=x onerror=alert("x2")>',
        "'; DROP TABLE competitors; --", "{{7*7}}", "</textarea><svg onload=alert(3)>"]


def routes() -> list[tuple[str, list[str]]]:
    """Every route the app has, from the app itself."""
    code = ("import json, app; print(json.dumps([(r.rule, sorted(r.methods - {'HEAD', 'OPTIONS'}))"
            " for r in app.app.url_map.iter_rules()]))")
    env = {**os.environ, "PYTHONPATH": ROOT, "BMEOS_EVENTS_DIR": os.path.join(ROOT, ".nothing")}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         cwd=ROOT, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def concrete(rule: str, slug: str) -> str:
    rule = rule.replace("<slug>", slug)
    rule = re.sub(r"<int:[^>]+>", "1", rule)
    rule = re.sub(r"<path:[^>]+>", "style.css", rule)
    return re.sub(r"<[^>]+>", "x", rule)


def run(check: Check) -> None:
    table = routes()
    check(len(table) > 100, "route list read from the app", len(table))
    with App("guard") as app:
        c = Client(app, check)
        ev = c.new_event("Guard Event", YESTERDAY)
        slug = ev["slug"]
        course = c.post("/api/courses", {"name": "C", "type": "linear", "controls": [31, 32]},
                        expect=201)["course"]
        cls = c.post("/api/classes", {"name": "Open", "course_id": course["id"]},
                     expect=201)["class"]["id"]
        c.post("/api/competitors", {"name": "Plain Runner", "club": "Solo", "class_id": cls,
                                    "card_number": 7900001, "start": "10:00:00"}, expect=201)

        check.part("public port surface")
        public_ok = {"/results", "/splits", "/clubs", "/live", "/enter", "/get-classes",
                     "/get-result-classes", "/get-results", "/search-competitors",
                     "/lookup-competitor", "/check-entered", "/manifest.json", "/sw.js",
                     "/api/online-entry/order", "/api/online-entry/capture", "/api/stream",
                     "/api/version", "/favicon.ico"}
        leaks = []
        for rule, methods in table:
            path = concrete(rule, slug)
            allowed = rule in public_ok or rule.startswith(("/static", "/public/"))
            for method in methods:
                if rule == "/api/stream":
                    continue
                r = c.s.request(method, app.public + path, json={}, timeout=15,
                                allow_redirects=False)
                if r.status_code >= 500:
                    leaks.append((method, path, r.status_code))
                elif not allowed and r.status_code not in (404, 405) and rule != "/":
                    leaks.append((method, path, r.status_code))
        check(not leaks, "the public port only answers the public pages", leaks[:8])
        r = c.s.get(app.public + "/", allow_redirects=False)
        check(r.status_code in (301, 302) and r.headers.get("Location", "").endswith("/results"),
              "public root goes to results")
        html = c.page("/results", public=True)
        admin_links = [h for h in re.findall(r'href="(/[^"]*)"', html)
                       if h not in public_ok and not h.startswith(("/static", "/public", "/results"))]
        check(not admin_links, "public results page doesn't link to operator pages", admin_links[:8])

        check.part("cross-site requests")
        body = {"name": "Cross Site", "class_id": cls}
        for headers, why in (({"Sec-Fetch-Site": "cross-site"}, "Sec-Fetch-Site cross-site"),
                             ({"Sec-Fetch-Site": "same-site"}, "same-site (another port)"),
                             ({"Origin": "http://evil.example"}, "a foreign Origin")):
            r = c.s.post(app.admin + "/api/competitors", json=body, headers=headers)
            check(r.status_code == 403, f"refuses {why}", r.status_code)
        r = c.s.post(app.admin + "/api/competitors", json=dict(body, name="Same Origin"),
                     headers={"Sec-Fetch-Site": "same-origin"})
        check(r.status_code == 201, "same-origin request goes through", r.status_code)
        check("Cross Site" not in [x["name"] for x in c.competitors()], "nothing was created")

        check.part("names that are really code")
        for i, bad in enumerate(EVIL):
            c.post("/api/competitors", {"name": f"Evil {i} {bad}", "club": bad, "class_id": cls,
                                        "card_number": 7900100 + i, "start": "10:00:00"},
                   expect=201)
            c.post("/api/classes", {"name": f"C{i} {bad}"[:60], "course_id": course["id"]},
                   expect=201)
            c.read(7900100 + i, None, "10:30:00", [(31, "10:10:00"), (32, "10:20:00")])
        c.setting(slip_footer=EVIL[0], live_title=EVIL[1], entry_message=EVIL[4])
        pages = ["/", "/competitors", "/classes", "/courses", "/draw", "/download", "/results",
                 "/splits", "/live", "/speaker", "/teams", "/prizes", "/season", "/clubs",
                 "/economy", "/audit", "/controls", "/entries", "/setup", "/tools", "/config",
                 "/readout", "/starter", "/slip/1", "/eventor", "/stages"]
        raw = []
        for path in pages:
            text = c.page(path)
            for bad in EVIL[:2] + EVIL[4:]:
                if bad in text:
                    raw.append((path, bad[:20]))
        for path in (f"/public/{slug}", "/results", "/live", "/enter", "/clubs"):
            text = c.page(path, public=True)
            for bad in EVIL[:2] + EVIL[4:]:
                if bad in text:
                    raw.append(("public " + path, bad[:20]))
        check(not raw, "names are escaped on every page", raw[:8])
        names = {x["name"] for x in c.competitors()}
        check(f"Evil 2 {EVIL[2]}" in names, "SQL-looking text is stored as plain text")
        for path in ("/export/results.pdf", "/export/startlist.pdf", "/export/results.xml",
                     "/slip/2.pdf", "/slip/2.png", "/export/invoices.pdf"):
            r = c.get(path, raw=True)
            check(r.status_code == 200, f"{path} with odd names", r.status_code)

        check.part("path tricks")
        for path in ("/static/%2e%2e/app.py", "/static/..%2fapp.py", "/static/..%5capp.py",
                     "/public/nope", f"/public/{slug}/runner/999999", "/slip/999999"):
            r = c.s.get(app.admin + path, allow_redirects=False)
            check(r.status_code in (400, 404) and "import " not in r.text,
                  f"{path} gives nothing away", r.status_code)
        for path in ("/public/%2e%2e/config", "/static/%2e%2e/config.json",
                     "/public/..%2fapi/settings"):
            r = c.s.get(app.public + path, allow_redirects=False)
            check(r.status_code in (400, 404) and "admin_port" not in r.text,
                  f"public {path} gives nothing away", r.status_code)

        check.part("admin password")
        c.setting("computer", admin_password="correct horse")
        c.s.post(app.admin + "/unlock", data={"password": "correct horse"})
        fresh = __import__("requests").Session()
        r = fresh.get(app.admin + "/api/competitors/1")
        check(r.status_code == 401, "API needs the password", r.status_code)
        r = fresh.get(app.admin + "/results", allow_redirects=False)
        check(r.status_code in (302, 303) and "/unlock" in r.headers.get("Location", ""),
              "pages ask for the password", r.status_code)
        r = fresh.post(app.admin + "/unlock", data={"password": "wrong"})
        check(r.status_code == 401, "a wrong password is refused", r.status_code)
        r = fresh.post(app.admin + "/unlock?next=//evil.example/x",
                       data={"password": "correct horse", "next": "https://evil.example"},
                       allow_redirects=False)
        where = r.headers.get("Location", "")
        check(r.status_code in (302, 303) and "evil" not in where, "no open redirect", where)
        check(fresh.get(app.admin + "/api/competitors/1").status_code == 200, "unlocked")
        cookie = r.headers.get("Set-Cookie", "")
        check("HttpOnly" in cookie and "SameSite=Lax" in cookie, "session cookie flags", cookie)
        fresh.get(app.admin + "/lock")
        check(fresh.get(app.admin + "/api/competitors/1").status_code == 401, "lock locks again")
        r = c.s.get(app.admin + "/api/config")
        check("correct horse" not in r.text and "admin_password_hash" not in r.text,
              "the password never comes back out")
        secrets = c.s.get(app.admin + "/api/settings?q=token").text
        c.setting("computer", station_token="s3cret-token-value")
        check("s3cret-token-value" not in c.s.get(app.admin + "/api/settings").text,
              "secret settings are write-only")

    check.part("logins")
    env = {"BMEOS_AUTH": "1", "BMEOS_ADMIN_USER": "boss", "BMEOS_ADMIN_PASS": "pa55word"}
    with App("guard-login", env=env) as app:
        c = Client(app, check)
        s = c.s
        r = s.get(app.admin + "/start", allow_redirects=False)
        check(r.status_code in (302, 303) and "/login" in r.headers.get("Location", ""),
              "logins on: the console asks you to log in", r.status_code)
        r = s.post(app.admin + "/login", data={"username": "boss", "password": "nope"})
        check("/login" in r.url or r.status_code in (200, 401), "wrong login stays out")
        check(s.get(app.admin + "/api/settings").status_code in (401, 302, 403),
              "API closed before logging in")
        s.post(app.admin + "/login", data={"username": "boss", "password": "pa55word"})
        ev = c.new_event("Login Event", YESTERDAY)
        check(ev.get("slug"), "logged in: can run an event")
        r = c.s.get(app.public + "/results")
        check(r.status_code == 200, "public results need no login", r.status_code)
        s.get(app.admin + "/logout")
        check(s.get(app.admin + "/results", allow_redirects=False).status_code in (302, 303),
              "logged out again")
