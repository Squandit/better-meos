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

    var LIVE_PAGES = { overview: 1, results: 1, splits: 1, download: 1, live: 1 };
    var page = document.body.getAttribute("data-page");
    if (!page || !LIVE_PAGES[page]) { return; }
    if (typeof EventSource === "undefined") { return; }

    var reloadTimer = null;
    function scheduleReload() {
        if (reloadTimer) { return; }          // a reload is already queued
        reloadTimer = setTimeout(function () { window.location.reload(); }, 600);
    }

    var source = new EventSource("/api/stream");
    source.onmessage = function (e) {
        var data = {};
        try { data = JSON.parse(e.data); } catch (err) { return; }
        // Ignore our own keep-alive comment lines (they don't reach onmessage).
        if (data.kind) { scheduleReload(); }
    };

    // A dropped connection auto-retries (EventSource default); nothing to do.
    source.onerror = function () { /* browser will reconnect */ };
})();
