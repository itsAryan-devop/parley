"""Argument clean-up for identifiers the user spelled out loud.

A user reading an order or document number aloud says it one character at a
time ("P, five, two"). Whisper transcribes that with separators nobody
pronounced -- "P-5-2", "A, B, C, 1, 2, 3", "D L 5" -- and the LLM copies the
transcript into the tool call verbatim, so the backend sees an identifier that
does not exist. Asking the LLM to join them in the system prompt did not work
reliably (docs/FDB_RESULTS.md, step 4), so it is done deterministically here.

The rule is deliberately narrow: only arguments that name an identifier, and
only when *every* piece between separators is at most three characters --
the shape of spelled-out speech. "ORD-12345" or "SAVE-2026" keep their hyphen.
"""

from __future__ import annotations

import re
from typing import Any

ID_ARGS = re.compile(r"(_id|_number|_code)$")
_PIECES = re.compile(r"^[A-Za-z0-9]{1,3}(?:[\s,.\-]+[A-Za-z0-9]{1,3})+$")


def canonical_spoken_id(value: str) -> str:
    text = value.strip()
    if not _PIECES.match(text):
        return value
    return re.sub(r"[\s,.\-]+", "", text)


def clean_args(args: dict[str, Any]) -> dict[str, Any]:
    return {
        k: canonical_spoken_id(v) if isinstance(v, str) and ID_ARGS.search(k) else v
        for k, v in args.items()
    }
