/*
 * Split slips printed from this page, without popups.
 *
 * When the Windows app prints slips itself ("Print slips: straight to the
 * printer"), Print just asks the server to print. Otherwise:
 *
 * Each slip loads in a hidden frame that prints itself (/slip/<id>?print=1).
 * Opening a window from code that isn't a click gets blocked as a popup, which
 * is how auto-print used to work; a frame on this page never is. With silent
 * printing (kiosk mode, see Settings) the slip goes straight to the printer.
 * Slips print one at a time, in the order they were asked for, and live
 * reloads wait until the queue is empty.
 */
(function () {
    "use strict";

    var queue = [];
    var busy = false;

    function hold(on) {
        if (on) { document.body.dataset.printing = "1"; } else { delete document.body.dataset.printing; }
    }

    function next() {
        if (busy) { return; }
        if (!queue.length) { hold(false); return; }
        busy = true;
        hold(true);
        var id = queue.shift();
        var frame = document.createElement("iframe");
        frame.className = "print-frame";
        frame.setAttribute("aria-hidden", "true");
        frame.tabIndex = -1;
        var done = false;
        function finish() {
            if (done) { return; }
            done = true;
            setTimeout(function () { frame.remove(); busy = false; next(); }, 400);
        }
        // print() in the frame blocks until the slip has gone (or the dialog
        // is closed), so the frame's load event means it's printed.
        frame.addEventListener("load", finish);
        setTimeout(finish, 20000);   // never let one stuck slip stop the rest
        frame.src = "/slip/" + encodeURIComponent(id) + "?print=1";
        document.body.appendChild(frame);
    }

    // With direct printing on (the Windows app), the server prints: no frame,
    // no dialog.
    function direct() { return !!document.querySelector("[data-print-direct]"); }

    function viaServer(id) {
        fetch("/api/print/slip/" + encodeURIComponent(id), { method: "POST" })
            .then(function (r) { return r.json().then(function (d) { if (!r.ok) { throw new Error(d.error || "Print failed"); } return d; }); })
            .then(function () { note("Slip sent to the printer"); })
            .catch(function (err) { note(err.message); });
    }
    function note(text) {
        var out = document.querySelector("[data-sim-result]");
        if (out) { out.textContent = text; }
    }

    window.BMPrint = {
        slip: function (id) { if (direct()) { viaServer(id); return; } queue.push(id); next(); },
        busy: function () { return busy || queue.length > 0; }
    };

    // Any [data-print-slip] link prints in place instead of opening a tab.
    document.addEventListener("click", function (e) {
        var link = e.target.closest("[data-print-slip]");
        if (!link) { return; }
        e.preventDefault();
        e.stopPropagation();
        window.BMPrint.slip(link.dataset.printSlip);
    });
})();
