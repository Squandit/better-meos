/*
 * Command palette: Ctrl+K (or "/" when you're not typing) anywhere in the
 * console. Type a runner's name, card number or bib to open them in the
 * editor, a class or club to jump to it, a page, a setting, or an action
 * (simulate a read, publish, back up, PDFs, dark mode...). Arrow keys and
 * Enter; Esc closes.
 */
(function () {
    "use strict";

    var ACTIONS = [
        { label: "Add a runner", hint: "Competitors", href: "/competitors#new", words: "new entry competitor on the day" },
        { label: "Simulate a card read", hint: "Download", post: "/api/reader/simulate", words: "download test si" },
        { label: "Publish results now", hint: "Online results", post: "/api/publish/now", words: "ftp upload web" },
        { label: "Back up now", hint: "Backups", post: "/api/backups/now", words: "save copy" },
        { label: "Readout screen", hint: "Opens in a new tab", href: "/readout", tab: true, words: "finish display" },
        { label: "Live screen", hint: "Opens in a new tab", href: "/live", tab: true, words: "projector leaderboard" },
        { label: "Start clock", hint: "Opens in a new tab", href: "/starter", tab: true, words: "starter call up beeps" },
        { label: "Results PDF", href: "/export/results.pdf", words: "print download" },
        { label: "Prize list PDF", href: "/export/prizes.pdf", words: "prize giving print" },
        { label: "Results XML (IOF)", href: "/export/results.xml", words: "export eventor winsplits routegadget" },
        { label: "Start list PDF", href: "/export/startlist.pdf", words: "print" },
        { label: "Club invoices PDF", href: "/export/invoices.pdf", words: "economy money bill" },
        { label: "Dark mode", setting: { theme: "dark" }, words: "theme night black" },
        { label: "Light mode", setting: { theme: "light" }, words: "theme day white" },
        { label: "Customise the home screen", href: "/#customise", words: "dashboard widgets overview" }
    ];

    var modal = null, input = null, list = null, items = [], active = 0, timer = null, seq = 0;

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (text !== undefined && text !== null) { n.textContent = text; }
        return n;
    }

    function ordinal(n) {
        var tens = n % 100, ones = n % 10;
        var suffix = (tens > 10 && tens < 14) ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[ones] || "th");
        return n + suffix;
    }

    function pages() {
        return Array.prototype.map.call(document.querySelectorAll(".nav a"), function (a) {
            return { label: a.textContent.trim(), hint: "Page", href: a.getAttribute("href"),
                     tab: a.target === "_blank" };
        });
    }

    function matches(item, q) {
        var hay = (item.label + " " + (item.words || "") + " " + (item.hint || "")).toLowerCase();
        return q.split(/\s+/).every(function (part) { return hay.indexOf(part) !== -1; });
    }

    function build() {
        modal = el("div", "palette");
        modal.hidden = true;
        modal.setAttribute("role", "dialog");
        modal.setAttribute("aria-label", "Search and commands");
        var box = el("div", "palette-box");
        input = el("input", "palette-input");
        input.type = "search";
        input.placeholder = "Find a runner, card, class, club, page, setting or action…";
        input.setAttribute("aria-label", "Search");
        list = el("div", "palette-list");
        list.setAttribute("role", "listbox");
        var foot = el("div", "palette-foot", "↑↓ to move · Enter to open · Esc to close");
        box.appendChild(input);
        box.appendChild(list);
        box.appendChild(foot);
        modal.appendChild(box);
        document.body.appendChild(modal);
        modal.addEventListener("mousedown", function (e) { if (e.target === modal) { close(); } });
        input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(search, 120); });
        input.addEventListener("keydown", function (e) {
            if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
            else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
            else if (e.key === "Enter") { e.preventDefault(); run(items[active]); }
            else if (e.key === "Escape") { e.preventDefault(); close(); }
        });
    }

    function open() {
        if (!modal) { build(); }
        modal.hidden = false;
        input.value = "";
        render([{ title: "Go to", rows: pages().slice(0, 8) }, { title: "Actions", rows: ACTIONS.slice(0, 6) }]);
        setTimeout(function () { input.focus(); }, 0);
    }
    function close() { if (modal) { modal.hidden = true; } }

    function move(step) {
        if (!items.length) { return; }
        active = (active + step + items.length) % items.length;
        paint();
    }
    function paint() {
        list.querySelectorAll(".palette-item").forEach(function (n, i) {
            n.classList.toggle("active", i === active);
            if (i === active) { n.scrollIntoView({ block: "nearest" }); }
        });
    }

    function render(sections) {
        list.innerHTML = "";
        items = [];
        sections.forEach(function (s) {
            if (!s.rows.length) { return; }
            list.appendChild(el("div", "palette-section", s.title));
            s.rows.forEach(function (row) {
                var index = items.length;
                items.push(row);
                var n = el("div", "palette-item");
                n.setAttribute("role", "option");
                n.appendChild(el("span", "palette-label", row.label));
                if (row.hint) { n.appendChild(el("span", "palette-hint", row.hint)); }
                n.addEventListener("mousemove", function () { if (active !== index) { active = index; paint(); } });
                n.addEventListener("click", function () { run(row); });
                list.appendChild(n);
            });
        });
        if (!items.length) { list.appendChild(el("div", "palette-empty", "Nothing matches.")); }
        active = 0;
        paint();
    }

    function search() {
        var q = input.value.trim().toLowerCase();
        var mine = ++seq;
        if (!q) { open(); return; }
        var local = [
            { title: "Go to", rows: pages().filter(function (p) { return matches(p, q); }) },
            { title: "Actions", rows: ACTIONS.filter(function (a) { return matches(a, q); }) }
        ];
        render(local);
        fetch("/api/search?q=" + encodeURIComponent(q), { cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || mine !== seq) { return; }        // a newer search is on its way
                render([
                    { title: "Runners", rows: d.runners.map(function (r) {
                        var bits = [r["class"], r.club, r.card ? "SI " + r.card : "",
                                    r.time ? r.time + (r.position ? " · " + ordinal(r.position) : "") : r.status];
                        return { label: r.name, hint: bits.filter(Boolean).join(" · "),
                                 href: "/competitors#edit/" + r.id };
                    }) },
                    { title: "Classes", rows: d.classes.map(function (c) {
                        return { label: c.name, hint: "Class results", href: "/results#cls-" + encodeURIComponent(c.name) };
                    }) },
                    { title: "Clubs", rows: d.clubs.map(function (c) {
                        return { label: c, hint: "Club", href: "/clubs#club-" + encodeURIComponent(c) };
                    }) }
                ].concat(local).concat([
                    { title: "Settings", rows: d.settings.map(function (s) {
                        return { label: s.label, hint: s.group, href: "/config?q=" + encodeURIComponent(s.label) };
                    }) }
                ]));
            })
            .catch(function () { /* offline: the local matches stay */ });
    }

    function toast(text) {
        var t = el("div", "palette-toast", text);
        document.body.appendChild(t);
        setTimeout(function () { t.remove(); }, 2600);
    }

    function run(item) {
        if (!item) { return; }
        close();
        if (item.post) {
            fetch(item.post, { method: "POST" }).then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (d) {
                    toast(r.ok ? item.label + ": done" : (d.error || item.label + " failed"));
                });
            });
            return;
        }
        if (item.setting) {
            window.BMSettings.post("computer", item.setting).then(function () {
                if (item.setting.theme) { document.documentElement.setAttribute("data-theme", item.setting.theme); }
                else { window.location.reload(); }
            });
            close();
            return;
        }
        if (item.tab) { window.open(item.href, "_blank", "noopener"); return; }
        var here = window.location.pathname;
        window.location.href = item.href;
        // Same page, new #hash: nothing reloads by itself, so do it.
        if (item.href.indexOf("#") !== -1 && item.href.split("#")[0] === here) { window.location.reload(); }
    }

    document.addEventListener("keydown", function (e) {
        var typing = e.target.closest && e.target.closest("input, textarea, select, [contenteditable]");
        if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            if (modal && !modal.hidden) { close(); } else { open(); }
        } else if (e.key === "/" && !typing && !(modal && !modal.hidden)) {
            e.preventDefault();
            open();
        }
    });
    var trigger = document.querySelector("[data-palette]");
    if (trigger) { trigger.addEventListener("click", open); }
    window.BMPalette = { open: open };
})();
