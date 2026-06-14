from datetime import datetime


def format_duration(seconds: int) -> str:
    """Format seconds as H:MM:SS (a negative duration keeps a leading minus)."""
    seconds = int(seconds)
    sign = "-" if seconds < 0 else ""
    hours, remainder = divmod(abs(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"


def calculate_splits(
    start: datetime,
    punches: list[tuple[int, datetime]],
    finish: datetime,
) -> list[dict]:
    """
    Compute leg and cumulative splits for ordered punches.

    Returns one row per control punch plus a final row (control "F") for
    the leg from the last punch to finish.
    """
    if not punches:
        return []

    previous = start
    splits = []
    for code, timestamp in punches:
        splits.append({
            "control": code,
            "leg_seconds": int((timestamp - previous).total_seconds()),
            "cumulative_seconds": int((timestamp - start).total_seconds()),
        })
        previous = timestamp

    splits.append({
        "control": "F",
        "leg_seconds": int((finish - previous).total_seconds()),
        "cumulative_seconds": int((finish - start).total_seconds()),
    })
    return splits


def format_split(seconds: int | None) -> str:
    """
    Compact split time for the splits table: ``M:SS`` under an hour, ``H:MM:SS``
    over it. Blank for missing values. (``format_duration`` always pads to
    ``HH:MM:SS``, which is noisy in a dense grid of legs.)
    """
    if seconds is None:
        return ""
    seconds = int(seconds)
    sign = "-" if seconds < 0 else ""
    hours, remainder = divmod(abs(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{sign}{hours}:{minutes:02d}:{secs:02d}"
    return f"{sign}{minutes}:{secs:02d}"


def aligned_splits(start, punches, finish, required: list[int]) -> list[dict]:
    """
    Align a card's punches to a course's required control order.

    Returns one entry per required control plus a final Finish leg, each
    ``{control, leg_seconds, cumulative_seconds, missing}``. Matching is tolerant
    the same way :func:`validate_linear` is -- spurious/extra punches are skipped,
    and a control the runner missed doesn't stop later controls from matching (we
    don't advance the search cursor past the gap). A missed control (or the Finish
    when there's no finish punch) is ``missing=True`` with ``None`` times.

    Crucially this matches **positionally**, scanning forward for each required
    control in turn, so a course that legitimately repeats a control code
    (butterfly / loop courses) gets a correct, distinct split for each visit --
    something a ``{code: split}`` lookup cannot do.
    """
    entries = []
    search = 0          # index of the next punch to consider
    last_time = start   # time of the previous matched control (or start)

    for code in required:
        match_time = None
        j = search
        while j < len(punches):
            if punches[j][0] == code:
                match_time = punches[j][1]
                search = j + 1
                break
            j += 1
        if match_time is None:
            entries.append({"control": code, "leg_seconds": None,
                            "cumulative_seconds": None, "missing": True})
        else:
            entries.append({
                "control": code,
                "leg_seconds": int((match_time - last_time).total_seconds()),
                "cumulative_seconds": int((match_time - start).total_seconds()),
                "missing": False,
            })
            last_time = match_time

    if finish is not None:
        entries.append({"control": "F",
                        "leg_seconds": int((finish - last_time).total_seconds()),
                        "cumulative_seconds": int((finish - start).total_seconds()),
                        "missing": False})
    else:
        entries.append({"control": "F", "leg_seconds": None,
                        "cumulative_seconds": None, "missing": True})
    return entries


def _velocity(leg_seconds: int, length_m) -> str:
    """Pace as M:SS per km for a leg of ``length_m`` metres, or '' if unknown."""
    if not length_m or leg_seconds is None or leg_seconds <= 0:
        return ""
    return format_split(round(leg_seconds / (length_m / 1000.0)))


def build_splits_matrix(results: list[dict], controls: list[int],
                        leg_lengths: list | None = None) -> dict:
    """
    Build a side-by-side splits table for one linear class.

    ``results`` are ranked engine results (each carrying ``punches``, ``start``
    and ``finish``); ``controls`` is the course's ordered control codes. Columns
    are one per control plus a final Finish leg, aligned positionally via
    :func:`aligned_splits` so repeated controls and mispunches are handled
    correctly. When ``leg_lengths`` (metres per control leg) is given, each cell
    also carries a ``velocity`` (min/km).

    For every competitor and leg it reports the leg and cumulative time, the leg
    rank and split (cumulative) rank within the class, the time behind that leg's
    and split's leader, and a ``best_leg`` flag for the fastest leg. Pure: no
    mutation of the inputs.
    """
    legs = [{"code": c, "label": str(c)} for c in controls]
    legs.append({"code": "F", "label": "F"})

    # Each competitor's splits aligned to the column order (len == len(legs)).
    indexed = []
    for r in results:
        if r.get("start") is not None:
            aligned = aligned_splits(r["start"], r.get("punches", []),
                                     r.get("finish"), controls)
        else:  # DNS: no start, so no leg is computable
            aligned = [{"control": leg["code"], "leg_seconds": None,
                        "cumulative_seconds": None, "missing": True} for leg in legs]
        indexed.append((r, aligned))

    # Per-column fastest leg / split, used for ranks and time-behind.
    columns = []
    for i in range(len(legs)):
        leg_times, cum_times = [], []
        for _, aligned in indexed:
            cell = aligned[i]
            if not cell["missing"]:
                leg_times.append(cell["leg_seconds"])
                cum_times.append(cell["cumulative_seconds"])
        columns.append({
            "best_leg": min(leg_times) if leg_times else None,
            "best_cum": min(cum_times) if cum_times else None,
            "leg_times": sorted(leg_times),
            "cum_times": sorted(cum_times),
        })

    def rank(sorted_times: list[int], value: int) -> int:
        # 1-based rank; ties share the lower rank (1, 1, 3, ...).
        return sum(1 for x in sorted_times if x < value) + 1

    leg_lengths = leg_lengths or []
    rows = []
    for r, aligned in indexed:
        cells = []
        for i, col in enumerate(columns):
            cell = aligned[i]
            if cell["missing"]:
                cells.append({"missing": True})
                continue
            leg_sec, cum_sec = cell["leg_seconds"], cell["cumulative_seconds"]
            length = leg_lengths[i] if i < len(leg_lengths) else None
            cells.append({
                "missing": False,
                "leg": format_split(leg_sec),
                "cum": format_split(cum_sec),
                "leg_rank": rank(col["leg_times"], leg_sec),
                "cum_rank": rank(col["cum_times"], cum_sec),
                "best_leg": leg_sec == col["best_leg"],
                "leg_behind": format_split(leg_sec - col["best_leg"]),
                "cum_behind": format_split(cum_sec - col["best_cum"]),
                "velocity": _velocity(leg_sec, length),
            })
        rows.append({
            "id": r.get("id"),
            "name": r["name"],
            "club": r.get("club"),
            "position": r.get("position"),
            "status": r["status"],
            "total": format_split(r["total_seconds"]) if r["total_seconds"] is not None else "",
            "cells": cells,
        })
    return {"legs": legs, "rows": rows}


# ---------------------------------------------------------------------------
# Result building and ranking
# ---------------------------------------------------------------------------

# Result statuses. Linear courses produce OK/MP/DNS/DNF/DSQ; score courses
# produce OK/OOT/DNS/DNF/DSQ (no MP, since there is no required control order).
STATUS_OK = "ok"
STATUS_MP = "mp"
STATUS_DNS = "dns"
STATUS_DNF = "dnf"
STATUS_DSQ = "dsq"
STATUS_OOT = "oot"


def validate_linear(
    required: list[int],
    punched: list[int],
) -> tuple[str, int | None]:
    """
    Check punched control codes against a linear course's required order.

    Tolerant: extra or spurious punches are ignored. The course is valid as
    long as every required control appears, in order, as a subsequence of the
    punched codes.

    Returns (status, missed_control). missed_control is None when OK, otherwise
    the first required control that could not be found in order.
    """
    i = 0
    for code in punched:
        if i < len(required) and code == required[i]:
            i += 1
    if i == len(required):
        return STATUS_OK, None
    return STATUS_MP, required[i]


def score_points(control_values: dict, punched: list[int]) -> int:
    """
    Sum the point values of valid controls hit on a score course.

    Each control counts once regardless of repeat punches; codes that are not
    part of the course are worth nothing.
    """
    hit = set()
    total = 0
    for code in punched:
        if code in control_values and code not in hit:
            hit.add(code)
            total += control_values[code]
    return total


def build_result(card: dict, course: dict) -> dict:
    """
    Turn one raw card (MOCK_CARD_DATA shape) into a result for the given course.

    The automatic evaluation always runs first: DNS (no start punch), DNF (no
    finish punch), then course-specific validation. Score courses additionally
    apply an over-time (OOT) penalty. Total time and splits are filled in
    whenever the card has both a start and a finish.

    A manual status (operator override, e.g. DSQ) is applied last. It changes
    only the displayed status -- the recorded time, splits and points stay as
    computed, so a struck-out runner still shows their splits and an operator
    promoting a mispunch to OK still ranks on their real time. ``manual`` flags
    that an override is in force, and ``auto_status`` preserves what the engine
    would have said on its own.
    """
    result = {
        "id": card.get("id"),
        "name": card["name"],
        "class": card["class"],
        "club": card.get("club"),
        "card_number": card.get("card_number"),
        "course_type": course["type"],
        "start": card.get("start"),
        "finish": card.get("finish"),
        "status": STATUS_OK,
        "auto_status": STATUS_OK,
        "manual": False,
        "total_seconds": None,
        "points": None,
        "missed_control": None,
        "splits": [],
    }

    start = card.get("start")
    finish = card.get("finish")
    punches = card.get("punches") or []

    # Free / punch start: take the start time from a start-control punch rather
    # than the clock, and drop that punch so it isn't treated as a control leg.
    if course.get("start_mode") == "punch":
        sc = course.get("start_control")
        for i, (code, t) in enumerate(punches):
            if sc is None or code == sc:
                start = t
                punches = punches[:i] + punches[i + 1:]
                break

    punched_codes = [code for code, _ in punches]
    # Kept on the result so the splits matrix can align punches to the course
    # order itself (it needs the raw punch sequence, not just punch-order splits).
    result["punches"] = list(punches)
    result["start"] = start  # reflect a derived punch-start

    if start is None:
        auto_status = STATUS_DNS
    elif finish is None:
        auto_status = STATUS_DNF
    else:
        total_seconds = int((finish - start).total_seconds())
        result["total_seconds"] = total_seconds
        result["splits"] = calculate_splits(start, punches, finish)

        if course["type"] == "linear":
            auto_status, missed = validate_linear(course["controls"], punched_codes)
            result["missed_control"] = missed

        elif course["type"] == "score":
            auto_status = STATUS_OK
            points = score_points(course["controls"], punched_codes)
            limit = course.get("time_limit_minutes")
            if limit is not None and total_seconds > limit * 60:
                seconds_over = total_seconds - limit * 60
                # Round up: any part of a minute over the limit counts as a full one.
                minutes_over = (seconds_over + 59) // 60
                penalty = minutes_over * course.get("penalty_per_minute", 0)
                points = max(0, points - penalty)
                auto_status = STATUS_OOT
            result["points"] = points

        else:
            raise ValueError(f"Unknown course type: {course['type']!r}")

    result["auto_status"] = auto_status
    result["status"] = auto_status

    # Operator override, applied last: relabel only, keep the computed figures.
    manual = card.get("manual_status")
    if manual:
        result["status"] = manual
        result["manual"] = True

    return result


def rank_results(results: list[dict]) -> dict:
    """
    Group results by class and assign a position within each class.

    Linear classes rank OK results by time ascending. Score classes rank OK and
    OOT results by points descending, then time ascending. Equal keys share a
    position (1, 1, 3, ...); results with a non-ranked status (MP/DNS/DNF/DSQ)
    follow with position None. Input results are not mutated.
    """
    by_class: dict = {}
    for r in results:
        by_class.setdefault(r["class"], []).append(dict(r))

    for class_name, rows in by_class.items():
        # Prefer the course type carried on the result; fall back to the old
        # heuristic for any caller that builds results without it.
        if rows and rows[0].get("course_type"):
            is_score = rows[0]["course_type"] == "score"
        else:
            is_score = any(r["points"] is not None for r in rows)

        if is_score:
            ranked_statuses = {STATUS_OK, STATUS_OOT}
            key = lambda r: (-r["points"], r["total_seconds"])
            # A row can only be ranked if it actually has both figures; a manual
            # OK on a card with no finish, say, drops to the unranked tail.
            rankable = lambda r: (
                r["status"] in ranked_statuses
                and r["points"] is not None
                and r["total_seconds"] is not None
            )
        else:
            ranked_statuses = {STATUS_OK}
            key = lambda r: r["total_seconds"]
            rankable = lambda r: (
                r["status"] in ranked_statuses and r["total_seconds"] is not None
            )

        ranked = sorted((r for r in rows if rankable(r)), key=key)
        unranked = [r for r in rows if not rankable(r)]

        for idx, r in enumerate(ranked):
            if idx > 0 and key(r) == key(ranked[idx - 1]):
                r["position"] = ranked[idx - 1]["position"]
            else:
                r["position"] = idx + 1
        for r in unranked:
            r["position"] = None

        by_class[class_name] = ranked + unranked

    return by_class


# ---------------------------------------------------------------------------
# Example course definitions (mock scaffolding for the demo below)
# ---------------------------------------------------------------------------

MOCK_LINEAR_COURSE = {
    "type": "linear",
    "controls": [138, 130, 142, 155],            # required order
}

MOCK_SCORE_COURSE = {
    "type": "score",
    "time_limit_minutes": 60,
    "penalty_per_minute": 10,                     # pts deducted per started minute over
    "controls": {138: 30, 130: 30, 142: 40, 155: 50},   # code -> points
}


def mock_classes() -> list[dict]:
    """
    Demo roster shared by the terminal demo (_demo) and the web app.

    Returns a list of classes, each pairing a course definition with the cards
    that ran it. Card shape mirrors MOCK_CARD_DATA. Pure mock data -- no Flask,
    no database, no SI hardware.
    """
    def t(h: int, m: int, s: int) -> datetime:
        return datetime(2026, 5, 17, h, m, s)

    return [
        {
            "name": "M21A",
            "course": MOCK_LINEAR_COURSE,
            # Linear class on controls 138, 130, 142, 155.
            "cards": [
                {
                    "card_number": 8500001,
                    "name": "Fast Ferdy", "class": "M21A", "club": "TESTOC",
                    "start": t(9, 30, 0), "finish": t(10, 45, 0),
                    "punches": [(138, t(9, 40, 0)), (130, t(9, 43, 0)),
                                (142, t(10, 5, 0)), (155, t(10, 28, 0))],
                },
                {
                    "card_number": 8635918,
                    "name": "Test Runner", "class": "M21A", "club": "TESTOC",
                    "start": t(9, 27, 8), "finish": t(10, 49, 53),
                    "punches": [(138, t(9, 38, 27)), (130, t(9, 41, 29)),
                                (142, t(10, 5, 12)), (155, t(10, 30, 41))],
                },
                {
                    "card_number": 8500002,
                    "name": "Tie Tina", "class": "M21A", "club": "BUSHO",
                    "start": t(9, 40, 0), "finish": t(11, 2, 45),  # same total as Test Runner
                    "punches": [(138, t(9, 52, 0)), (130, t(9, 56, 0)),
                                (142, t(10, 22, 0)), (155, t(10, 48, 0))],
                },
                {
                    "card_number": 8500003,
                    "name": "Slow Sue", "class": "M21A", "club": "BUSHO",
                    "start": t(9, 35, 0), "finish": t(11, 5, 0),
                    "punches": [(138, t(9, 48, 0)), (130, t(9, 52, 0)),
                                (142, t(10, 20, 0)), (155, t(10, 50, 0))],
                },
                {
                    "card_number": 8500004,
                    "name": "Mispunch Mary", "class": "M21A", "club": "TESTOC",
                    "start": t(9, 45, 0), "finish": t(10, 55, 0),
                    "punches": [(138, t(9, 57, 0)), (130, t(10, 1, 0)),
                                (155, t(10, 40, 0))],  # skipped 142
                },
                {
                    "card_number": 8500005,
                    "name": "DNF Dan", "class": "M21A", "club": "BUSHO",
                    "start": t(9, 50, 0), "finish": None,  # never downloaded a finish
                    "punches": [(138, t(10, 2, 0)), (130, t(10, 6, 0))],
                },
                {
                    "card_number": 8500006,
                    "name": "DSQ Dave", "class": "M21A", "club": "TESTOC",
                    "start": t(9, 55, 0), "finish": t(11, 10, 0),
                    "punches": [(138, t(10, 7, 0)), (130, t(10, 11, 0)),
                                (142, t(10, 35, 0)), (155, t(11, 0, 0))],
                    "manual_status": "dsq",  # operator override
                },
            ],
        },
        {
            "name": "Score-O",
            "course": MOCK_SCORE_COURSE,
            # Score class, 60 min limit, 10 pts/min penalty.
            "cards": [
                {
                    "card_number": 8500010,
                    "name": "Score Sam", "class": "Score-O", "club": "TESTOC",
                    "start": t(13, 0, 0), "finish": t(13, 55, 0),  # 55 min, all controls
                    "punches": [(138, t(13, 10, 0)), (999, t(13, 20, 0)),  # 999 off-course
                                (130, t(13, 25, 0)), (142, t(13, 40, 0)), (155, t(13, 52, 0))],
                },
                {
                    "card_number": 8500011,
                    "name": "Score Carl", "class": "Score-O", "club": "BUSHO",
                    "start": t(13, 10, 0), "finish": t(14, 13, 30),  # 63:30, over by 3:30
                    "punches": [(138, t(13, 20, 0)), (130, t(13, 30, 0)),
                                (142, t(13, 50, 0)), (155, t(14, 10, 0))],
                },
                {
                    "card_number": 8500012,
                    "name": "Score Bob", "class": "Score-O", "club": "TESTOC",
                    "start": t(13, 5, 0), "finish": t(13, 50, 0),  # 45 min, missed the 50-pointer
                    "punches": [(138, t(13, 15, 0)), (130, t(13, 25, 0)), (142, t(13, 42, 0))],
                },
                {
                    "card_number": 8500013,
                    "name": "Score Dana", "class": "Score-O", "club": "BUSHO",
                    "start": t(13, 15, 0), "finish": None,
                    "punches": [(138, t(13, 25, 0)), (130, t(13, 35, 0))],
                },
            ],
        },
    ]


def build_class_results(classes: list[dict] | None = None) -> dict:
    """
    Build and rank results for every card in every class.

    Returns rank_results' output: {class_name: [result, ...]}. Defaults to the
    mock roster so the web app and the terminal demo render the same data.
    """
    if classes is None:
        classes = mock_classes()
    results = []
    for entry in classes:
        for card in entry["cards"]:
            results.append(build_result(card, entry["course"]))
    return rank_results(results)


def _demo() -> None:
    """
    Run the mock roster through its courses and print the results.

    Pure mock data, mirroring MOCK_CARD_DATA's shape -- no Flask, no database,
    no SI hardware. Run with:  python results.py
    """
    classes = mock_classes()

    # Splits for one runner, to eyeball the split calculator.
    runner = classes[0]["cards"][1]  # Test Runner
    print(f"Splits -- {runner['name']}")
    for row in calculate_splits(runner["start"], runner["punches"], runner["finish"]):
        print(
            f"  {str(row['control']):>4}   "
            f"leg {format_duration(row['leg_seconds'])}   "
            f"cum {format_duration(row['cumulative_seconds'])}"
        )

    ranked = build_class_results(classes)

    for class_name, rows in ranked.items():
        is_score = any(r["points"] is not None for r in rows)
        print(f"\n=== {class_name} ({'score' if is_score else 'linear'}) ===")
        for r in rows:
            pos = "-" if r["position"] is None else str(r["position"])
            time = "-" if r["total_seconds"] is None else format_duration(r["total_seconds"])
            note = ""
            if r.get("missed_control") is not None:
                note = f"  (missed {r['missed_control']})"
            if is_score:
                pts = "-" if r["points"] is None else str(r["points"])
                print(f"  {pos:>2}  {r['name']:<14} {r['status']:<4} {pts:>4} pts  {time}{note}")
            else:
                print(f"  {pos:>2}  {r['name']:<14} {r['status']:<4} {time}{note}")


if __name__ == "__main__":
    _demo()
