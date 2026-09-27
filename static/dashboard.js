/*
 * Home screen: customise mode (add / remove / reorder / resize widgets) plus
 * the small interactive bits inside widgets (simulate, quick actions, notes,
 * start countdown). The layout is saved to the server (per computer).
 */
(function () {
    "use strict";
    var grid = document.querySelector("[data-dash-grid]");
    if (!grid) { return; }
    var editBtn = document.querySelector("[data-dash-edit]");
    var resetBtn = document.querySelector("[data-dash-reset]");
    var tray = document.querySelector("[data-dash-tray]");
    var SIZES = ["small", "wide", "full"];

    function post(url, body) {
        return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(body || {}) })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
    }

    function layout() {
        return Array.prototype.map.call(grid.querySelectorAll("[data-widget]"), function (card) {
            return { id: card.dataset.widget, size: card.dataset.size };
        });
    }
    function save() { return post("/api/dashboard", { layout: layout() }); }

    // ---- customise mode -------------------------------------------------------
    function setEditing(on) {
        document.body.dataset.editing = on ? "1" : "";      // live.js holds reloads
        grid.classList.toggle("editing", on);
        tray.hidden = !on;
        resetBtn.hidden = !on;
        editBtn.textContent = on ? "Done" : "Customise";
        grid.querySelectorAll("[data-widget]").forEach(function (c) { c.draggable = on; });
        if (!on) { window.location.reload(); }                // widgets fetch fresh data
    }
    editBtn.addEventListener("click", function () { setEditing(!grid.classList.contains("editing")); });
    if (window.location.hash === "#customise") {            // from the command palette
        history.replaceState(null, "", window.location.pathname);
        setEditing(true);
    }
    resetBtn.addEventListener("click", function () {
        if (!window.confirm("Put the home screen back to the default widgets?")) { return; }
        post("/api/dashboard/reset").then(function () { document.body.dataset.editing = ""; window.location.reload(); });
    });

    grid.addEventListener("click", function (e) {
        var card = e.target.closest("[data-widget]");
        if (!card || !grid.classList.contains("editing")) { return; }
        if (e.target.closest("[data-remove]")) {
            var id = card.dataset.widget;
            card.remove();
            var item = tray.querySelector('[data-add-widget="' + id + '"]');
            if (item) { item.hidden = false; }
            save();
        } else if (e.target.closest("[data-move]")) {
            var dir = Number(e.target.closest("[data-move]").dataset.move);
            var sibling = dir < 0 ? card.previousElementSibling : card.nextElementSibling;
            if (sibling) {
                if (dir < 0) { grid.insertBefore(card, sibling); } else { grid.insertBefore(sibling, card); }
                save();
            }
        } else if (e.target.closest("[data-resize]")) {
            var next = SIZES[(SIZES.indexOf(card.dataset.size) + 1) % SIZES.length];
            card.classList.remove("dash-" + card.dataset.size);
            card.dataset.size = next;
            card.classList.add("dash-" + next);
            save();
        }
    });

    tray.addEventListener("click", function (e) {
        var item = e.target.closest("[data-add-widget]");
        if (!item) { return; }
        var list = layout();
        list.push({ id: item.dataset.addWidget, size: item.dataset.size });
        post("/api/dashboard", { layout: list }).then(function () {
            window.location.reload();       // render the new widget with its data
        });
    });

    // Drag and drop (desktop).
    var dragging = null;
    grid.addEventListener("dragstart", function (e) {
        dragging = e.target.closest("[data-widget]");
        if (dragging) { dragging.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; }
    });
    grid.addEventListener("dragover", function (e) {
        if (!dragging) { return; }
        e.preventDefault();
        var over = e.target.closest("[data-widget]");
        if (!over || over === dragging) { return; }
        var box = over.getBoundingClientRect();
        var after = (e.clientY - box.top) > box.height / 2 || (e.clientX - box.left) > box.width * 0.66;
        grid.insertBefore(dragging, after ? over.nextSibling : over);
    });
    grid.addEventListener("dragend", function () {
        if (dragging) { dragging.classList.remove("dragging"); dragging = null; save(); }
    });

    // ---- widget behaviour ---------------------------------------------------------
    document.addEventListener("click", function (e) {
        var sim = e.target.closest("[data-simulate-read]");
        if (sim) {
            sim.disabled = true;
            fetch("/api/reader/simulate", { method: "POST" }).then(function () { sim.disabled = false; });
            return;   // live.js reloads the page when the read lands
        }
        var action = e.target.closest("[data-post]");
        if (action) {
            var label = action.textContent;
            action.disabled = true;
            post(action.dataset.post).then(function (res) {
                action.disabled = false;
                action.textContent = res.ok ? "Done ✓" : (res.d.error || "Failed");
                setTimeout(function () { action.textContent = label; }, 2500);
            });
        }
    });

    var notes = document.querySelector("[data-notes]");
    if (notes) {
        var status = document.querySelector("[data-notes-status]");
        var timer = null;
        notes.addEventListener("focus", function () { document.body.dataset.editing = "1"; });
        notes.addEventListener("input", function () {
            status.textContent = "Typing…";
            clearTimeout(timer);
            timer = setTimeout(saveNotes, 800);
        });
        notes.addEventListener("blur", function () {
            clearTimeout(timer);
            saveNotes().then(function () {
                if (!grid.classList.contains("editing")) { document.body.dataset.editing = ""; }
            });
        });
        function saveNotes() {
            return post("/api/dashboard/notes", { text: notes.value }).then(function (res) {
                status.textContent = res.ok ? "Saved with this event." : (res.d.error || "Could not save");
            });
        }
    }

    document.querySelectorAll("[data-countdown]").forEach(function (el) {
        var left = Number(el.dataset.countdown);
        function tick() {
            if (left < 0) { el.textContent = "now"; return; }
            var m = Math.floor(left / 60), s = left % 60;
            el.textContent = (m ? m + "m " : "") + s + "s";
            left -= 1;
        }
        tick();
        setInterval(tick, 1000);
    });
})();
