// Expand/collapse a runner's splits row (Results and Download pages).
document.querySelectorAll("tr.runner.has-splits").forEach(function (row) {
    function toggle() {
        var next = row.nextElementSibling;
        if (!next || !next.classList.contains("splits-row")) return;
        var open = row.classList.toggle("open");
        next.classList.toggle("open", open);
        row.setAttribute("aria-expanded", open ? "true" : "false");
    }
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
        }
    });
});

// Live text filter for any table inside a [data-filter] container.
document.querySelectorAll("[data-filter]").forEach(function (wrap) {
    var input = wrap.querySelector(".search");
    var rows = wrap.querySelectorAll("tbody tr");
    var counter = wrap.querySelector("[data-count]");
    if (!input) return;

    function apply() {
        var q = input.value.trim().toLowerCase();
        var shown = 0;
        rows.forEach(function (tr) {
            var match = tr.textContent.toLowerCase().indexOf(q) !== -1;
            tr.style.display = match ? "" : "none";
            if (match) shown++;
        });
        if (counter) counter.textContent = shown;
    }
    input.addEventListener("input", apply);
});

// Active-event switcher (top bar): switch the event server-side, then reload.
var switcher = document.querySelector("[data-event-switcher]");
if (switcher) {
    switcher.addEventListener("change", function () {
        fetch("/api/events/active", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ event_id: switcher.value })
        }).then(function (r) { if (r.ok) { window.location.reload(); } });
    });
}
