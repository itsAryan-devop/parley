"""The learned endpointer as a LiveKit turn detector.

LiveKit's `AgentSession` asks a turn detector for P(user is done) each time VAD
reports end of speech. Below `unlikely_threshold` it waits `max_endpointing_delay`
before replying; otherwise `min_endpointing_delay`. The stock FDB-v3 cascaded
agent has no detector, so every pause over ~1 s ends the turn -- including the
pause in "flights to Paris -- actually, no ... Berlin", after which the LLM
fires on Paris. That is the extra-call failure FDB-v3's self-correction and
hesitation items punish.

This adapter feeds the *same* frozen model and the *same* feature function the
PARLEY agent uses (`parley.agent.endpointer`). Three inputs were computed from
PARLEY's own tool manifest; here they come from the FDB-v3 tool manifest
instead, derived mechanically from the tool names (nothing is written per test
item -- the guide disqualifies that):

- `has_intent`       -- the turn mentions a word from a tool name
- `just_bound_value` -- the turn ends on a value (number, date, proper noun,
                        currency code), not on a dangling word, filler or
                        editing term
- `complete_request` -- both of the above

Timing: LiveKit calls the detector after VAD has already observed silence, so
`silence_ms` is that VAD window; `elapsed_ms` is estimated from word count at a
conversational rate because the chat context carries no audio timing. Both
approximations are stated rather than hidden.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from parley.agent.endpointer import _DANGLING, EndpointModel, endpoint_features
from parley.agent.lexicon import EDITING_TERMS, FILLED_PAUSES

# "instead" / "rather" close a repair ("make it Madrid instead") rather than open one.
_REPAIR = EDITING_TERMS - {"instead", "rather"}

WORDS_PER_SECOND = 2.5
_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december", "jan", "feb", "mar", "apr",
           "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
         "today", "tomorrow", "tonight"}
_NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                 "ten", "eleven", "twelve", "twenty", "thirty", "forty", "fifty",
                 "hundred", "thousand", "million"}
_TOKEN = re.compile(r"[A-Za-z0-9$€£¥'.\-]+")
_STOP = {"i", "me", "my", "a", "an", "the", "to", "for", "and", "get", "please"}


def manifest_vocabulary(tool_names: Iterable[str]) -> frozenset[str]:
    """Intent words from tool names: `search_flights` -> {search, flights, flight}."""
    vocab: set[str] = set()
    for name in tool_names:
        for part in name.lower().split("_"):
            if part and part not in _STOP:
                vocab.add(part)
                if part.endswith("s") and len(part) > 3:
                    vocab.add(part[:-1])
    return frozenset(vocab)


def _is_value(raw: str, *, sentence_initial: bool) -> bool:
    tok = raw.strip(".,!?;:'\"").lower()
    if not tok:
        return False
    if any(c.isdigit() for c in tok) or tok in _MONTHS or tok in _DAYS or tok in _NUMBER_WORDS:
        return True
    core = raw.strip(".,!?;:'\"")
    if core.isupper() and 2 <= len(core) <= 4 and core.isalpha():  # USD, LHR
        return True
    return core[:1].isupper() and not sentence_initial and core.lower() != "i"


def turn_features(
    text: str, vocab: frozenset[str], *, silence_ms: float
) -> dict[str, float]:
    tokens = _TOKEN.findall(text)
    lowered = [t.strip(".,!?;:'\"").lower() for t in tokens]
    has_intent = any(t in vocab for t in lowered)

    # Where the last repair marker ends: only a value supplied *after* it counts.
    repair_end = 0
    for i, tok in enumerate(lowered):
        pair = f"{lowered[i - 1]} {tok}" if i else ""
        if tok in _REPAIR or pair in _REPAIR:
            repair_end = i + 1
    last = lowered[-1] if lowered else ""
    initial = {0} | {i + 1 for i, t in enumerate(tokens) if t.endswith((".", "?", "!"))}
    just_bound_value = (
        bool(tokens)
        and repair_end < len(tokens)          # did not end on "no" / "actually" / ...
        and last not in FILLED_PAUSES
        and last not in _DANGLING
        and any(_is_value(tokens[i], sentence_initial=i in initial)
                for i in range(repair_end, len(tokens)))
    )
    return endpoint_features(
        text,
        silence_ms=silence_ms,
        elapsed_ms=len(tokens) / WORDS_PER_SECOND * 1000.0,
        complete_request=has_intent and just_bound_value,
        has_intent=has_intent,
        just_bound_value=just_bound_value,
    )


class ParleyTurnDetector:
    """Implements LiveKit's `_TurnDetector` protocol (duck-typed; no LiveKit import)."""

    def __init__(
        self,
        tool_names: Iterable[str],
        *,
        vad_silence_s: float,
        unlikely_threshold: float = 0.15,
        model: EndpointModel | None = None,
    ) -> None:
        loaded = model or EndpointModel.load_default()
        if loaded is None:
            raise RuntimeError("endpoint_model.json missing -- run scripts/train_endpointer.py")
        self._model = loaded
        self._vocab = manifest_vocabulary(tool_names)
        self._silence_ms = vad_silence_s * 1000.0
        self._unlikely = unlikely_threshold

    @property
    def model(self) -> str:
        return "parley-endpointer"

    @property
    def provider(self) -> str:
        return "parley"

    async def unlikely_threshold(self, language: str | None) -> float | None:
        return self._unlikely

    async def supports_language(self, language: str | None) -> bool:
        return language is None or str(language).lower().startswith("en")

    def probability(self, text: str) -> float:
        return self._model.probability(
            turn_features(text, self._vocab, silence_ms=self._silence_ms)
        )

    async def predict_end_of_turn(self, chat_ctx, *, timeout: float | None = None) -> float:
        for item in reversed(getattr(chat_ctx, "items", [])):
            if getattr(item, "role", None) == "user":
                return self.probability(getattr(item, "text_content", None) or "")
        return 1.0
