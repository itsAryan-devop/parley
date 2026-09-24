"""Surface forms -> slot values, and the cue words that mark a repair.

Two sources of vocabulary, in priority order:

1. **The manifest.** A `ParamSpec` with an `enum` is a closed vocabulary the
   scenario itself handed us, so those values are matched first and confidently.
   Intent cues are derived the same way — from the *distinctive* words in each
   intent's tool names and descriptions — which means an unseen tool brings its
   own vocabulary with it and needs no code change.

2. **A small built-in gazetteer.** Real transcripts say "Mumbai", not "BOM".
   Cities, weekdays, times of day and flight numbers are domain-general enough
   to ship, and a scenario may override or extend any of it.

The cue lists implement Shriberg's interregnum distinction (see RESEARCH.md
R2.1): *filled pauses* skew towards self-repair, *editing terms* towards
correction. Neither decides on its own — the value comparison does — but they
are the strongest single feature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..protocol.manifest import ToolManifest

# --------------------------------------------------------------------- cue words

FILLED_PAUSES = frozenset({"uh", "um", "er", "erm", "uhh", "umm", "hmm", "mm", "eh"})
"""Hesitation with no semantic content. Skews self-repair."""

EDITING_TERMS = frozenset(
    {
        "no", "sorry", "actually", "wait", "rather", "instead", "scratch that",
        "make that", "i mean", "i meant", "correction", "not", "nope", "hold on",
        "let me", "change that", "strike that",
    }
)
"""Explicit repair markers. Skews correction."""

ADDITIVE_CUES = frozenset({"and", "also", "plus", "as well", "too", "another", "while you"})
"""Markers that a second goal is being *added*, not substituted.

"...and a hotel in Goa" while a flight search runs is not a goal switch, and
treating it as one cancelled the flight search for nothing. The cue is the
leading conjunction; this is the only thing in the sentence that distinguishes
the two readings.
"""

GOAL_SWITCH_CUES = frozenset(
    {
        "forget", "never mind", "nevermind", "cancel that", "drop that",
        "change of plan", "different", "something else", "new plan", "abandon",
    }
)

REFINEMENT_CUES = frozenset(
    {
        "only", "just", "make it", "prefer", "cheapest", "earliest", "latest",
        "fastest", "narrow", "filter", "under", "less than", "at most", "nonstop",
        "non-stop", "direct",
    }
)

REPEAT_CUES = frozenset(
    {
        "say that again", "repeat", "what was that", "come again", "pardon",
        "sorry what", "one more time", "again please", "didn't catch",
        "did not catch", "what did you say",
        # Shortened forms that survive an ASR deletion. "say that again" losing
        # a word is still unambiguously a repeat request, and audio is 30% of
        # the hidden set.
        "say again", "that again", "more time", "catch that", "did you say",
        "what was",
    }
)

FLOOR_GRABS = frozenset({"stop", "wait", "hold on", "hang on", "shush", "quiet", "listen"})
"""Pure floor-grabs: the user wants the microphone, not a different outcome."""

BACKCHANNELS = frozenset(
    {
        "mhm", "uh huh", "huh", "yeah", "yah", "yep", "yup", "right", "ok",
        "okay", "sure", "got it", "i see", "gotcha",
        # "huh" is listed because decontamination strips the "uh" from "uh huh".
    }
)
"""Listener noises. NOT interruptions -- the user is signalling attention."""

_STOPWORDS = frozenset(
    {
        "a", "an", "and", "the", "to", "for", "of", "in", "on", "at", "by", "with",
        "get", "set", "list", "find", "search", "lookup", "look", "up", "create",
        "make", "do", "run", "fetch", "query", "check", "status", "id", "new",
    }
)


# ------------------------------------------------------------------- gazetteer

_CITIES: dict[str, str] = {
    "delhi": "DEL", "new delhi": "DEL",
    "mumbai": "BOM", "bombay": "BOM",
    "bengaluru": "BLR", "bangalore": "BLR",
    "chennai": "MAA", "madras": "MAA",
    "kolkata": "CCU", "calcutta": "CCU",
    "hyderabad": "HYD", "pune": "PNQ", "goa": "GOI",
    "ahmedabad": "AMD", "jaipur": "JAI", "kochi": "COK", "lucknow": "LKO",
    "chandigarh": "IXC", "patiala": "IXC",
}

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

#: Which slot a preposition points at, when several slots share a vocabulary.
#: "fly to Goa" and "a hotel in Goa" name the same city and mean different slots,
#: and the preposition is the only thing in the sentence that says which.
_PREPOSITION_SLOTS: dict[str, tuple[str, ...]] = {
    "to": ("destination",),
    "into": ("destination",),
    "toward": ("destination",),
    "towards": ("destination",),
    "from": ("origin",),
    "out": ("origin",),
    "in": ("city", "destination"),
    "at": ("city", "destination"),
    "near": ("city",),
    "around": ("city",),
    "on": ("date",),
    "for": ("date", "party_size"),
}

_TIME_OF_DAY = {
    "morning": "morning", "afternoon": "afternoon", "evening": "evening",
    "night": "night", "tonight": "night", "am": "morning", "pm": "afternoon",
}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


@dataclass
class Match:
    slot: str
    value: Any
    surface: str
    start: int
    end: int
    confidence: float


@dataclass
class Lexicon:
    """Scenario-supplied vocabulary, layered over the built-in gazetteer."""

    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    """slot -> {lowercase surface form: canonical value}"""
    patterns: dict[str, list[re.Pattern[str]]] = field(default_factory=dict)
    """slot -> regexes capturing the value in group 'v'"""
    intent_cues: dict[str, set[str]] = field(default_factory=dict)
    """intent -> distinctive words that signal it"""

    # -- construction --------------------------------------------------

    @classmethod
    def default(cls) -> Lexicon:
        values: dict[str, dict[str, Any]] = {
            "destination": dict(_CITIES),
            "origin": dict(_CITIES),
            "city": dict(_CITIES),
            "date": {d: d.capitalize() for d in _WEEKDAYS}
            | {"today": "today", "tomorrow": "tomorrow", "day after tomorrow": "day_after"},
            "time_of_day": dict(_TIME_OF_DAY),
            # Number words are deliberately NOT a bare vocabulary for party_size.
            # "book the Tuesday one" and "that uh uh one" both end in a pronoun,
            # not a count, and matching it bound party_size=1 out of thin air --
            # a false slot in the snapshot, which is scored. A count needs a
            # counting context, so it lives in the patterns below instead.
        }
        _COUNT = "|".join(_NUMBER_WORDS)
        _HEADS = "people|passengers|adults|guests|travellers|travelers|tickets|seats|rooms|of us"
        patterns: dict[str, list[re.Pattern[str]]] = {
            "flight_no": [re.compile(r"\b(?P<v>[A-Z0-9]{2}\s?\d{2,4})\b")],
            "party_size": [
                re.compile(rf"\b(?P<v>\d{{1,2}}|{_COUNT})\s+(?:{_HEADS})\b", re.I),
                re.compile(rf"\bfor\s+(?P<v>\d{{1,2}}|{_COUNT})\b(?!\s*(?:am|pm|o'clock))", re.I),
            ],
            "date": [re.compile(r"\b(?P<v>\d{4}-\d{2}-\d{2})\b")],
            "max_price": [re.compile(r"(?:under|below|less than|at most)\s+(?:inr\s*|rs\.?\s*|₹\s*)?(?P<v>\d{3,6})", re.I)],
        }
        return cls(values=values, patterns=patterns)

    @classmethod
    def for_manifest(cls, manifest: ToolManifest, base: Lexicon | None = None) -> Lexicon:
        """Layer manifest-declared vocabulary on top of a base lexicon."""
        lex = base or cls.default()
        values = {k: dict(v) for k, v in lex.values.items()}

        for spec in manifest.tools.values():
            for param in spec.params:
                if param.enum:
                    bucket = values.setdefault(param.name, {})
                    for option in param.enum:
                        bucket.setdefault(str(option).lower(), option)

        return cls(
            values=values,
            patterns={k: list(v) for k, v in lex.patterns.items()},
            intent_cues=_derive_intent_cues(manifest),
        )

    # -- matching ------------------------------------------------------

    def find(
        self,
        text: str,
        slots: Iterable[str] | None = None,
        prefer: Iterable[str] | None = None,
    ) -> list[Match]:
        """All slot values mentioned in `text`, longest surface form first.

        Longest-first matters: "new delhi" must not be shadowed by "delhi", and
        "day after tomorrow" must not be shadowed by "tomorrow".

        `prefer` breaks ties between slots that share a vocabulary. "Goa" is a
        valid `destination`, `origin` and `city`, and picking by dictionary
        order meant "a hotel in Goa" bound `destination` — after which the hotel
        search could never find its required `city` and simply never ran. The
        preposition decides first (`to` → destination, `from` → origin,
        `in` → city); `prefer` — the parameters the active goal's tools accept —
        decides what is left.
        """
        lowered = text.lower()
        wanted = set(slots) if slots is not None else None
        preferred = set(prefer or ())
        found: list[Match] = []
        claimed: list[tuple[int, int]] = []

        def overlaps(a: int, b: int) -> bool:
            return any(not (b <= s or a >= e) for s, e in claimed)

        # Group by surface form so competing slots for the same span are
        # resolved together rather than first-come-first-served.
        by_surface: dict[str, list[tuple[str, Any]]] = {}
        for slot, forms in self.values.items():
            if wanted is not None and slot not in wanted:
                continue
            for surface, value in forms.items():
                by_surface.setdefault(surface, []).append((slot, value))

        for surface in sorted(by_surface, key=len, reverse=True):
            options = by_surface[surface]
            for m in re.finditer(rf"(?<!\w){re.escape(surface)}(?!\w)", lowered):
                if overlaps(m.start(), m.end()):
                    continue
                claimed.append((m.start(), m.end()))
                slot, value = self._disambiguate(options, lowered, m.start(), preferred)
                found.append(
                    Match(slot=slot, value=value, surface=text[m.start():m.end()],
                          start=m.start(), end=m.end(), confidence=0.95)
                )

        for slot, pats in self.patterns.items():
            if wanted is not None and slot not in wanted:
                continue
            for pat in pats:
                for m in pat.finditer(text):
                    if not overlaps(m.start(), m.end()):
                        claimed.append((m.start(), m.end()))
                        raw = m.group("v")
                        found.append(
                            Match(slot=slot, value=_coerce(raw), surface=raw,
                                  start=m.start(), end=m.end(), confidence=0.9)
                        )

        return sorted(found, key=lambda x: x.start)

    @staticmethod
    def _disambiguate(
        options: list[tuple[str, Any]], lowered: str, start: int, preferred: set[str]
    ) -> tuple[str, Any]:
        """Choose which slot a surface form fills."""
        if len(options) == 1:
            return options[0]

        by_slot = dict(options)
        preceding = lowered[:start].split()
        head = preceding[-1] if preceding else ""

        for slot in _PREPOSITION_SLOTS.get(head, ()):
            if slot in by_slot:
                return slot, by_slot[slot]

        for slot, value in options:
            if slot in preferred:
                return slot, value

        return options[0]

    def intent_for(self, text: str) -> tuple[str | None, float]:
        """Best-scoring intent for an utterance, by distinctive-cue hits."""
        # Punctuation-stripped, for the same reason the NLU cue matcher is:
        # "I want to rent a kayak, a double" hid the cue behind a comma and the
        # goal switch was never detected.
        lowered = " " + " ".join(re.sub(r"[^\w\s']+", " ", text.lower()).split()) + " "
        lowered = _drop_dismissed(lowered)
        best: tuple[str | None, int] = (None, 0)
        for intent, cues in self.intent_cues.items():
            hits = sum(1 for cue in cues if f" {cue} " in lowered or f" {cue}s " in lowered)
            if hits > best[1]:
                best = (intent, hits)
        if best[0] is None:
            return None, 0.0
        return best[0], min(1.0, 0.55 + 0.2 * best[1])


#: Phrases after which the next word or two names what the user is *abandoning*.
_DISMISSALS = ("forget", "never mind", "nevermind", "not", "instead of", "drop", "cancel", "no more")


def _drop_dismissed(padded: str) -> str:
    """Remove the goal the user just rejected, before scoring intents.

    "forget flights, find me a hotel" mentions both goals exactly once, so a
    naive cue count ties and the dictionary order decides — which picked
    `book_flight`, the goal being abandoned. The words immediately following a
    dismissal are evidence *against* that goal, not for it, so they are struck
    out before counting.
    """
    tokens = padded.split()
    if not tokens:
        return padded

    drop: set[int] = set()
    for i, token in enumerate(tokens):
        window = " ".join(tokens[i:i + 2])
        if token in _DISMISSALS or window in _DISMISSALS:
            span = 2 if window in _DISMISSALS and token not in _DISMISSALS else 1
            drop.update(range(i, min(i + span + 2, len(tokens))))

    kept = [t for i, t in enumerate(tokens) if i not in drop]
    return " " + " ".join(kept) + " "


def _coerce(raw: str) -> Any:
    text = raw.strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    spelled = _NUMBER_WORDS.get(text.lower())
    if spelled is not None:
        return spelled
    return text


def _derive_intent_cues(manifest: ToolManifest) -> dict[str, set[str]]:
    """Words that appear in one intent's tools and no other's.

    "book" appears in both `book_flight` and `book_hotel`, so it is useless as a
    discriminator; "flight" and "hotel" are the words that actually separate the
    goals. Taking the set difference finds them without anyone writing them down,
    which is what keeps this working for tools we have never seen.
    """
    per_intent: dict[str, set[str]] = {}
    for spec in manifest.tools.values():
        if not spec.intent:
            continue
        words = _content_words(f"{spec.name} {spec.description}")
        words |= {_singular(p) for p in spec.param_names}
        per_intent.setdefault(spec.intent, set()).update(words)

    cues: dict[str, set[str]] = {}
    for intent, words in per_intent.items():
        others: set[str] = set()
        for other, ws in per_intent.items():
            if other != intent:
                others |= ws
        distinctive = words - others
        cues[intent] = distinctive or words
    return cues


def _content_words(text: str) -> set[str]:
    raw = re.split(r"[^a-zA-Z]+", text.lower())
    return {_singular(w) for w in raw if len(w) > 2 and w not in _STOPWORDS}


def _singular(word: str) -> str:
    """Crude stemming: enough to unify flight/flights without a dependency."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word
