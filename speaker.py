"""
Speaker (commentator) numbers for runners still out on course.

For every runner who has started but not finished, on a linear course:

* **Time to beat**: the clock time they must finish by to take the lead
  (their start + the class leader's time).
* **Radio controls**: at the last course control they've punched (radio /
  online controls), their split, their place at that control and the gap to
  the fastest split there.
* **Prediction**: finish time and place if they keep their current pace
  relative to the leader (leader's time scaled by their split ratio at the
  last control).

Pure over the engine's evaluated classes, so it's cheap and testable.
"""

from __future__ import annotations

from datetime import timedelta

import store
from results import aligned_splits, format_split


def out_on_course(evaluated: list[dict]) -> list[dict]:
    """Rows for every started-but-not-finished runner, soonest start first."""
    rows = []
    for entry in evaluated:
        course = entry["course"]
        results = entry["results"]
        linear = course["type"] == "linear"
        controls = store.split_controls(course)[0] if linear else []
        finished_ok = sorted(r["total_seconds"] for r in results
                             if r["status"] == "ok" and r["total_seconds"] is not None)
        leader = finished_ok[0] if finished_ok else None

        # Every runner's cumulative time at each course control (None = not there).
        cum_at = {}
        if linear:
            for r in results:
                if r.get("start") is None:
                    continue
                aligned = aligned_splits(r["start"], r.get("punches", []), r.get("finish"),
                                         controls)
                cum_at[r["id"]] = [a["cumulative_seconds"] for a in aligned[:-1]]

        for r in results:
            if r.get("start") is None or r.get("finish") is not None:
                continue
            row = {"id": r["id"], "name": r["name"], "club": r.get("club"),
                   "class": entry["class"]["name"], "start": r["start"].strftime("%H:%M:%S"),
                   "must_finish_by": None, "last_control": None, "split": None,
                   "split_place": None, "behind": None, "predicted": None,
                   "predicted_place": None}
            if leader is not None:
                row["must_finish_by"] = (r["start"] + timedelta(seconds=leader)).strftime("%H:%M:%S")
            mine = cum_at.get(r["id"], [])
            last = max((i for i, t in enumerate(mine) if t is not None), default=None)
            if last is not None:
                t = mine[last]
                at_control = sorted(c[last] for c in cum_at.values()
                                    if len(c) > last and c[last] is not None)
                row.update(last_control=f"{last + 1}/{len(controls)} ({controls[last]})",
                           split=format_split(t),
                           split_place=at_control.index(t) + 1,
                           behind=format_split(t - at_control[0]))
                # Scale the leader's finish by how this runner compares with the
                # leader's own split at the same control.
                leader_row = next((x for x in results if x["status"] == "ok"
                                   and x["total_seconds"] == leader), None) if leader else None
                ref = cum_at.get(leader_row["id"], []) if leader_row else []
                if leader_row and len(ref) > last and ref[last]:
                    predicted = round(leader * t / ref[last])
                    row["predicted"] = format_split(predicted)
                    row["predicted_place"] = sum(1 for x in finished_ok if x < predicted) + 1
            rows.append(row)
    rows.sort(key=lambda x: x["start"])
    return rows
