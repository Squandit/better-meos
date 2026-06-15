/*
 * Operator editing: competitor drawer, class + course modals, and the CRUD
 * calls behind them. Everything talks to the JSON API in app.py; after a
 * successful create/update/delete the page reloads so the server re-ranks and
 * re-renders (positions, stats and leaders all stay correct). A flash message
 * is stashed across the reload and shown as a toast.
 *
 * This file is loaded on every page and guards each feature on the presence of
 * its root element, so pages without an editor simply do nothing here.
 */
(function () {
    "use strict";

    var $ = function (sel, root) { return (root || document).querySelector(sel); };
    var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

    // ---- API + feedback ---------------------------------------------------

    function api(method, url, body) {
        var opts = { method: method, headers: {} };
        if (body !== undefined) {
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(body);
        }
        return fetch(url, opts).then(function (res) {
            return res.json().catch(function () { return null; }).then(function (data) {
                if (!res.ok) {
                    var msg = (data && data.error) || ("Request failed (" + res.status + ")");
                    throw new Error(msg);
                }
                return data;
            });
        });
    }

    var toastHost = $("[data-toasts]");
    function toast(message, kind) {
        if (!toastHost) { return; }
        var el = document.createElement("div");
        el.className = "toast toast-" + (kind || "ok");
        el.textContent = message;
        toastHost.appendChild(el);
        requestAnimationFrame(function () { el.classList.add("show"); });
        setTimeout(function () {
            el.classList.remove("show");
            setTimeout(function () { el.remove(); }, 250);
        }, 3200);
    }

    function reloadWith(message, kind) {
        try { sessionStorage.setItem("bm-flash", JSON.stringify({ message: message, kind: kind || "ok" })); } catch (e) {}
        window.location.reload();
    }

    (function popFlash() {
        var raw;
        try { raw = sessionStorage.getItem("bm-flash"); } catch (e) { return; }
        if (!raw) { return; }
        try { sessionStorage.removeItem("bm-flash"); } catch (e) {}
        try { var f = JSON.parse(raw); toast(f.message, f.kind); } catch (e) {}
    })();

    function confirmAction(message) { return window.confirm(message); }

    // ---- Overlay (drawer / modal) management ------------------------------

    var backdrop = $("[data-backdrop]");
    var currentOverlay = null;
    var lastFocus = null;

    function openOverlay(el) {
        if (currentOverlay && currentOverlay !== el) { hideOverlay(currentOverlay); }
        lastFocus = document.activeElement;
        currentOverlay = el;
        if (backdrop) { backdrop.hidden = false; }
        document.body.classList.add("overlay-open");
        el.classList.add("open");
        el.setAttribute("aria-hidden", "false");
        var first = el.querySelector("input, select, textarea, button");
        if (first) { setTimeout(function () { first.focus(); }, 60); }
    }

    function hideOverlay(el) {
        el.classList.remove("open");
        el.setAttribute("aria-hidden", "true");
    }

    function closeOverlay() {
        if (!currentOverlay) { return; }
        hideOverlay(currentOverlay);
        currentOverlay = null;
        if (backdrop) { backdrop.hidden = true; }
        document.body.classList.remove("overlay-open");
        if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
    }

    if (backdrop) { backdrop.addEventListener("click", closeOverlay); }
    document.addEventListener("click", function (e) {
        if (e.target.closest("[data-close]")) { closeOverlay(); }
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && currentOverlay) { closeOverlay(); }
    });

    function showError(form, message) {
        var box = $("[data-form-error]", form);
        if (box) { box.textContent = message; box.hidden = false; }
    }
    function clearError(form) {
        var box = $("[data-form-error]", form);
        if (box) { box.hidden = true; box.textContent = ""; }
    }

    // ---- Time helper ------------------------------------------------------

    function clockToSeconds(text) {
        var m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec((text || "").trim());
        if (!m) { return null; }
        return (+m[1]) * 3600 + (+m[2]) * 60 + (+(m[3] || 0));
    }

    // =======================================================================
    // Competitor editor (drawer)
    // =======================================================================

    (function competitorEditor() {
        var drawer = $("#competitor-drawer");
        var list = $("[data-competitors]");
        if (!drawer) { return; }

        var form = $("[data-competitor-form]", drawer);
        var titleEl = $("[data-drawer-title]", drawer);
        var subEl = $("[data-drawer-sub]", drawer);
        var punchBody = $("[data-punch-body]", drawer);
        var punchEmpty = $("[data-punch-empty]", drawer);
        var punchTemplate = $("[data-punch-template]");
        var deleteCurrentBtn = $("[data-delete-current]", drawer);
        var previewTimer = null;
        var editId = null;

        function field(name) { return form.elements[name]; }

        // --- punch rows ---
        function updatePunchEmpty() {
            var has = punchBody.children.length > 0;
            if (punchEmpty) { punchEmpty.hidden = has; }
        }

        function addPunchRow(code, time) {
            var row = punchTemplate.content.firstElementChild.cloneNode(true);
            $(".punch-code", row).value = (code === undefined || code === null) ? "" : code;
            $(".punch-time", row).value = time || "";
            $("[data-remove]", row).addEventListener("click", function () {
                row.remove(); updatePunchEmpty(); schedulePreview();
            });
            $("[data-up]", row).addEventListener("click", function () { moveRow(row, -1); });
            $("[data-down]", row).addEventListener("click", function () { moveRow(row, 1); });
            punchBody.appendChild(row);
            updatePunchEmpty();
        }

        function moveRow(row, dir) {
            if (dir < 0 && row.previousElementSibling) {
                row.parentNode.insertBefore(row, row.previousElementSibling);
            } else if (dir > 0 && row.nextElementSibling) {
                row.parentNode.insertBefore(row.nextElementSibling, row);
            }
            schedulePreview();
        }

        function readPunches() {
            return $$(".punch-row", punchBody).map(function (r) {
                return { code: $(".punch-code", r).value.trim(), time: $(".punch-time", r).value.trim() };
            }).filter(function (p) { return p.code !== "" || p.time !== ""; });
        }

        $("[data-add-punch]", drawer).addEventListener("click", function () {
            addPunchRow("", "");
            var rows = $$(".punch-row", punchBody);
            var last = rows[rows.length - 1];
            if (last) { $(".punch-code", last).focus(); }
        });

        $("[data-sort-punches]", drawer).addEventListener("click", function () {
            var rows = readPunches();
            rows.sort(function (a, b) {
                var sa = clockToSeconds(a.time), sb = clockToSeconds(b.time);
                if (sa === null) { return 1; }
                if (sb === null) { return -1; }
                return sa - sb;
            });
            punchBody.innerHTML = "";
            rows.forEach(function (p) { addPunchRow(p.code, p.time); });
            updatePunchEmpty();
            schedulePreview();
        });

        // --- gather + preview ---
        function gather() {
            return {
                id: editId,
                name: field("name").value,
                club: field("club").value,
                class_id: field("class_id").value,
                card_number: field("card_number").value,
                start: field("start").value,
                finish: field("finish").value,
                manual_status: field("manual_status").value,
                bib: field("bib") ? field("bib").value : "",
                hired: field("hired") ? field("hired").checked : false,
                team_id: field("team_id") ? field("team_id").value : "",
                leg: field("leg") ? field("leg").value : "",
                time_adjustment: field("time_adjustment") ? field("time_adjustment").value : "",
                credit: field("credit") ? field("credit").value : "",
                not_competing: field("not_competing") ? field("not_competing").checked : false,
                vacant: field("vacant") ? field("vacant").checked : false,
                punches: readPunches()
            };
        }

        // Relay team picker: shown only when the chosen class has teams.
        var teamField = $("[data-team-field]", drawer);
        var legField = $("[data-leg-field]", drawer);
        function refreshTeams(classId, selectedTeamId) {
            var sel = field("team_id");
            if (!sel || !classId) {
                if (teamField) { teamField.hidden = true; }
                if (legField) { legField.hidden = true; }
                return;
            }
            api("GET", "/api/teams?class_id=" + encodeURIComponent(classId)).then(function (teams) {
                if (!teams || !teams.length) {
                    teamField.hidden = true; legField.hidden = true; return;
                }
                sel.innerHTML = '<option value="">— none —</option>' + teams.map(function (t) {
                    return '<option value="' + t.id + '">' + t.name + '</option>';
                }).join("");
                if (selectedTeamId) { sel.value = selectedTeamId; }
                teamField.hidden = false; legField.hidden = false;
            }).catch(function () {});
        }
        var classSel = field("class_id");
        if (classSel) {
            classSel.addEventListener("change", function () { refreshTeams(classSel.value); });
        }

        function schedulePreview() {
            clearTimeout(previewTimer);
            previewTimer = setTimeout(refreshPreview, 250);
        }

        function refreshPreview() {
            var payload = gather();
            if (!payload.class_id) { renderPreview(null); return; }
            // Only evaluate fully-entered punches so a half-typed row (code but
            // no time yet) doesn't flash a validation error during editing.
            payload.punches = payload.punches.filter(function (p) {
                return p.code !== "" && p.time !== "";
            });
            api("POST", "/api/preview", payload)
                .then(function (d) { renderPreview(d.result, null); })
                .catch(function (err) { renderPreview(null, err.message); });
        }

        function renderPreview(result, errorMsg) {
            var badge = $("[data-preview-badge]", drawer);
            var figure = $("[data-preview-figure]", drawer);
            var auto = $("[data-preview-auto]", drawer);
            var note = $("[data-preview-note]", drawer);
            var splits = $("[data-preview-splits]", drawer);

            if (errorMsg) {
                badge.className = "preview-badge muted"; badge.textContent = "—";
                figure.textContent = "";
                auto.hidden = true;
                note.hidden = false; note.className = "preview-note warn"; note.textContent = errorMsg;
                splits.hidden = true;
                return;
            }
            if (!result) {
                badge.className = "preview-badge muted"; badge.textContent = "—";
                figure.textContent = "Pick a class to evaluate.";
                auto.hidden = true; note.hidden = true; splits.hidden = true;
                return;
            }

            badge.className = "preview-badge badge badge-" + result.status;
            badge.textContent = result.status_label;

            var parts = [];
            if (result.course_type === "score" && result.points !== null && result.points !== undefined) {
                parts.push(result.points + " pts");
            }
            if (result.time) { parts.push(result.time); }
            if (result.position) { parts.push("position " + result.position); }
            figure.textContent = parts.join("  ·  ");

            if (result.manual && result.auto_status_label) {
                auto.hidden = false;
                auto.textContent = "override · would be " + result.auto_status_label;
            } else {
                auto.hidden = true;
            }

            if (result.missed_control) {
                note.hidden = false; note.className = "preview-note mp";
                note.textContent = "Mispunch — first missed control " + result.missed_control;
            } else {
                note.hidden = true;
            }

            var body = $("tbody", splits);
            if (result.splits && result.splits.length) {
                body.innerHTML = "";
                result.splits.forEach(function (s) {
                    var tr = document.createElement("tr");
                    tr.innerHTML = "<td>" + s.control + "</td><td>" + s.leg + "</td><td>" + s.cumulative + "</td>";
                    body.appendChild(tr);
                });
                splits.hidden = false;
            } else {
                splits.hidden = true;
            }
        }

        form.addEventListener("input", schedulePreview);
        form.addEventListener("change", schedulePreview);

        // SI-read autofill: typing/reading a known card fills name, club and the
        // person's usual class from the shared runner database.
        var cardField = field("card_number");
        if (cardField) {
            cardField.addEventListener("change", function () {
                var card = cardField.value.trim();
                if (!card) { return; }
                api("GET", "/api/runners/lookup?card=" + encodeURIComponent(card))
                    .then(function (d) {
                        if (!d) { return; }
                        if (!field("name").value) { field("name").value = d.name || ""; }
                        if (!field("club").value) { field("club").value = d.club || ""; }
                        if (d.class_id && !field("class_id").value) { field("class_id").value = d.class_id; }
                        schedulePreview();
                    })
                    .catch(function () {});
            });
        }

        // --- open / populate ---
        function openNew() {
            editId = null;
            form.reset();
            clearError(form);
            punchBody.innerHTML = "";
            updatePunchEmpty();
            titleEl.textContent = "New competitor";
            subEl.textContent = "Enter a competitor and their punches";
            if (deleteCurrentBtn) { deleteCurrentBtn.hidden = true; }
            refreshTeams(null);
            renderPreview(null);
            openOverlay(drawer);
            refreshPreview();
        }

        function openEdit(id) {
            api("GET", "/api/competitors/" + id).then(function (d) {
                var c = d.competitor;
                editId = c.id;
                clearError(form);
                field("name").value = c.name || "";
                field("club").value = c.club || "";
                field("class_id").value = c.class_id;
                field("card_number").value = c.card_number == null ? "" : c.card_number;
                field("start").value = c.start || "";
                field("finish").value = c.finish || "";
                field("manual_status").value = c.manual_status || "";
                if (field("bib")) { field("bib").value = c.bib == null ? "" : c.bib; }
                if (field("hired")) { field("hired").checked = !!c.hired; }
                if (field("leg")) { field("leg").value = c.leg == null ? "" : c.leg; }
                if (field("time_adjustment")) { field("time_adjustment").value = c.time_adjustment || ""; }
                if (field("credit")) { field("credit").value = c.credit || ""; }
                if (field("not_competing")) { field("not_competing").checked = !!c.not_competing; }
                if (field("vacant")) { field("vacant").checked = !!c.vacant; }
                refreshTeams(c.class_id, c.team_id);
                punchBody.innerHTML = "";
                (c.punches || []).forEach(function (p) { addPunchRow(p.code, p.time); });
                updatePunchEmpty();
                titleEl.textContent = "Edit competitor";
                subEl.textContent = c.name + (c.class_name ? " · " + c.class_name : "");
                if (deleteCurrentBtn) { deleteCurrentBtn.hidden = false; }
                renderPreview(d.result);
                openOverlay(drawer);
            }).catch(function (err) { toast(err.message, "error"); });
        }

        // --- save / delete ---
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            clearError(form);
            var payload = gather();
            var saveBtn = $("[data-save]", drawer);
            saveBtn.disabled = true;
            var req = editId
                ? api("PUT", "/api/competitors/" + editId, payload)
                : api("POST", "/api/competitors", payload);
            req.then(function () {
                reloadWith((editId ? "Saved " : "Added ") + (payload.name || "competitor"));
            }).catch(function (err) {
                saveBtn.disabled = false;
                showError(form, err.message);
            });
        });

        function deleteCompetitor(id, name) {
            if (!confirmAction("Delete " + (name || "this competitor") + "? This cannot be undone.")) { return; }
            api("DELETE", "/api/competitors/" + id)
                .then(function () { reloadWith("Deleted " + (name || "competitor")); })
                .catch(function (err) { toast(err.message, "error"); });
        }

        if (deleteCurrentBtn) {
            deleteCurrentBtn.addEventListener("click", function () {
                if (editId) { deleteCompetitor(editId, field("name").value); }
            });
        }

        // --- list wiring ---
        var newBtn = $("[data-new-competitor]");
        if (newBtn) { newBtn.addEventListener("click", openNew); }

        if (list) {
            list.addEventListener("click", function (e) {
                var row = e.target.closest(".entry-row");
                if (!row) { return; }
                if (e.target.closest("[data-delete]")) {
                    e.stopPropagation();
                    deleteCompetitor(row.dataset.id, e.target.closest("[data-delete]").dataset.name);
                    return;
                }
                openEdit(row.dataset.id);
            });
            list.addEventListener("keydown", function (e) {
                var row = e.target.closest(".entry-row");
                if (row && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault();
                    openEdit(row.dataset.id);
                }
            });
        }

        // Deep links: /competitors#new opens a blank entry, #edit/<id> opens one.
        (function fromHash() {
            var h = decodeURIComponent(window.location.hash);
            var m;
            if (h === "#new") {
                history.replaceState(null, "", window.location.pathname + window.location.search);
                openNew();
            } else if ((m = /^#edit\/(\d+)$/.exec(h))) {
                history.replaceState(null, "", window.location.pathname + window.location.search);
                openEdit(m[1]);
            }
        })();
    })();

    // =======================================================================
    // Class editor (modal)
    // =======================================================================

    (function classEditor() {
        var modal = $("#class-modal");
        if (!modal) { return; }

        var form = $("[data-class-form]", modal);
        var titleEl = $("[data-modal-title]", modal);
        var editId = null;

        function openNew() {
            editId = null;
            form.reset();
            clearError(form);
            titleEl.textContent = "New class";
            openOverlay(modal);
        }

        function openEdit(row) {
            editId = row.dataset.id;
            clearError(form);
            form.elements.name.value = row.dataset.name || "";
            if (row.dataset.courseId) { form.elements.course_id.value = row.dataset.courseId; }
            if (form.elements.kind) { form.elements.kind.value = row.dataset.kind || "individual"; }
            if (form.elements.legs) { form.elements.legs.value = row.dataset.legs || "1"; }
            if (form.elements.fee) { form.elements.fee.value = row.dataset.fee || ""; }
            titleEl.textContent = "Edit class";
            openOverlay(modal);
        }

        form.addEventListener("submit", function (e) {
            e.preventDefault();
            clearError(form);
            var payload = {
                name: form.elements.name.value, course_id: form.elements.course_id.value,
                kind: form.elements.kind ? form.elements.kind.value : "individual",
                legs: form.elements.legs ? form.elements.legs.value : 1,
                fee: form.elements.fee ? form.elements.fee.value : 0
            };
            var req = editId
                ? api("PUT", "/api/classes/" + editId, payload)
                : api("POST", "/api/classes", payload);
            req.then(function () { reloadWith((editId ? "Saved class " : "Added class ") + payload.name); })
               .catch(function (err) { showError(form, err.message); });
        });

        var newBtn = $("[data-new-class]");
        if (newBtn) { newBtn.addEventListener("click", openNew); }

        document.addEventListener("click", function (e) {
            var editBtn = e.target.closest("[data-edit-class]");
            if (editBtn) { openEdit(editBtn.closest("[data-class-row]")); return; }
            var delBtn = e.target.closest("[data-delete-class]");
            if (delBtn) {
                var row = delBtn.closest("[data-class-row]");
                if (!confirmAction("Delete class \"" + row.dataset.name + "\"?")) { return; }
                api("DELETE", "/api/classes/" + row.dataset.id)
                    .then(function () { reloadWith("Deleted class " + row.dataset.name); })
                    .catch(function (err) { toast(err.message, "error"); });
                return;
            }
            // Click anywhere on the row (not a button/link) opens the editor.
            var clsRow = e.target.closest("[data-class-row]");
            if (clsRow && !e.target.closest("button, a, input, select, label")) {
                openEdit(clsRow);
            }
        });

        if (window.location.hash === "#new") {
            history.replaceState(null, "", window.location.pathname + window.location.search);
            openNew();
        }
    })();

    // =======================================================================
    // Course editor (modal)
    // =======================================================================

    (function courseEditor() {
        var modal = $("#course-modal");
        if (!modal) { return; }

        var form = $("[data-course-form]", modal);
        var titleEl = $("[data-modal-title]", modal);
        var body = $("[data-control-body]", modal);
        var empty = $("[data-control-empty]", modal);
        var heading = $("[data-controls-heading]", modal);
        var scoreOnly = $("[data-score-only]", modal);
        var linearHead = $("[data-linear-head]", modal);
        var scoreHead = $("[data-score-head]", modal);
        var linearTmpl = $("[data-linear-row-template]");
        var scoreTmpl = $("[data-score-row-template]");
        var editId = null;
        var currentType = "linear";

        function updateEmpty() { if (empty) { empty.hidden = body.children.length > 0; } }

        function renumber() {
            if (currentType !== "linear") { return; }
            $$(".control-edit-row", body).forEach(function (row, i) {
                var seq = $("[data-seq]", row);
                if (seq) { seq.textContent = i + 1; }
            });
        }

        function wireRow(row) {
            $("[data-remove]", row).addEventListener("click", function () {
                row.remove(); renumber(); updateEmpty();
            });
            $("[data-up]", row).addEventListener("click", function () {
                if (row.previousElementSibling) {
                    row.parentNode.insertBefore(row, row.previousElementSibling);
                    renumber();
                }
            });
            $("[data-down]", row).addEventListener("click", function () {
                if (row.nextElementSibling) {
                    row.parentNode.insertBefore(row.nextElementSibling, row);
                    renumber();
                }
            });
        }

        function addRow(type, code, points) {
            var tmpl = type === "score" ? scoreTmpl : linearTmpl;
            var row = tmpl.content.firstElementChild.cloneNode(true);
            $(".ctl-code", row).value = (code === undefined || code === null) ? "" : code;
            var pts = $(".ctl-points", row);
            if (pts) { pts.value = (points === undefined || points === null) ? "" : points; }
            wireRow(row);
            body.appendChild(row);
            renumber();
            updateEmpty();
        }

        function readRows() {
            return $$(".control-edit-row", body).map(function (r) {
                var pts = $(".ctl-points", r);
                return { code: $(".ctl-code", r).value.trim(), points: pts ? pts.value.trim() : "" };
            });
        }

        function parseVariants(text) {
            return (text || "").split(/\r?\n/).map(function (line) {
                return line.split(",").map(function (s) { return s.trim(); })
                    .filter(function (s) { return s !== ""; }).map(Number);
            }).filter(function (seq) { return seq.length > 0; });
        }

        function applyTypeUI(type) {
            currentType = type;
            scoreOnly.hidden = type !== "score";
            $$("[data-linear-only]", modal).forEach(function (el) { el.hidden = type === "score"; });
            linearHead.hidden = type === "score";
            scoreHead.hidden = type !== "score";
            heading.textContent = type === "score" ? "Controls & points" : "Controls (in order)";
            var radio = form.querySelector('input[name="type"][value="' + type + '"]');
            if (radio) { radio.checked = true; }
        }

        function rebuild(type, items) {
            body.innerHTML = "";
            (items && items.length ? items : [{}]).forEach(function (it) {
                addRow(type, it.code, it.points);
            });
            updateEmpty();
        }

        $$('input[name="type"]', form).forEach(function (radio) {
            radio.addEventListener("change", function () {
                if (!radio.checked) { return; }
                var existing = readRows();
                applyTypeUI(radio.value);
                rebuild(radio.value, existing);
            });
        });

        $("[data-add-control]", modal).addEventListener("click", function () {
            addRow(currentType, "", "");
            var rows = $$(".control-edit-row", body);
            var last = rows[rows.length - 1];
            if (last) { $(".ctl-code", last).focus(); }
        });

        function gather() {
            var rows = readRows();
            var payload = { name: form.elements.name.value, type: currentType };
            if (currentType === "score") {
                payload.controls = rows.filter(function (r) { return r.code !== ""; })
                    .map(function (r) { return { code: r.code, points: r.points }; });
                payload.time_limit_minutes = form.elements.time_limit_minutes.value;
                payload.penalty_per_minute = form.elements.penalty_per_minute.value;
                payload.score_formula = form.elements.score_formula ? form.elements.score_formula.value : "";
            } else {
                payload.controls = rows.filter(function (r) { return r.code !== ""; })
                    .map(function (r) { return r.code; });
                payload.start_mode = form.elements.start_mode ? form.elements.start_mode.value : "clock";
                payload.start_control = form.elements.start_control ? form.elements.start_control.value : "";
                payload.mass_start = form.elements.mass_start ? form.elements.mass_start.value : "";
                payload.length_m = form.elements.length_m ? form.elements.length_m.value : "";
                payload.variants = parseVariants(form.elements.variants ? form.elements.variants.value : "");
            }
            return payload;
        }

        function openNew() {
            editId = null;
            form.reset();
            clearError(form);
            applyTypeUI("linear");
            rebuild("linear", [{}]);
            titleEl.textContent = "New course";
            openOverlay(modal);
        }

        function openEdit(id) {
            api("GET", "/api/courses/" + id).then(function (d) {
                var c = d.course;
                editId = c.id;
                form.reset();
                clearError(form);
                form.elements.name.value = c.name || "";
                if (form.elements.start_mode) { form.elements.start_mode.value = c.start_mode || "clock"; }
                if (form.elements.start_control) { form.elements.start_control.value = c.start_control == null ? "" : c.start_control; }
                if (form.elements.mass_start) { form.elements.mass_start.value = c.mass_start || ""; }
                if (form.elements.length_m) { form.elements.length_m.value = c.length_m == null ? "" : c.length_m; }
                applyTypeUI(c.type);
                if (c.type === "score") {
                    form.elements.time_limit_minutes.value = c.time_limit_minutes == null ? "" : c.time_limit_minutes;
                    form.elements.penalty_per_minute.value = c.penalty_per_minute == null ? "" : c.penalty_per_minute;
                    if (form.elements.score_formula) { form.elements.score_formula.value = c.score_formula || ""; }
                    rebuild("score", c.controls.map(function (ctl) { return { code: ctl.code, points: ctl.points }; }));
                } else {
                    rebuild("linear", c.controls.map(function (code) { return { code: code }; }));
                }
                if (form.elements.variants) {
                    form.elements.variants.value = (c.variants || []).map(function (v) {
                        return v.join(","); }).join("\n");
                }
                titleEl.textContent = "Edit course";
                openOverlay(modal);
            }).catch(function (err) { toast(err.message, "error"); });
        }

        form.addEventListener("submit", function (e) {
            e.preventDefault();
            clearError(form);
            var payload = gather();
            var req = editId
                ? api("PUT", "/api/courses/" + editId, payload)
                : api("POST", "/api/courses", payload);
            req.then(function () { reloadWith((editId ? "Saved course " : "Added course ") + payload.name); })
               .catch(function (err) { showError(form, err.message); });
        });

        var newBtn = $("[data-new-course]");
        if (newBtn) { newBtn.addEventListener("click", openNew); }

        document.addEventListener("click", function (e) {
            var editBtn = e.target.closest("[data-edit-course]");
            if (editBtn) { openEdit(editBtn.closest("[data-course-row]").dataset.id); return; }
            var delBtn = e.target.closest("[data-delete-course]");
            if (delBtn) {
                var row = delBtn.closest("[data-course-row]");
                if (!confirmAction("Delete course \"" + delBtn.dataset.name + "\"?")) { return; }
                api("DELETE", "/api/courses/" + row.dataset.id)
                    .then(function () { reloadWith("Deleted course " + delBtn.dataset.name); })
                    .catch(function (err) { toast(err.message, "error"); });
                return;
            }
            // Click anywhere on the course card (not a button/link) opens the editor.
            var courseRow = e.target.closest("[data-course-row]");
            if (courseRow && !e.target.closest("button, a, input, select, label")) {
                openEdit(courseRow.dataset.id);
            }
        });

        (function fromHash() {
            var h = decodeURIComponent(window.location.hash);
            var m;
            if (h === "#new") {
                history.replaceState(null, "", window.location.pathname + window.location.search);
                openNew();
            } else if ((m = /^#edit\/(\d+)$/.exec(h))) {
                history.replaceState(null, "", window.location.pathname + window.location.search);
                openEdit(m[1]);
            }
        })();
    })();
})();
