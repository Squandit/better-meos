/*
 * Settings UI shared by the master Settings page and each page's gear menu.
 *
 * The server describes every setting (type, choices, limits, where the value
 * comes from); this renders a row per setting and saves each change as it's
 * made (no Save button). Values are sent to POST /api/settings with a target:
 * "event" (this event), "computer" (defaults on this PC) or "auto".
 */
(function () {
    "use strict";

    var SOURCE_LABEL = {
        event: "This event",
        computer: "This computer",
        env: "Environment",
        "default": "Built-in default"
    };

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) { n.className = cls; }
        if (text !== undefined && text !== null) { n.textContent = text; }
        return n;
    }

    function post(target, values, reset) {
        return fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target: target, values: values || {}, reset: reset || [] })
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (d) {
                if (!r.ok) { throw new Error(d.error || "Could not save"); }
                return d;
            });
        });
    }

    function control(f) {
        var input;
        switch (f.type) {
            case "bool":
                var wrap = el("label", "switch");
                input = el("input");
                input.type = "checkbox";
                input.checked = !!f.value;
                wrap.appendChild(input);
                wrap.appendChild(el("span", "switch-track"));
                return { node: wrap, input: input, read: function () { return input.checked; }, instant: true };
            case "choice":
                input = el("select");
                f.choices.forEach(function (c) {
                    var o = el("option", null, c.label);
                    o.value = c.value;
                    input.appendChild(o);
                });
                input.value = f.value == null ? "" : String(f.value);
                return { node: input, input: input, read: function () { return input.value; }, instant: true };
            case "text":
                input = el("textarea");
                input.rows = 3;
                input.value = f.value == null ? "" : f.value;
                break;
            case "password":
                input = el("input");
                input.type = "password";
                input.autocomplete = "new-password";
                input.placeholder = f.is_set ? "•••••••• (set)" : "not set";
                break;
            case "int":
            case "float":
                input = el("input");
                input.type = "number";
                input.step = f.type === "int" ? "1" : "0.01";
                if (f.min != null) { input.min = f.min; }
                if (f.max != null) { input.max = f.max; }
                input.value = f.value == null ? "" : f.value;
                break;
            case "datetime":
                input = el("input");
                input.type = "datetime-local";
                input.value = f.value || "";
                return { node: input, input: input, read: function () { return input.value; }, instant: true };
            case "date":
                input = el("input");
                input.type = "date";
                input.value = f.value || "";
                return { node: input, input: input, read: function () { return input.value; }, instant: true };
            case "time":
                input = el("input");
                input.type = "text";
                input.className = "mono";
                input.placeholder = "10:00:00";
                input.value = f.value || "";
                break;
            default:
                input = el("input");
                input.type = "text";
                input.value = f.value == null ? "" : f.value;
        }
        return { node: input, input: input, read: function () { return input.value; }, instant: false };
    }

    // Extra buttons for particular settings.
    var HELPERS = {
        reader_ports: function (box, input) {
            var btn = el("button", "btn btn-soft btn-sm set-helper", "Detect SI stations");
            btn.type = "button";
            var list = el("div", "set-helper-list");
            btn.addEventListener("click", function () {
                list.textContent = "Looking…";
                fetch("/api/serial-ports").then(function (r) { return r.json(); }).then(function (ports) {
                    list.textContent = ports.length ? "" : "No serial ports found. Is the station plugged in?";
                    ports.forEach(function (p) {
                        var pick = el("button", "btn btn-ghost btn-sm",
                            p.device + (p.likely_si ? " (SI station)" : "") + " · " + p.description);
                        pick.type = "button";
                        pick.addEventListener("click", function () {
                            input.value = p.device;
                            input.dispatchEvent(new Event("input"));
                            input.dispatchEvent(new Event("blur"));
                        });
                        list.appendChild(pick);
                    });
                });
            });
            box.appendChild(btn);
            box.appendChild(list);
        },
        slip_printer: function (box, input) {
            var btn = el("button", "btn btn-soft btn-sm set-helper", "Choose printer");
            btn.type = "button";
            var test = el("button", "btn btn-ghost btn-sm set-helper", "Print a test slip");
            test.type = "button";
            var list = el("div", "set-helper-list");
            btn.addEventListener("click", function () {
                list.textContent = "Looking…";
                fetch("/api/printers").then(function (r) { return r.json(); }).then(function (d) {
                    list.textContent = d.printers.length ? "" : "No printers found (direct printing needs the Windows app).";
                    d.printers.forEach(function (name) {
                        var pick = el("button", "btn btn-ghost btn-sm", name + (name === d["default"] ? " (default)" : ""));
                        pick.type = "button";
                        pick.addEventListener("click", function () {
                            input.value = name;
                            input.dispatchEvent(new Event("input"));
                            input.dispatchEvent(new Event("blur"));
                        });
                        list.appendChild(pick);
                    });
                });
            });
            test.addEventListener("click", function () {
                list.textContent = "Printing…";
                fetch("/api/print/test", { method: "POST" })
                    .then(function (r) { return r.json(); })
                    .then(function (d) { list.textContent = d.error || "Test slip sent. Nothing came out? Check the Download page's printer line."; });
            });
            box.appendChild(btn);
            box.appendChild(test);
            box.appendChild(list);
        }
    };

    function row(f, opts) {
        var r = el("div", "set-row");
        r.dataset.key = f.key;
        var text = el("div", "set-text");
        var label = el("label", "set-label", f.label);
        text.appendChild(label);
        if (f.help) { text.appendChild(el("div", "set-help", f.help)); }
        var meta = el("div", "set-meta");
        var badge = el("span", "set-source set-source-" + f.source, SOURCE_LABEL[f.source] || f.source);
        badge.title = "Where this value comes from";
        meta.appendChild(badge);
        if (f.restart) { meta.appendChild(el("span", "set-restart", "restart to apply")); }
        var status = el("span", "set-status");
        meta.appendChild(status);
        // Reset: clear the value at this level so it falls back.
        // A saved password or key can't be shown, only removed (from this computer).
        var secret = f.type === "password";
        var resettable = secret ? f.source === "computer" && f.key !== "admin_password" :
                         (opts.target === "event" && f.source === "event") ||
                         (opts.target === "computer" && f.source === "computer");
        if (resettable) {
            var reset = el("button", "link-btn set-reset",
                           secret ? "Remove" : opts.target === "event" ? "Use default" : "Reset");
            reset.type = "button";
            reset.addEventListener("click", function () {
                status.textContent = "Saving…";
                post(secret ? "computer" : opts.target, {}, [f.key]).then(function () {
                    status.textContent = secret ? "Removed" : "Reset";
                    if (secret) { c.input.placeholder = "not set"; reset.remove(); }
                    if (APPEARANCE[f.key]) { refreshAppearance(f.key); }
                    opts.onChange && opts.onChange(f.key, true);
                }).catch(function (err) { status.className = "set-status err"; status.textContent = err.message; });
            });
            meta.appendChild(reset);
        }
        text.appendChild(meta);
        r.appendChild(text);

        var c = control(f);
        label.htmlFor = c.input.id = "set-" + f.key + "-" + Math.random().toString(36).slice(2, 7);
        var box = el("div", "set-control");
        box.appendChild(c.node);
        if (HELPERS[f.key]) { HELPERS[f.key](box, c.input); }
        r.appendChild(box);

        var last = c.read();
        var timer = null;
        function save() {
            var value = c.read();
            if (f.type === "password" && !value) { return; }
            if (value === last) { return; }
            status.className = "set-status";
            status.textContent = "Saving…";
            var values = {};
            values[f.key] = value;
            post(opts.target, values).then(function () {
                last = value;
                status.textContent = "Saved";
                if (APPEARANCE[f.key]) { document.documentElement.setAttribute(APPEARANCE[f.key], value); }
                if (f.type === "password") { c.input.value = ""; c.input.placeholder = "•••••••• (set)"; }
                badge.className = "set-source set-source-" + (opts.target === "event" && f.scope === "event" ? "event" : "computer");
                badge.textContent = SOURCE_LABEL[opts.target === "event" && f.scope === "event" ? "event" : "computer"];
                opts.onChange && opts.onChange(f.key, false);
            }).catch(function (err) {
                status.className = "set-status err";
                status.textContent = err.message;
            });
        }
        if (c.instant) {
            c.input.addEventListener("change", save);
        } else {
            c.input.addEventListener("input", function () {
                clearTimeout(timer);
                timer = setTimeout(save, 900);
            });
            c.input.addEventListener("blur", function () { clearTimeout(timer); save(); });
            c.input.addEventListener("keydown", function (e) {
                if (e.key === "Enter" && f.type !== "text") { e.preventDefault(); clearTimeout(timer); save(); }
            });
        }
        return r;
    }

    // Appearance settings take effect on this page straight away, not on the next load.
    var APPEARANCE = { theme: "data-theme", accent: "data-accent", density: "data-density", text_size: "data-text" };
    function refreshAppearance(key) {
        load({ q: key }).then(function (d) {
            d.groups.forEach(function (g) {
                g.fields.forEach(function (f) {
                    if (f.key === key) { document.documentElement.setAttribute(APPEARANCE[key], f.value); }
                });
            });
        });
    }

    /*
     * Render settings groups into a container.
     * opts: { target: "event"|"computer", onChange: fn(key, isReset), headings: bool }
     */
    function render(container, groups, opts) {
        container.innerHTML = "";
        if (!groups.length) {
            container.appendChild(el("p", "empty", "No settings match."));
            return;
        }
        groups.forEach(function (g) {
            var section = el("section", "set-group");
            section.id = "grp-" + g.group.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
            if (opts.headings !== false) { section.appendChild(el("h3", "set-group-title", g.group)); }
            g.fields.forEach(function (f) { section.appendChild(row(f, opts)); });
            container.appendChild(section);
        });
    }

    function load(params) {
        var q = Object.keys(params).filter(function (k) { return params[k]; })
            .map(function (k) { return k + "=" + encodeURIComponent(params[k]); }).join("&");
        return fetch("/api/settings" + (q ? "?" + q : ""), { cache: "no-store" })
            .then(function (r) { return r.json(); });
    }

    window.BMSettings = { render: render, load: load, post: post };

    // ---- Gear menu: this page's settings in a side drawer --------------------
    var gear = document.querySelector("[data-page-settings]");
    var drawer = document.querySelector("[data-settings-drawer]");
    if (!gear || !drawer) { return; }
    var body = drawer.querySelector("[data-settings-body]");
    var changed = false;

    function openDrawer() {
        changed = false;
        body.innerHTML = "<p class='empty'>Loading…</p>";
        drawer.hidden = false;
        requestAnimationFrame(function () { drawer.classList.add("open"); });
        load({ page: gear.dataset.pageSettings, target: "event" }).then(function (d) {
            render(body, d.groups, {
                target: d.event_open ? "event" : "computer",
                onChange: function () { changed = true; }
            });
        });
    }
    function closeDrawer() {
        drawer.classList.remove("open");
        setTimeout(function () { drawer.hidden = true; }, 180);
        if (changed) { window.location.reload(); }   // show the page with its new settings
    }
    gear.addEventListener("click", openDrawer);
    drawer.addEventListener("click", function (e) {
        if (e.target === drawer || e.target.closest("[data-settings-close]")) { closeDrawer(); }
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !drawer.hidden) { closeDrawer(); }
    });
})();
