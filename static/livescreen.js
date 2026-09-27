/*
 * The projector screen (/live).
 *
 * Classes that don't fit on the screen are split into pages that rotate every
 * "seconds per page". New results swap in without reloading the page
 * (window.BMSoftRefresh, called by live.js), so the rotation keeps its place
 * and the screen doesn't flash on every card read. Rows for runners who just
 * read out glow once.
 */
(function () {
    "use strict";

    var body = document.body;
    var pages = [];
    var current = 0;
    var timer = null;
    var seen = {};          // result ids already highlighted

    function remember(key, value) {
        try { sessionStorage.setItem(key, value); } catch (e) { /* private mode */ }
    }
    function recall(key) {
        try { return sessionStorage.getItem(key); } catch (e) { return null; }
    }

    function seconds() { return parseInt(body.dataset.pageSeconds || "0", 10) || 0; }
    function cards() {
        return Array.prototype.slice.call(document.querySelectorAll("[data-live-grid] .live-card"));
    }

    // Split the cards into screenfuls, in order.
    function paginate() {
        var all = cards();
        var box = document.querySelector("[data-live-body]");
        var grid = document.querySelector("[data-live-grid]");
        body.classList.toggle("live-paged", seconds() > 0);
        if (!seconds() || !box || !grid || all.length < 2) { all.forEach(function (c) { c.hidden = false; }); return [all]; }
        var out = [];
        var i = 0;
        while (i < all.length) {
            all.forEach(function (c) { c.hidden = true; });
            var j = i;
            while (j < all.length) {
                all[j].hidden = false;
                if (j > i && grid.offsetHeight > box.clientHeight) { all[j].hidden = true; break; }
                j++;
            }
            out.push(all.slice(i, j));
            i = j;
        }
        return out;
    }

    function show(index) {
        current = pages.length ? (index + pages.length) % pages.length : 0;
        cards().forEach(function (c) { c.hidden = true; });
        (pages[current] || []).forEach(function (c) { c.hidden = false; });
        var pager = document.querySelector("[data-live-pager]");
        if (pager) {
            pager.innerHTML = "";
            if (pages.length > 1) {
                pages.forEach(function (_, i) {
                    var dot = document.createElement("i");
                    if (i === current) { dot.className = "on"; }
                    pager.appendChild(dot);
                });
            }
        }
        var first = (pages[current] || [])[0];
        if (first) { remember("bm-live-page", first.dataset.name); }
    }

    // Lay out again, staying on the page that shows `name` (if it still exists).
    function relayout(name) {
        pages = paginate();
        var index = 0;
        if (name) {
            pages.forEach(function (p, i) {
                p.forEach(function (c) { if (c.dataset.name === name) { index = i; } });
            });
        }
        show(index);
    }

    function restartTimer() {
        clearInterval(timer);
        timer = null;
        if (seconds() > 0) {
            timer = setInterval(function () { if (pages.length > 1) { show(current + 1); } },
                                seconds() * 1000);
        }
    }

    // Glow once per runner, not again on every refresh.
    function markFresh() {
        document.querySelectorAll("[data-live-grid] tr.fresh[data-id]").forEach(function (tr) {
            if (seen[tr.dataset.id]) { tr.classList.remove("fresh"); }
            seen[tr.dataset.id] = true;
        });
    }

    function tickClock() {
        var el = document.querySelector("[data-live-clock]");
        if (el) { el.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }); }
    }

    window.BMSoftRefresh = function () {
        return fetch(window.location.href, { cache: "no-store" })
            .then(function (r) { if (!r.ok) { throw new Error(r.status); } return r.text(); })
            .then(function (html) {
                var doc = new DOMParser().parseFromString(html, "text/html");
                var fresh = doc.querySelector("[data-live-content]");
                if (!fresh) { window.location.reload(); return; }
                var shown = (pages[current] || [])[0];
                var before = seconds();
                body.dataset.pageSeconds = doc.body.dataset.pageSeconds || "0";
                body.style.cssText = doc.body.getAttribute("style") || "";
                document.querySelector("[data-live-content]").innerHTML = fresh.innerHTML;
                markFresh();
                tickClock();
                relayout(shown && shown.dataset.name);
                if (seconds() !== before) { restartTimer(); }
            })
            .catch(function () { /* server busy or restarting: the next change retries */ });
    };

    markFresh();
    tickClock();
    setInterval(tickClock, 1000);
    relayout(recall("bm-live-page"));
    restartTimer();
    var resizeTimer = null;
    window.addEventListener("resize", function () {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function () {
            var shown = (pages[current] || [])[0];
            relayout(shown && shown.dataset.name);
        }, 200);
    });
})();
