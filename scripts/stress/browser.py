"""
The app in a real browser (Chromium via Playwright): every page at desktop
and phone size, light and dark, watching for script errors, failed requests,
pages wider than the screen and injected script running; then the main
things an operator does, done by clicking.
"""

from __future__ import annotations

import os
import random
import re

import runs
from harness import YESTERDAY, App, Check, Client, at

CHROMIUM = os.environ.get("BMEOS_CHROMIUM", "/opt/pw-browsers/chromium")
OPERATOR_PAGES = ["/", "/competitors", "/classes", "/courses", "/draw", "/download", "/results",
                  "/splits", "/live", "/speaker", "/teams", "/prizes", "/season", "/stages",
                  "/clubs", "/economy", "/audit", "/controls", "/entries", "/eventor", "/setup",
                  "/tools", "/config", "/readout", "/starter", "/start"]
PUBLIC_PAGES = ["/results", "/splits", "/clubs", "/live", "/enter"]


def build(c: Client, rng) -> dict:
    day = YESTERDAY
    ev = c.new_event("Browser Event", day)
    c.setting(results_unplaced="all")
    lin = c.post("/api/courses", {"name": "Long", "type": "linear",
                                  "controls": [31, 32, 33, 34, 35, 36]}, expect=201)["course"]
    score = c.post("/api/courses", {"name": "Score", "type": "score", "time_limit_minutes": 30,
                                    "penalty_per_minute": 2,
                                    "controls": [{"code": k, "points": 10} for k in range(50, 56)]},
                   expect=201)["course"]
    ids = {}
    for name, course, kind in (("M21", lin, "individual"), ("W21", lin, "individual"),
                               ("Score", score, "individual"), ("Relay", lin, "relay")):
        ids[name] = c.post("/api/classes", {"name": name, "course_id": course["id"], "kind": kind,
                                            "legs": 2 if kind == "relay" else 1, "fee": 12},
                           expect=201)["class"]["id"]
    card = 8100001
    for cls in ("M21", "W21", "Score"):
        for n in runs.names(rng, 12):
            c.post("/api/competitors", {"name": n, "club": rng.choice(runs.CLUBS),
                                        "class_id": ids[cls], "card_number": card}, expect=201)
            card += 1
    c.post("/api/competitors", {"name": '<img src=x onerror=alert("xss")>', "club": "<b>bold</b>",
                                "class_id": ids["M21"], "card_number": 8199990}, expect=201)
    for t in range(2):
        team = c.post("/api/teams", {"name": f"Team {t + 1}", "class_id": ids["Relay"],
                                     "start": "11:00:00"}, expect=201)["team"]
        for leg in (1, 2):
            c.post("/api/competitors", {"name": f"Relay {t + 1}.{leg}", "class_id": ids["Relay"],
                                        "card_number": card, "team_id": team["id"], "leg": leg},
                   expect=201)
            card += 1
    c.post("/api/draw", {"class_ids": [ids["M21"], ids["W21"], ids["Score"]],
                         "first_start": "10:00:00", "interval_seconds": 60, "method": "club"},
           expect=200)
    for comp in c.competitors():
        if not comp["card_number"] or comp["class_name"] == "Relay" or rng.random() < 0.15:
            continue
        start = at(day, comp["start"])
        codes = [50, 51, 52, 53] if comp["class_name"] == "Score" else [31, 32, 33, 34, 35, 36]
        plan = runs.linear_run(rng, codes, start, rng.choice(["clean", "clean", "skip"]))
        c.read(comp["card_number"], None, plan["finish"], plan["punches"])
    c.read(8199999, "12:00:00", "12:30:00", [(31, "12:05:00")], expect_ok=False)  # unknown card
    return {"event": ev, "ids": ids, "course": lin["id"]}


def run(check: Check) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("     (Playwright isn't installed: browser checks skipped)")
        return
    rng = random.Random(77)
    with App("browser") as app, sync_playwright() as pw:
        c = Client(app, check)
        data = build(c, rng)
        slug = data["event"]["slug"]
        browser = pw.chromium.launch(executable_path=CHROMIUM if os.path.exists(CHROMIUM) else None)
        try:
            sweep(browser, app, check, slug)
            flows(browser, app, c, check, data)
        finally:
            browser.close()


def watch(page, problems: list):
    page.on("pageerror", lambda e: problems.append(("script error", page.url, str(e)[:150])))
    page.on("console", lambda m: m.type == "error" and "stream" not in m.text and problems.append(
        ("console", page.url, m.text[:150])))
    def dialog(d):
        # Confirmations ("Delete this runner?") are the app asking; say yes. An
        # alert() would be injected script (the app itself never uses alert for
        # data), except the few messages the app shows on purpose.
        if d.type == "alert" and "xss" in d.message:
            problems.append(("injected script ran", page.url, d.message[:80]))
        d.accept()
    page.on("dialog", dialog)

    def response(r):
        if r.status >= 400 and "/api/stream" not in r.url and "favicon" not in r.url:
            try:
                body = r.text()[:160]
            except Exception:  # noqa: BLE001
                body = ""
            problems.append(("http", r.url, r.status, body))
    page.on("response", response)


def sweep(browser, app, check, slug):
    for theme in ("light", "dark"):
        for width, height in ((1366, 800), (390, 844)):
            check.part(f"every page, {theme}, {width}px")
            requests_s = __import__("requests")
            requests_s.post(app.admin + "/api/settings", json={"target": "computer",
                                                               "values": {"theme": theme}})
            ctx = browser.new_context(viewport={"width": width, "height": height})
            page = ctx.new_page()
            problems, wide = [], []
            watch(page, problems)
            for path in OPERATOR_PAGES:
                page.goto(app.admin + path)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(150)
                over = page.evaluate("() => document.documentElement.scrollWidth - "
                                     "document.documentElement.clientWidth")
                if over > 4 and path not in ("/splits",):
                    wide.append((path, over))
                # The projector, readout and start clock are dark screens on purpose.
                if path not in ("/live", "/readout", "/starter"):
                    check.equal(page.evaluate("() => document.documentElement.dataset.theme"),
                                theme, f"{path} in the {theme} theme")
            for path in PUBLIC_PAGES + [f"/public/{slug}"]:
                page.goto(app.public + path)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(200)
                over = page.evaluate("() => document.documentElement.scrollWidth - "
                                     "document.documentElement.clientWidth")
                if over > 4:
                    wide.append(("public " + path, over))
            page.goto(app.admin + "/")   # back to a real event page for the next context
            check(not problems, "no script errors, failed requests or injected script",
                  problems[:6])
            if width < 500:
                check(not wide, "nothing wider than a phone screen", wide[:8])
            ctx.close()


def flows(browser, app, c, check, data):
    import requests
    requests.post(app.admin + "/api/settings", json={"target": "computer",
                                                     "values": {"theme": "light"}})
    ctx = browser.new_context(viewport={"width": 1366, "height": 850})
    page = ctx.new_page()
    problems = []
    watch(page, problems)
    A = app.admin

    def go(path):
        page.goto(A + path)
        page.wait_for_load_state("networkidle")

    check.part("competitor editor")
    go("/competitors")
    page.click("[data-new-competitor]")
    form = page.locator("[data-competitor-form]")
    form.locator("input[name=name]").fill("Clicked Carla")
    form.locator("input[name=club]").fill("Wildflower OC")
    form.locator("select[name=class_id]").select_option(label="W21")
    form.locator("input[name=card_number]").fill("8188888")
    form.locator("button[type=submit]").first.click()
    page.wait_for_timeout(1200)
    names = {x["name"]: x for x in c.competitors()}
    check("Clicked Carla" in names, "new competitor saved from the form")
    go("/competitors")
    page.locator(".entry-row", has_text="Clicked Carla").first.click()
    page.wait_for_timeout(400)
    form.locator("input[name=club]").fill("Edited Club")
    form.locator("button[type=submit]").first.click()
    page.wait_for_timeout(1200)
    names = {x["name"]: x for x in c.competitors()}
    check(names.get("Clicked Carla", {}).get("club") == "Edited Club", "edit saved")
    go("/competitors")
    page.locator(".entry-row", has_text="Clicked Carla").first.locator("[data-delete]").click()
    page.wait_for_timeout(1200)
    check("Clicked Carla" not in {x["name"] for x in c.competitors()}, "deleted after confirming")

    check.part("course editor: a mass-start score course")
    go("/courses")
    page.click("[data-new-course]")
    cf = page.locator("[data-course-form]")
    cf.locator("input[name=name]").fill("Clicked Score")
    cf.locator("label:has(input[name=type][value=score])").click()
    page.wait_for_timeout(200)
    for i, (code, pts) in enumerate(((60, 10), (61, 20), (62, 30))):
        if i:
            cf.locator("[data-add-control]").click()
        row = cf.locator(".control-edit-row").nth(i)
        row.locator(".ctl-code").fill(str(code))
        row.locator(".ctl-points").fill(str(pts))
    cf.locator("input[name=time_limit_minutes]").fill("40")
    cf.locator("select[name=start_mode]").select_option("mass")
    cf.locator("input[name=mass_start]").fill("12:00:00")
    cf.locator("button[type=submit]").first.click()
    page.wait_for_timeout(1200)
    saved = next((x for x in c.courses().values() if x["name"] == "Clicked Score"), None)
    check(saved and saved["type"] == "score" and saved.get("start_mode") == "mass"
          and saved.get("mass_start") == "12:00:00" and len(saved["controls"]) == 3,
          "score course with a mass start saved from the editor", saved)

    check.part("class editor: a relay")
    go("/classes")
    page.click("[data-new-class]")
    kf = page.locator("[data-class-form]")
    kf.locator("input[name=name]").fill("Clicked Relay")
    kf.locator("select[name=kind]").select_option("relay")
    kf.locator("input[name=legs]").fill("3")
    kf.locator("input[name=restart]").fill("13:00:00")
    kf.locator("button[type=submit]").click()
    page.wait_for_timeout(1200)
    html = c.page("/classes")
    check(re.search(r'data-name="Clicked Relay"[^>]*data-kind="relay"', html)
          or ('data-name="Clicked Relay"' in html and 'data-legs="3"' in html),
          "relay class saved from the editor")

    check.part("draw")
    late = c.post("/api/classes", {"name": "Late Class", "course_id": data["course"]},
                  expect=201)["class"]["id"]
    for i in range(4):
        c.post("/api/competitors", {"name": f"Late {i}", "class_id": late,
                                    "card_number": 8170001 + i}, expect=201)
    go("/draw")
    page.locator("[data-draw] button[type=submit]").click()      # everything ticked
    page.wait_for_timeout(1500)
    msg = page.locator("[data-draw] [data-result]").inner_text()
    check("finished" in msg, "redrawing classes that have finished is refused, with why", msg)
    page.locator("[data-all]").uncheck()
    page.locator(f"input[name=class_ids][value='{late}']").check()
    page.locator("[data-draw] button[type=submit]").click()
    page.wait_for_timeout(2500)
    check(all(x["start"] for x in c.competitors() if x["class_name"] == "Late Class"),
          "drawn from the page")
    problems[:] = [p for p in problems if "/api/draw" not in str(p) and "/draw" not in p[1]]

    check.part("download desk")
    go("/download")
    before = c.page("/download").count("tr class=\"runner")
    page.click("[data-simulate]")
    page.wait_for_timeout(2500)
    unknown = page.locator("[data-read-row]").first
    check(unknown.count() == 1, "the unknown card waits on the download page")
    unknown.locator("[data-new-entry]").click()
    qf = page.locator("[data-quick-entry]").first
    qf.locator("input[name=name]").fill("Walk Up Wanda")
    qf.locator("button[type=submit]").click()
    page.wait_for_timeout(1500)
    check("Walk Up Wanda" in {x["name"] for x in c.competitors()}, "walk-up entered from the read")

    check.part("results + splits")
    go("/results")
    rows = page.locator("tr.runner.has-splits")
    if rows.count():
        rows.first.click()
        page.wait_for_timeout(300)
        check(page.locator("tr.splits-row").first.is_visible(), "a click shows the splits")

    check.part("gear menu and settings")
    go("/results")
    page.click("[data-page-settings]")
    page.wait_for_timeout(800)
    page.locator(".set-row[data-key='time_format'] select").select_option("hms")
    page.wait_for_timeout(700)
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)
    check(re.search(r">\d\d:\d\d:\d\d<", page.content()), "the gear change shows on the page")
    c.setting(time_format="auto")
    go("/config?q=theme")
    page.locator(".set-row[data-key='theme'] select").select_option("dark")
    page.wait_for_timeout(700)
    check(page.evaluate("() => document.documentElement.dataset.theme") == "dark",
          "theme applies at once")
    page.locator(".set-row[data-key='theme'] select").select_option("light")
    page.wait_for_timeout(500)

    check.part("palette, help, home screen")
    go("/")
    page.keyboard.press("Control+k")
    page.wait_for_timeout(300)
    page.keyboard.type("Walk Up")
    page.wait_for_timeout(700)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    check("Walk Up Wanda" in page.content(), "Ctrl+K finds and opens a runner")
    page.keyboard.press("Escape")
    go("/download")
    page.click(".help-btn")
    page.wait_for_timeout(400)
    for tab in ("meos", "tours", "page"):
        page.click(f"[data-help-tab={tab}]")
        page.wait_for_timeout(200)
    check(page.locator("[data-help-panel]").is_visible(), "help panel opens with its tabs")
    page.keyboard.press("Escape")
    go("/")
    page.click("[data-dash-edit]")
    page.wait_for_timeout(400)
    removable = page.locator("[data-widget-remove], .widget-remove")
    n_before = page.locator("[data-widget]").count()
    if removable.count():
        removable.first.click()
        page.wait_for_timeout(300)
    page.click("[data-dash-edit]")
    page.wait_for_timeout(800)
    go("/")
    n_after = page.locator("[data-widget]").count()
    check(n_after == n_before - 1 or not removable.count(), "home screen change sticks",
          (n_before, n_after))

    check.part("economy")
    go("/economy")
    owing = page.locator("tr[data-owing='1']").first
    who = owing.locator(".cell-name").inner_text()
    owing.locator("[data-pay]").first.click()
    page.wait_for_timeout(1500)
    go("/economy")
    row = page.locator("tr", has=page.locator(".cell-name", has_text=who)).first
    check(row.get_attribute("data-owing") == "0", "one-click payment")

    check.part("entry page on a phone")
    c.setting(fee_senior=0, fee_junior=0)
    phone = browser.new_context(viewport={"width": 390, "height": 844})
    pp = phone.new_page()
    pprobs = []
    watch(pp, pprobs)
    pp.goto(app.public + "/enter")
    pp.wait_for_load_state("networkidle")
    pp.fill("#name", "Phone Entrant")
    pp.fill("#card", "8177001")
    m21 = pp.evaluate("() => [...document.querySelectorAll('#class option')]"
                      ".find(o => o.textContent.startsWith('M21'))?.value")
    check(m21, "entry page offers the classes")
    pp.select_option("#class", value=m21)
    pp.wait_for_timeout(200)
    pp.click("text=+ Add to cart")
    pp.wait_for_timeout(500)
    if pp.locator("#email").is_visible():
        pp.fill("#email", "phone@example.org")
    pp.click("text=Submit entries")
    pp.wait_for_timeout(2000)
    check("Phone Entrant" in {x["name"] for x in c.competitors()}, "entered from a phone")
    pp.click("#tab-results-btn")
    pp.wait_for_timeout(1500)
    check(pp.locator("text=M21").count() > 0, "results tab on the entry page")
    check(not pprobs, "entry page: no errors", pprobs[:5])
    phone.close()

    check.part("start page")
    go("/start")
    page.fill("[data-new-event] input[name=name]", "Made In The Browser")
    date_input = page.locator("[data-new-event] input[name=date]")
    date_input.fill(str(YESTERDAY))
    page.locator("[data-new-event] button[type=submit]").click()
    page.wait_for_timeout(2000)
    check("/setup" in page.url or "/start" not in page.url, "a new event from the start page",
          page.url)
    check(not problems, "flows: no script errors or failed requests",
          [p for p in problems if p[0] != "dialog"][:6])
    ctx.close()
