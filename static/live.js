/*
 * Live updates via Server-Sent Events.
 *
 * Read-only pages (overview, results, splits, download, the projector screen)
 * open the /api/stream feed; whenever the server reports a change -- a card
 * read, an edit, a restore -- the page re-fetches itself so the standings stay
 * current with no manual refresh. Editing pages (competitors, classes, courses)
 * are deliberately excluded so a reload never interrupts an open form.
 *
 * Reloading the whole page (rather than patching the DOM) keeps the server the
 * single source of truth for ranking and rendering; a short throttle collapses
 * a burst of reads into one reload.
 */
(function () {
    "use strict";

    var LIVE_PAGES = { overview: 1, results: 1, splits: 1, download: 1, live: 1, speaker: 1 };
    var page = document.body.getAttribute("data-page");
    if (!page || !LIVE_PAGES[page]) { return; }

    var reloadTimer = null;
    function scheduleReload() {
        if (reloadTimer) { return; }          // a reload is already queued
        reloadTimer = setTimeout(function () {
            reloadTimer = null;
            // Don't yank the page away while someone is customising it or typing.
            if (document.body.dataset.editing === "1") { scheduleReload(); return; }
            // Pages that can swap in new content in place (the live screen) do.
            if (typeof window.BMSoftRefresh === "function") { window.BMSoftRefresh(); return; }
            window.location.reload();
        }, 600);
    }

    // Fallback when the server refuses a stream (too many open) or the browser
    // gives up on it: poll a cheap change counter and reload when it moves.
    var polling = false;
    function startPolling() {
        if (polling) { return; }
        polling = true;
        var seen = null;
        function poll() {
            fetch("/api/version", { cache: "no-store" })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (seen !== null && d.version !== seen) { scheduleReload(); }
                    seen = d.version;
                })
                .catch(function () { /* offline for a moment; try again */ })
                .then(function () { setTimeout(poll, 15000); });
        }
        poll();
    }

    if (typeof EventSource === "undefined") { startPolling(); return; }

    var source = new EventSource("/api/stream");
    source.onmessage = function (e) {
        var data = {};
        try { data = JSON.parse(e.data); } catch (err) { return; }
        if (data.kind) { scheduleReload(); }
    };

    // A dropped connection auto-retries (EventSource default). A refused one
    // (503 over the cap) closes for good, so switch to polling.
    source.onerror = function () {
        if (source.readyState === EventSource.CLOSED) { startPolling(); }
    };
})();
