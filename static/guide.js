/*
 * Run-an-event guide sidebar. A non-modal side panel toggled from the topbar
 * "Guide" button: it stays open while you navigate so you can follow the steps
 * as you work. Open/closed state and per-step "done" ticks persist in
 * localStorage (no server state). The steps themselves come from guide.py.
 */
(function () {
    "use strict";
    var panel = document.querySelector("[data-guide-panel]");
    if (!panel) { return; }

    var OPEN_KEY = "pc-guide-open";
    var DONE_KEY = "pc-guide-done";

    function load(key, fallback) {
        try { var v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); }
        catch (e) { return fallback; }
    }
    function save(key, value) {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
    }

    function setOpen(open) {
        panel.classList.toggle("open", open);
        panel.setAttribute("aria-hidden", open ? "false" : "true");
        document.body.classList.toggle("guide-open", open);
        save(OPEN_KEY, open);
    }

    // Restore open state + ticks.
    var done = load(DONE_KEY, []);
    if (!Array.isArray(done)) { done = []; }
    document.querySelectorAll(".guide-step").forEach(function (li) {
        var box = li.querySelector("[data-step-check]");
        if (!box) { return; }
        var isDone = done.indexOf(li.dataset.step) !== -1;
        box.checked = isDone;
        li.classList.toggle("done", isDone);
        box.addEventListener("change", function () {
            li.classList.toggle("done", box.checked);
            var set = load(DONE_KEY, []);
            if (!Array.isArray(set)) { set = []; }
            var i = set.indexOf(li.dataset.step);
            if (box.checked && i === -1) { set.push(li.dataset.step); }
            else if (!box.checked && i !== -1) { set.splice(i, 1); }
            save(DONE_KEY, set);
        });
    });

    setOpen(load(OPEN_KEY, false) === true);

    document.querySelectorAll("[data-guide-toggle]").forEach(function (b) {
        b.addEventListener("click", function () { setOpen(!panel.classList.contains("open")); });
    });
    var closeBtn = panel.querySelector("[data-guide-close]");
    if (closeBtn) { closeBtn.addEventListener("click", function () { setOpen(false); }); }
})();
