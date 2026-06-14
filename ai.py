"""
AI commentary layer (scaffold).

Turns the deterministic numbers from :mod:`analytics` into natural-language
coaching and course-setting feedback. When ``ANTHROPIC_API_KEY`` is set it calls
the Claude API; otherwise it returns a rule-based summary built from the same
numbers, so every feature works (just less eloquently) with no key and no
network. The rule-based text is also the prompt context for the AI path, so the
two stay consistent.

Config:
    ANTHROPIC_API_KEY     enables the Claude API
    BMEOS_AI_MODEL        model id (default "claude-sonnet-4-6")
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("ai")

DEFAULT_MODEL = os.environ.get("BMEOS_AI_MODEL", "claude-sonnet-4-6")


def is_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _complete(system: str, prompt: str, *, max_tokens: int = 400) -> str | None:
    """Call Claude; return text, or None on any failure (caller falls back)."""
    if not is_enabled():
        return None
    try:
        import anthropic  # lazy: app runs without the dependency
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in msg.content
                       if getattr(block, "type", None) == "text").strip()
    except Exception as err:  # pragma: no cover - network/credentials/SDK
        log.error("AI call failed, falling back to rule-based: %s", err)
        return None


# ---------------------------------------------------------------------------
# Training focus (from a competitor's weak legs)
# ---------------------------------------------------------------------------

def _weak_legs_sentence(weak_legs: list[dict]) -> str:
    if not weak_legs:
        return "No standout weak legs — your splits are even across the course."
    parts = [f"control {l['control']} (+{round(l['pct_vs_avg'] * 100)}% vs field)"
             for l in weak_legs[:3]]
    return "Weakest legs compared with the field: " + ", ".join(parts) + "."


def training_advice(name: str, weak_legs: list[dict]) -> dict:
    """Coaching focus for a competitor. Returns ``{text, source}`` where source
    is 'ai' or 'rules'."""
    summary = _weak_legs_sentence(weak_legs)
    ai_text = _complete(
        system="You are an orienteering coach. Give brief, practical training "
               "advice (3-4 sentences). Be specific about route choice, compass "
               "work and concentration; don't invent data.",
        prompt=f"Runner: {name}\n{summary}\nWhat should they focus their training on?",
    )
    if ai_text:
        return {"text": ai_text, "source": "ai"}

    if not weak_legs:
        tip = (" Keep building consistency and work on clean control flow at speed.")
    else:
        tip = (" These are likely route-choice or concentration losses — practise "
               "planning the next leg while approaching the control, and simplify "
               "route choices under fatigue.")
    return {"text": summary + tip, "source": "rules"}


# ---------------------------------------------------------------------------
# Course-setting review
# ---------------------------------------------------------------------------

def course_review(course_name: str, findings: list[dict],
                  shared: list[dict]) -> dict:
    """Narrative course-setting review from rule-based findings + shared legs.
    Returns ``{text, source}``."""
    lines = []
    for f in findings:
        lines.append(f"- [{f['level']}] {f['message']}")
    for s in shared:
        lines.append(f"- [warn] Leg {s['leg'][0]}→{s['leg'][1]} is shared with: "
                     + ", ".join(c for c in s["courses"] if c != course_name))
    summary = "\n".join(lines) if lines else "No structural issues found."

    ai_text = _complete(
        system="You are an experienced orienteering course planner. Review the "
               "structural findings and give a short, constructive assessment "
               "(3-5 sentences). Don't invent issues beyond those listed.",
        prompt=f"Course: {course_name}\nFindings:\n{summary}\n"
               f"Give a brief planning review.",
    )
    if ai_text:
        return {"text": ai_text, "source": "ai", "findings": summary}
    return {"text": summary, "source": "rules", "findings": summary}
