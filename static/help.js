/*
 * Help panel (right side) and guided tours.
 *
 * Three tabs: "This page" (what it's for, the MeOS equivalent, and "Show me"
 * buttons that point at the controls that matter), "From MeOS" (every MeOS tab
 * mapped to where it lives here) and "Tours" (walkthroughs that move from page
 * to page, spotlighting the button to press at each step). Tour progress is
 * kept in sessionStorage so it survives the page changes it makes.
 */
(function () {
    "use strict";

    // ---- Content ---------------------------------------------------------------

    var PAGES = {
        overview: {
            title: "Overview", meos: "No single MeOS equivalent: a live summary of the event.",
            what: "Your home screen. Widgets show what needs attention, cards coming in, who's still out and more. Press Customise to add, remove, drag or resize them; each computer keeps its own layout.",
            show: [["Customise the screen", "[data-dash-edit]"], ["Find anything (Ctrl+K)", "[data-palette]"]]
        },
        competitors: {
            title: "Competitors", meos: "MeOS: Runners tab.",
            what: "Everyone entered. Click a row to edit: card, class, start and finish, punches, status, fee. The panel on the right re-checks the run as you type, so you see OK / MP before saving.",
            show: [["Add a runner", "[data-new-competitor]"], ["Search the list", "[data-filter] .search"]]
        },
        eventor: {
            title: "Eventor", meos: "MeOS: Competition > Eventor connection.",
            what: "With your club's API key: check the connection, link this event to its Eventor event, fetch entries (again for late entries, nobody gets doubled up), load your members' SI cards for autofill, and upload the results.",
            show: [["Fetch entries", "[data-ev=fetch]"], ["Upload results", "[data-ev=upload]"]]
        },
        entries: {
            title: "Entries", meos: "MeOS: entries from Eventor or the online entry form.",
            what: "Online entries and payments. Paid orders become competitors automatically; anything that needs a look (paid but not entered, pending) is flagged here.",
            show: []
        },
        draw: {
            title: "Start draw", meos: "MeOS: Classes tab > Draw start times.",
            what: "Tick classes, pick the first start, interval and method (random, clubs separated, alphabetical) and draw. Vacant slots can be added for late entries.",
            show: [["The draw form", "[data-draw]"]]
        },
        classes: {
            title: "Classes", meos: "MeOS: Classes tab.",
            what: "Each class runs a course. Edit a class for relay legs, fees, forking, entry caps, online entry, and whether results show times, names only or stay hidden.",
            show: [["Add a class", "[data-new-class]"]]
        },
        courses: {
            title: "Courses", meos: "MeOS: Courses tab.",
            what: "Control sequences. Import them from OCAD, Purple Pen or Condes on Import / Export, or type them here. Score courses have points per control and a time limit.",
            show: [["Add a course", "[data-new-course]"]]
        },
        controls: {
            title: "Controls", meos: "MeOS: Controls tab (control status Bad, Optional, No timing).",
            what: "What happened at every control while the event runs. A unit broken or missing? Set it to Bad and nobody needs it. A spare unit with a different code? Add it as an alternate.",
            show: [["Control statuses", "[data-controls]"]]
        },
        download: {
            title: "Download", meos: "MeOS: SportIdent tab (readout).",
            what: "Every card read at the finish. Unknown cards are kept until you say whose they are, or add them as a new runner in one click. Slips can print automatically.",
            show: [["Simulate a card read", "[data-simulate]"], ["Auto-print split slips", "[data-autoprint]"], ["Readout screen for runners", "a[href='/readout']"]]
        },
        results: {
            title: "Results", meos: "MeOS: Lists tab > Results.",
            what: "Live standings. Click a runner for their splits. The gear sets how results look everywhere: time format, by class or course, who's listed, class order, the live screen.",
            show: [["Results settings", "[data-page-settings]"], ["Open the live screen", ".page-head a[href='/live']"]]
        },
        splits: {
            title: "Splits", meos: "MeOS: Lists tab > Split times.",
            what: "Every leg of every runner, with leg places and the fastest leg highlighted.",
            show: []
        },
        teams: { title: "Teams", meos: "MeOS: Teams tab.", what: "Relay teams and their legs.", show: [] },
        prizes: {
            title: "Prizes", meos: "MeOS: Lists tab > Prize list.",
            what: "Who gets a prize under this event's rules (gear menu): places, share of starters, which classes, one prize per season.",
            show: [["Prize rules", "[data-page-settings]"]]
        },
        season: { title: "Season", meos: "MeOS: separate series tools.", what: "Standings across every event with the same season name. Points rules are in the gear menu.", show: [["Season rules", "[data-page-settings]"]] },
        stages: { title: "Multi-stage", meos: "MeOS: Multi-stage events (Competition tab).", what: "Combine several event files into overall standings, and set chase starts from them.", show: [] },
        speaker: { title: "Speaker", meos: "MeOS: Speaker tab.", what: "Who's out on course, where they were at radio controls, and when they'd need to finish to lead.", show: [] },
        clubs: { title: "Clubs", meos: "MeOS: Clubs tab.", what: "Everyone grouped by club.", show: [] },
        economy: {
            title: "Economy", meos: "MeOS: Economy / invoices (Clubs tab).",
            what: "What everyone owes and has paid. One click records a payment; each club gets an invoice PDF.",
            show: [["Record payments", "[data-payments]"]]
        },
        audit: { title: "Change log", meos: "No MeOS equivalent.", what: "Who changed what and when: handy for protests and 'why is this runner MP?'.", show: [] },
        setup: {
            title: "Setup", meos: "MeOS: Competition tab.",
            what: "The event hub. The race-day checklist says what's missing before the start; the after-event one says who hasn't read out and what's left to tidy up.",
            show: [["Checklists", ".checklist-grid"]]
        },
        tools: {
            title: "Import / Export", meos: "MeOS: Competition tab > Import / Export.",
            what: "Courses from OCAD or Purple Pen, start lists, entries, results (IOF XML for Eventor, WinSplits, Routegadget), PDFs and backups.",
            show: [["Import courses", "[data-upload='/api/import/courses']"], ["Results for Eventor (IOF XML)", "a[href='/export/results.xml']"]]
        },
        config: { title: "Settings", meos: "MeOS: Competition settings + Automatic tasks.", what: "Every setting, searchable. 'This event' changes only this event; 'Defaults for every event' is what new events start with.", show: [] }
    };

    var MEOS = [
        ["Competition", "Setup, and the start page (new / open event). Settings for everything else.", "/setup"],
        ["Runners", "Competitors. Click a row to edit.", "/competitors"],
        ["Teams", "Teams.", "/teams"],
        ["Classes", "Classes; drawing start times has its own page, Start draw (under More tools).", "/classes"],
        ["Courses", "Courses; import from OCAD on Import / Export.", "/courses"],
        ["Controls", "Controls, with the same Bad / Optional / No timing statuses.", "/controls"],
        ["Clubs", "Clubs; invoices are on Economy.", "/clubs"],
        ["Lists", "Results, Splits, Prizes, and the PDFs on Import / Export.", "/results"],
        ["SportIdent", "Download. Reader port in Settings > SI reader.", "/download"],
        ["Interactive readout", "Unknown cards: 'New runner' on Download, or Settings > Unknown cards.", "/download"],
        ["Speaker", "Speaker.", "/speaker"],
        ["Automatic (backup, online results, print splits)", "Settings > Backups / Online results; auto-print on Download.", "/config"],
        [".meos files", "Each event is one .bmeos file in the events folder. Bring a MeOS event across as IOF XML (results / entries).", "/tools"]
    ];
    var DIFFERENT = [
        "Results are never stored: they're worked out fresh from the punches every time, so fixing a card or a course fixes every list at once.",
        "Two ports: the operator console, and a public one (results + online entry) that's safe to share or put on the internet.",
        "Runners still out show as 'On course', not DNF, until their card is read.",
        "Ctrl+K finds any runner, card, page or setting from anywhere."
    ];

    var TOURS = [
        { id: "first", title: "Set up your first event", blurb: "Courses, classes, runners, start times, then the checklist. About 3 minutes.",
          steps: [
            { page: "/tools", el: "[data-upload='/api/import/courses']", title: "Start with courses", text: "Export IOF XML (CourseData) from OCAD or Purple Pen and import it here. If classes are set up there too, they come across on their courses." },
            { page: "/courses", el: "[data-new-course]", title: "Or type a course", text: "No map program file? Add a course here: its control codes in order, or points for a score course." },
            { page: "/classes", el: "[data-new-class]", title: "Classes run courses", text: "Add a class and pick its course. Edit it later for fees, entry caps, relays, or names-only results for kids." },
            { page: "/competitors", el: "[data-new-competitor]", title: "Add runners", text: "One at a time here, or import a start list / Eventor entries on Import / Export. Online entries arrive by themselves." },
            { page: "/draw", el: "[data-draw]", title: "Draw start times", text: "Pick classes, first start and interval. 'Clubs separated' keeps club mates apart; vacants leave room for late entries." },
            { page: "/setup", el: ".checklist-grid", title: "Check you're ready", text: "The race-day checklist lists anything missing: cards, start times, the reader, backups. Green means go." }
          ] },
        { id: "raceday", title: "Race day at the finish", blurb: "Reading out cards, slips, the readout and live screens.",
          steps: [
            { page: "/download", el: "[data-simulate]", title: "Cards arrive here", text: "With the reader on (Settings > SI reader) every card read appears here. Simulate a read to practise with no hardware." },
            { page: "/download", el: "[data-autoprint]", title: "Split slips", text: "Pick when slips print by themselves: every read, only OK runs, or only problems." },
            { page: "/download", el: "a[href='/readout']", title: "The runners' screen", text: "Open this on a screen facing the finish: a big OK or MP and a beep for each card." },
            { page: "/results", el: "[data-page-settings]", title: "How results look", text: "The gear on any page holds just that page's settings. Here: time format, by class or course, who's listed." },
            { page: "/results", el: ".page-head a[href='/live']", title: "The live screen", text: "Put this on a projector. It pages through classes by itself and lights up whoever just finished." },
            { page: "/speaker", el: ".page", title: "Who's still out", text: "The speaker page lists everyone on course, longest first. Handy for the commentator and for safety." }
          ] },
        { id: "after", title: "After the event", blurb: "Everyone back? Prizes, results for Eventor, money.",
          steps: [
            { page: "/setup", el: ".checklist-grid", title: "Is everyone back?", text: "The after-event checklist names anyone who started but never read out. Check they're safe before packing up." },
            { page: "/prizes", el: ".toolbar .btn-primary", title: "Prize giving", text: "The prize list follows the event's rules (gear menu). Read it out from the bottom of each class." },
            { page: "/tools", el: "a[href='/export/results.xml']", title: "Results for Eventor", text: "IOF XML with split times: upload it to Eventor, WinSplits or Routegadget." },
            { page: "/economy", el: "[data-payments]", title: "Money", text: "Who still owes what. Record payments with one click and print club invoices." }
          ] },
        { id: "fast", title: "Finding things fast", blurb: "Two tricks that save a lot of clicking.",
          steps: [
            { page: null, el: "[data-palette]", title: "Ctrl+K finds anything", text: "Type a runner's name, card number or bib and press Enter to open them. It also finds pages, classes, settings and actions like 'publish'." },
            { page: null, el: "[data-page-settings]", title: "Each page's settings", text: "The gear shows only the settings for the page you're on. Settings in the sidebar has all of them, with search." },
            { page: null, el: "[data-nav-more]", title: "More tools", text: "The pages you need every event are at the top. Everything else lives under More tools." }
          ] }
    ];

    // ---- Helpers ----------------------------------------------------------------

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (text !== undefined && text !== null) { n.textContent = text; }
        return n;
    }
    function remember(key, value) { try { if (value === null) { sessionStorage.removeItem(key); } else { sessionStorage.setItem(key, value); } } catch (e) { /* private mode */ } }
    function recall(key) { try { return sessionStorage.getItem(key); } catch (e) { return null; } }
    function seenBefore() { try { return localStorage.getItem("bm-help-seen") === "1"; } catch (e) { return true; } }
    function markSeen() { try { localStorage.setItem("bm-help-seen", "1"); } catch (e) { /* ignore */ } }

    function find(selector) {
        var target = selector ? document.querySelector(selector) : null;
        if (!target) { return null; }
        var details = target.closest("details");
        if (details && !details.open) { details.open = true; }
        return target.getClientRects().length ? target : null;
    }

    // ---- Spotlight ------------------------------------------------------------------

    var spot = null, callout = null;
    function clearSpot() {
        if (spot) { spot.remove(); spot = null; }
        if (callout) { callout.remove(); callout = null; }
        window.removeEventListener("resize", place);
        window.removeEventListener("scroll", place, true);
    }
    var current = null;   // {target, box}
    function place() {
        if (!current || !callout) { return; }
        var t = current.target;
        if (t && spot) {
            var r = t.getBoundingClientRect(), pad = 6;
            spot.style.left = (r.left - pad) + "px";
            spot.style.top = (r.top - pad) + "px";
            spot.style.width = (r.width + pad * 2) + "px";
            spot.style.height = (r.height + pad * 2) + "px";
            var cw = callout.offsetWidth, ch = callout.offsetHeight, vw = window.innerWidth, vh = window.innerHeight;
            var top = r.bottom + 14 + ch < vh ? r.bottom + 14 : Math.max(12, r.top - ch - 14);
            var left = Math.min(Math.max(12, r.left), vw - cw - 12);
            if (r.bottom + 14 + ch >= vh && r.top - ch - 14 < 0) {        // tall target: beside it
                top = Math.min(Math.max(12, r.top), vh - ch - 12);
                left = r.right + 14 + cw < vw ? r.right + 14 : Math.max(12, r.left - cw - 14);
            }
            callout.style.top = top + "px";
            callout.style.left = left + "px";
        } else {
            callout.style.top = "30%";
            callout.style.left = "calc(50% - " + (callout.offsetWidth / 2) + "px)";
        }
    }
    function spotlight(target, build) {
        clearSpot();
        current = { target: target };
        if (target) {
            target.scrollIntoView({ block: "center", behavior: "smooth" });
            spot = el("div", "help-spot");
            document.body.appendChild(spot);
        }
        callout = el("div", "help-callout" + (target ? "" : " help-callout-free"));
        callout.setAttribute("role", "dialog");
        build(callout);
        document.body.appendChild(callout);
        place();
        setTimeout(place, 350);          // after the smooth scroll
        window.addEventListener("resize", place);
        window.addEventListener("scroll", place, true);
    }

    // "Show me": point at one control for a moment.
    function showMe(selector, label) {
        closePanel();
        var target = find(selector);
        spotlight(target, function (box) {
            box.appendChild(el("strong", null, label));
            box.appendChild(el("p", null, target ? "This one." : "It isn't on the screen right now (it appears once there's something to show)."));
            var ok = el("button", "btn btn-primary btn-sm", "Got it");
            ok.type = "button";
            ok.addEventListener("click", clearSpot);
            box.appendChild(ok);
        });
        if (target) { setTimeout(function () { if (current && current.target === target) { clearSpot(); } }, 6000); }
    }

    // ---- Tours ---------------------------------------------------------------------

    function tourById(id) { return TOURS.filter(function (t) { return t.id === id; })[0]; }
    function startTour(id) { closePanel(); markSeen(); goto(id, 0); }
    function endTour() { remember("bm-tour", null); clearSpot(); }
    function goto(id, index) {
        var tour = tourById(id);
        if (!tour || index < 0 || index >= tour.steps.length) { endTour(); return; }
        remember("bm-tour", JSON.stringify({ id: id, step: index }));
        var step = tour.steps[index];
        if (step.page && step.page !== window.location.pathname) { window.location.href = step.page; return; }
        showStep(tour, index);
    }
    function showStep(tour, index) {
        var step = tour.steps[index];
        var target = find(step.el);
        spotlight(target, function (box) {
            box.appendChild(el("div", "help-callout-count", tour.title + " · " + (index + 1) + " of " + tour.steps.length));
            box.appendChild(el("strong", null, step.title));
            box.appendChild(el("p", null, step.text));
            var row = el("div", "help-callout-actions");
            var skip = el("button", "link-btn", "End tour");
            skip.type = "button";
            skip.addEventListener("click", endTour);
            row.appendChild(skip);
            if (index > 0) {
                var back = el("button", "btn btn-ghost btn-sm", "Back");
                back.type = "button";
                back.addEventListener("click", function () { goto(tour.id, index - 1); });
                row.appendChild(back);
            }
            var last = index === tour.steps.length - 1;
            var next = el("button", "btn btn-primary btn-sm", last ? "Done" : "Next");
            next.type = "button";
            next.addEventListener("click", function () { if (last) { endTour(); } else { goto(tour.id, index + 1); } });
            row.appendChild(next);
            box.appendChild(row);
            setTimeout(function () { next.focus(); }, 50);
        });
    }
    function resume() {
        var raw = recall("bm-tour");
        if (!raw) { return; }
        var state;
        try { state = JSON.parse(raw); } catch (e) { endTour(); return; }
        var tour = tourById(state.id);
        if (!tour || !tour.steps[state.step]) { endTour(); return; }
        var step = tour.steps[state.step];
        if (step.page && step.page !== window.location.pathname) { endTour(); return; }   // wandered off
        setTimeout(function () { showStep(tour, state.step); }, 250);
    }

    // ---- Panel ---------------------------------------------------------------------

    var panel = document.querySelector("[data-help-panel]");
    if (!panel) { return; }
    var body = panel.querySelector("[data-help-body]");
    var tabs = panel.querySelectorAll("[data-help-tab]");
    var pageKey = document.body.dataset.page || "";

    function renderPage() {
        var info = PAGES[pageKey];
        body.innerHTML = "";
        if (!info) {
            body.appendChild(el("p", "help-lead", "Pick a tour to get going, or open any page and come back here for help with it."));
            return;
        }
        body.appendChild(el("h4", "help-h", info.title));
        body.appendChild(el("p", "help-lead", info.what));
        var meos = el("p", "help-meos");
        meos.appendChild(el("span", "help-tag", "MeOS"));
        meos.appendChild(document.createTextNode(" " + info.meos.replace(/^MeOS: /, "")));
        body.appendChild(meos);
        if (info.show.length) {
            body.appendChild(el("div", "help-sub", "Show me"));
            info.show.forEach(function (item) {
                var b = el("button", "help-show", item[0]);
                b.type = "button";
                b.addEventListener("click", function () { showMe(item[1], item[0]); });
                body.appendChild(b);
            });
        }
        body.appendChild(el("div", "help-sub", "Tip"));
        body.appendChild(el("p", "help-tip", "Ctrl+K (or /) jumps to any runner, card number, page or setting."));
    }
    function renderMeos() {
        body.innerHTML = "";
        body.appendChild(el("p", "help-lead", "Where each MeOS tab lives here."));
        var list = el("div", "help-map");
        MEOS.forEach(function (row) {
            var a = el("a", "help-map-row");
            a.href = row[2];
            a.appendChild(el("span", "help-map-from", row[0]));
            a.appendChild(el("span", "help-map-to", row[1]));
            list.appendChild(a);
        });
        body.appendChild(list);
        body.appendChild(el("div", "help-sub", "What works differently"));
        var ul = el("ul", "help-list");
        DIFFERENT.forEach(function (t) { ul.appendChild(el("li", null, t)); });
        body.appendChild(ul);
    }
    function renderTours() {
        body.innerHTML = "";
        body.appendChild(el("p", "help-lead", "Tours move from page to page and point at each button you'll use."));
        TOURS.forEach(function (tour) {
            var card = el("button", "help-tour");
            card.type = "button";
            card.appendChild(el("strong", null, tour.title));
            card.appendChild(el("span", null, tour.blurb));
            card.appendChild(el("em", null, tour.steps.length + " steps →"));
            card.addEventListener("click", function () { startTour(tour.id); });
            body.appendChild(card);
        });
    }
    var RENDER = { page: renderPage, meos: renderMeos, tours: renderTours };
    function show(tab) {
        tabs.forEach(function (t) { t.classList.toggle("active", t.dataset.helpTab === tab); t.setAttribute("aria-selected", t.dataset.helpTab === tab ? "true" : "false"); });
        RENDER[tab]();
        try { localStorage.setItem("bm-help-tab", tab); } catch (e) { /* ignore */ }
    }
    function openPanel(tab) {
        clearSpot();
        markSeen();
        document.querySelectorAll("[data-help-open]").forEach(function (b) { b.classList.remove("help-new"); });
        var saved = null;
        try { saved = localStorage.getItem("bm-help-tab"); } catch (e) { /* ignore */ }
        show(tab || (PAGES[pageKey] ? (saved || "page") : "tours"));
        panel.hidden = false;
        requestAnimationFrame(function () { panel.classList.add("open"); });
    }
    function closePanel() {
        panel.classList.remove("open");
        setTimeout(function () { panel.hidden = true; }, 180);
    }
    tabs.forEach(function (t) { t.addEventListener("click", function () { show(t.dataset.helpTab); }); });
    document.querySelectorAll("[data-help-open]").forEach(function (b) {
        b.addEventListener("click", function () { if (panel.hidden) { openPanel(b.dataset.helpOpen || null); } else { closePanel(); } });
        if (!seenBefore()) { b.classList.add("help-new"); }
    });
    panel.addEventListener("click", function (e) { if (e.target.closest("[data-help-close]")) { closePanel(); } });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            if (callout) { endTour(); } else if (!panel.hidden) { closePanel(); }
        } else if ((e.key === "?" || e.key === "F1") && !(e.target.closest && e.target.closest("input, textarea, select"))) {
            e.preventDefault();
            if (panel.hidden) { openPanel(); } else { closePanel(); }
        }
    });
    window.BMHelp = { open: openPanel, tour: startTour };
    resume();
})();
