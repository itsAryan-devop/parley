"""Stale-value check: refuse a call built from a value the user took back.

Idea credited to SentinelEdge (github.com/Harya018/samsung-prism-Hackathon, no
licence stated, so reimplemented here from the description, not copied): a
"block-only resolver" that checks a proposed tool call against the turn's
transcript before it runs.

Our version uses the structure PARLEY already models (Shriberg's repair:
reparandum, editing term, repair; see docs/RESEARCH.md R2.1). If an argument
value occurs in the current user turn *only before* the last strong repair
marker ("no", "wait", "I mean", "scratch that", ...) and not after it, it
sits in the reparandum: the user said it and then corrected it. If a call uses
such a value and none of its values comes from after the correction, it is
refused once, quoting the correction, and the LLM can try again. A
second identical attempt goes through, so a false positive costs one extra LLM
round trip, never a lost action.

Deliberately not markers: "actually", "not" and "let me". They occur too often
without being corrections ("I have not received it", "let me find it").
"""

from __future__ import annotations

import re
from typing import Any

STRONG_REPAIRS = (
    "scratch that", "strike that", "change that", "make that", "make it",
    "i mean", "i meant", "correction", "no wait", "wait", "sorry", "nope", "no",
    "instead",
)
_MARKER = re.compile(r"\b(" + "|".join(re.escape(m) for m in STRONG_REPAIRS) + r")\b")
_SQUASH = re.compile(r"[^a-z0-9]+")


def _squash(text: str) -> str:
    return _SQUASH.sub("", text.lower())


REPARANDUM_WORDS = 5  # how far back from the correction a replaced value can sit


def stale_value(args: dict[str, Any], turn_text: str) -> str | None:
    """The user's correction, if this call ignored it; else None.

    Stale means the call uses a value from the reparandum (the few words right
    before the last cluster of repair markers) and *none* of its values
    appears after the correction. A call that uses the corrected value passes,
    and so does one whose other slots were simply left unchanged.
    """
    lowered = turn_text.lower()
    marks = list(_MARKER.finditer(lowered))
    if not marks:
        return None
    # "..., sorry, I mean ..." is one correction: walk back over adjacent markers.
    start, i = marks[-1].start(), len(marks) - 1
    while i > 0 and not _SQUASH.sub("", lowered[marks[i - 1].end():marks[i].start()]):
        i -= 1
        start = marks[i].start()
    reparandum = _squash(" ".join(lowered[:start].split()[-REPARANDUM_WORDS:]))
    tail = turn_text[marks[-1].end():].strip(" ,.;:-")
    after = _squash(tail)
    if not after:  # a correction with nothing after it yet: the guard's job
        return None
    values = [_squash(v) for v in args.values() if isinstance(v, str)]
    values = [v for v in values if len(v) >= 3]
    if any(v in after for v in values):
        return None
    if any(v in reparandum for v in values):
        return " ".join(tail.split()[:10])
    return None


class StaleValueCheck:
    """One per conversation. Refuses each stale (tool, args) at most once per turn."""

    def __init__(self) -> None:
        self.turn_text = ""
        self._warned: set[str] = set()

    def user_said(self, text: str) -> None:
        self.turn_text = f"{self.turn_text} {text}".strip()

    def agent_replied(self) -> None:
        self.turn_text, self._warned = "", set()

    def check(self, tool: str, args: dict[str, Any], key: str) -> dict | None:
        correction = stale_value(args, self.turn_text)
        if correction is None or key in self._warned:
            return None
        self._warned.add(key)
        return {
            "status": "not_executed",
            "reason": f"The user corrected themselves (\"...{correction}\") and this call "
                      f"still uses the value they took back. Use the corrected value. If "
                      f"nothing here was corrected, call again unchanged.",
        }
