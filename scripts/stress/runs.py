"""
Planned runs: what a runner actually did on course, as a card read would
show it. Each fault is a thing that happens at real events.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

# How often each thing happens (weights), for an individual linear event.
LINEAR_MIX = {
    "clean": 60, "extra_punches": 8, "double_punch": 4, "skip": 6, "swap": 3,
    "dnf": 3, "blank_card": 1, "not_read": 3, "old_punches": 4, "stale_check": 2,
    "own_start_punch": 4, "slow": 2,
}


def pick(rng: random.Random, mix: dict) -> str:
    kinds = list(mix)
    return rng.choices(kinds, weights=[mix[k] for k in kinds])[0]


def linear_run(rng: random.Random, controls: list[int], start: datetime, kind: str,
               *, pace=(60, 240), spare_codes=(), slow_minutes=0) -> dict:
    """A run on a linear course. Returns {kind, card_start, start, finish,
    punches, check, read} where ``start`` is when they really set off and
    ``card_start`` is what the card says (None for a clock start)."""
    codes = list(controls)
    if kind == "skip" and len(codes) > 2:
        codes.pop(rng.randrange(len(codes)))
    if kind == "swap" and len(codes) > 3:
        i = rng.randrange(len(codes) - 1)
        if codes[i] != codes[i + 1]:
            codes[i], codes[i + 1] = codes[i + 1], codes[i]
    card_start = None
    real_start = start
    if kind == "own_start_punch":
        real_start = start + timedelta(seconds=rng.randint(5, 50))
        card_start = real_start
    t = real_start
    punches = []
    for code in codes:
        t += timedelta(seconds=rng.randint(*pace))
        punches.append((code, t))
        if kind == "double_punch" and rng.random() < 0.3:
            punches.append((code, t + timedelta(seconds=2)))
        if kind == "extra_punches" and spare_codes and rng.random() < 0.25:
            t += timedelta(seconds=rng.randint(20, 60))
            punches.append((rng.choice(spare_codes), t))
    if kind == "slow":
        t += timedelta(minutes=slow_minutes)
    finish = t + timedelta(seconds=rng.randint(15, 60))
    check = None
    if kind == "old_punches":
        # Yesterday's run still on the card: earlier times, then cleared + checked.
        old = start - timedelta(hours=2)
        punches = [(c, old + timedelta(minutes=i)) for i, c in enumerate(controls)] + punches
        check = start - timedelta(minutes=3)
    elif kind == "stale_check":
        check = finish + timedelta(minutes=30)           # a check "after" the run: ignored
    else:
        check = start - timedelta(minutes=rng.randint(2, 6))
    if kind == "dnf":
        punches = punches[: max(1, len(punches) // 2)]
        finish = None
    if kind == "blank_card":
        punches, finish, card_start = [], None, None
    if kind == "not_read":
        # Never reads out: the app knows no finish. (The planned punches are
        # kept so the card can be read later in a scenario.)
        return {"kind": kind, "card_start": card_start, "start": start, "finish": None,
                "later_finish": finish, "punches": punches, "check": check, "read": False}
    return {"kind": kind, "card_start": card_start, "start": real_start if card_start
            else start, "finish": finish, "punches": punches, "check": check,
            "read": kind != "not_read"}


def score_run(rng, values: dict, start: datetime, limit_minutes: int, kind: str) -> dict:
    """A score-O run: visits some controls in any order."""
    codes = list(values)
    rng.shuffle(codes)
    take = codes[: rng.randint(1, len(codes))]
    t = start
    punches = []
    budget = limit_minutes * 60
    for code in take:
        t += timedelta(seconds=rng.randint(90, 400))
        punches.append((code, t))
    if kind == "over":
        t = max(t, start + timedelta(seconds=budget + rng.randint(1, 900)))
    elif kind == "clean":
        # Back just inside the limit most of the time.
        t = min(t, start + timedelta(seconds=budget - rng.randint(5, 120)))
        punches = [(c, pt) for c, pt in punches if pt <= t]
    elif kind == "repeat" and punches:
        punches.append((punches[0][0], t + timedelta(seconds=30)))
        t += timedelta(seconds=60)
    finish = t + timedelta(seconds=rng.randint(10, 40)) if kind != "dnf" else None
    return {"kind": kind, "card_start": None, "start": start, "finish": finish,
            "punches": punches, "check": start - timedelta(minutes=4), "read": True}


FIRST = ["Åsa", "Björn", "Chloé", "Dāvis", "Émile", "Freya", "Gösta", "Hélène", "Ingrid", "Jürgen",
         "Kaito", "Liv", "Mateo", "Nadia", "Oona", "Pål", "Quinn", "Rūta", "Søren", "Tiia",
         "Ugo", "Vera", "Wiremu", "Xiu", "Yusuf", "Zoë", "Aroha", "Ben", "Cara", "Dylan",
         "Erin", "Finn", "Grace", "Hamish", "Isla", "Jack", "Kiri", "Liam", "Mia", "Noah"]
LAST = ["Andersson", "O'Brien", "Nguyễn", "Müller", "Smith", "Taylor", "Walker", "Kowalski",
        "García", "Johansen", "Te Rangi", "Brown", "Wilson", "Chen", "Singh", "Martin",
        "Lee", "Harris", "Clark", "Young", "King", "Wright", "Scott", "Green", "Baker"]
CLUBS = ["Bibbulmun Orienteers", "Wildflower OC", "Kulgun OC", "Top End O",
         "Lost & Found", "Orienteering Club Ümeå", "Tasman Navigators", "Solo"]


def names(rng: random.Random, n: int) -> list[str]:
    """n distinct believable names (with accents, apostrophes, spaces)."""
    out, seen = [], set()
    while len(out) < n:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name in seen:
            name = f"{name} {len(out)}"
        seen.add(name)
        out.append(name)
    return out
