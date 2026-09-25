"""A generated corpus for the endpointer: turns streamed as chunks, each prefix
labelled done / not-done.

The same honesty caveats as `corpus.py` apply, and one more that is specific to
turn-taking. There is no public dataset labelled with *our* streaming chunk
boundaries and per-chunk trailing silences, so the corpus is synthesised. What
keeps it from being circular:

1. **Prefixes, not sentences.** A turn is realised as a sequence of chunks and
   every prefix becomes one example — so the model sees the same words with the
   label flipping from not-done to done only at the real boundary. It cannot
   learn "this phrasing => done"; it has to learn *where in the phrasing*.

2. **`complete_request` is computed through the real Lexicon and Planner**, the
   exact code the live agent runs, against the same manifest. The semantic
   completeness signal cannot drift between train and serve because there is one
   implementation of it.

3. **Held-out openers and an adversarial pause distribution.** `EVAL` uses
   turn shapes and cue words absent from `TRAIN`, and both include the case the
   whole exercise exists for: a long pause *mid-turn* after a dangling word
   ("...to Delhi and <900 ms> a hotel in Goa"). A silence-timeout baseline ends
   the turn there; a lexical endpointer must not. If the model only reproduced
   the timeout it would fail exactly these.

What this corpus honestly is: a way to learn sensible weights over a
hand-designed feature vector. What it is not: evidence about real human
turn-taking timing. That limit is recorded in DISCLOSURE.md, not papered over.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..protocol.manifest import parse_manifest
from ..protocol.state import SessionState, SlotSource
from .endpointer import endpoint_features
from .lexicon import Lexicon
from .planner import Planner

# The manifest the completeness signal is computed against. Travel + support,
# mirroring the public suite, so `complete_request` means the same thing here as
# it does at serve time.
MANIFEST = parse_manifest(
    [
        {"name": "search_flights", "description": "search available flights", "read_only": True,
         "intent": "book_flight",
         "params": [{"name": "origin"}, {"name": "destination", "required": True},
                    {"name": "date"},
                    {"name": "time_of_day", "enum": ["morning", "afternoon", "evening"]}]},
        {"name": "book_flight", "mutating": True, "intent": "book_flight",
         "params": [{"name": "flight_no", "required": True}]},
        {"name": "search_hotels", "description": "search available hotels", "read_only": True,
         "intent": "book_hotel", "params": [{"name": "city", "required": True}, {"name": "date"}]},
        {"name": "diagnose_sound", "description": "identify the mechanical cause of an appliance sound",
         "read_only": True, "intent": "troubleshoot",
         "params": [{"name": "sound", "required": True,
                     "enum": ["beeping", "grinding", "clicking", "continuous_tone", "silence"]}]},
    ]
)
LEXICON = Lexicon.for_manifest(MANIFEST)
PLANNER = Planner(MANIFEST)

CITIES = [("delhi", "DEL"), ("mumbai", "BOM"), ("bengaluru", "BLR"), ("goa", "GOI"),
          ("chennai", "MAA"), ("hyderabad", "HYD"), ("kolkata", "CCU"), ("pune", "PNQ")]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
FLIGHTS = ["AI101", "6E202", "UK404", "AI303", "6E550", "UK550"]
SOUNDS = ["beeping", "grinding", "clicking"]
FILLED = ["uh", "um", "er", "hmm"]


@dataclass
class Chunk:
    text: str
    trailing_silence_ms: float


@dataclass
class EndpointExample:
    turn_text: str
    silence_ms: float
    elapsed_ms: float
    complete_request: bool
    has_intent: bool
    just_bound_value: bool
    label: bool
    """True iff this prefix is the real end of the turn."""

    def features(self) -> dict[str, float]:
        return endpoint_features(
            self.turn_text,
            silence_ms=self.silence_ms,
            elapsed_ms=self.elapsed_ms,
            complete_request=self.complete_request,
            has_intent=self.has_intent,
            just_bound_value=self.just_bound_value,
        )


def _speak_ms(rng: random.Random, text: str) -> float:
    """Rough speaking duration for a chunk: ~230 ms/word with jitter."""
    return len(text.split()) * rng.uniform(190.0, 280.0)


def _mid_pause(rng: random.Random) -> float:
    """A between-words pause inside a turn. Usually short, occasionally long —
    the long ones are the adversarial cases (a thinking pause mid-sentence)."""
    return rng.uniform(60.0, 350.0) if rng.random() < 0.8 else rng.uniform(600.0, 1000.0)


def _final_pause(rng: random.Random) -> float:
    """The trailing silence after the last word. Overlaps the mid-pause range on
    purpose, so timing alone cannot separate done from not-done."""
    return rng.uniform(450.0, 1200.0)


def _turn_flight(rng: random.Random, *, held_out: bool) -> list[Chunk]:
    city = rng.choice(CITIES)[0]
    day = rng.choice(DAYS)
    openers = (["get me a flight", "looking to fly", "i'd like to travel"]
               if held_out else ["find me a flight", "i need a flight", "i want to fly"])
    pieces = [rng.choice(openers), f"to {city}", f"on {day}"]
    if rng.random() < 0.4:
        pieces.append(rng.choice(["in the morning", "morning only", "for two people"]))
    return _to_chunks(rng, pieces)


def _turn_add_hotel(rng: random.Random, *, held_out: bool) -> list[Chunk]:
    """The adversarial shape: a complete flight request, a dangling conjunction,
    a long pause, then a second goal. The pause after 'and' must not end it."""
    c1, c2 = rng.sample(CITIES, 2)
    day = rng.choice(DAYS)
    lead = "get me a flight" if held_out else "find me a flight"
    pieces = [lead, f"to {c1[0]}", f"on {day}", "and", f"a hotel in {c2[0]}"]
    chunks = _to_chunks(rng, pieces)
    # Force the long thinking pause onto the dangling "and".
    for ch in chunks:
        if ch.text.strip().endswith("and"):
            ch.trailing_silence_ms = rng.uniform(650.0, 1100.0)
    return chunks


def _turn_booking(rng: random.Random, *, held_out: bool) -> list[Chunk]:
    flight = rng.choice(FLIGHTS)
    verb = "reserve" if held_out else "book"
    pieces = [f"{verb} {flight}"]
    if rng.random() < 0.5:
        pieces = [verb, flight] if not held_out else ["go with", flight]
    return _to_chunks(rng, pieces)


def _turn_troubleshoot(rng: random.Random, *, held_out: bool) -> list[Chunk]:
    sound = rng.choice(SOUNDS)
    lead = (["the machine is", "it keeps", "there's a"] if held_out
            else ["the washing machine is", "it's", "listen it's"])
    pieces = [rng.choice(lead), f"{sound}"]
    return _to_chunks(rng, pieces)


def _to_chunks(rng: random.Random, pieces: list[str]) -> list[Chunk]:
    chunks = [Chunk(text=p, trailing_silence_ms=_mid_pause(rng)) for p in pieces]
    chunks[-1].trailing_silence_ms = _final_pause(rng)
    return chunks


def _noisify(rng: random.Random, text: str, *, level: float) -> str:
    """A light ASR-style perturbation, applied to the held-out split."""
    if level <= 0:
        return text
    tokens = text.split()
    if tokens and rng.random() < level:
        i = rng.randrange(len(tokens))
        tokens.insert(i, rng.choice(FILLED))
    return " ".join(tokens)


_BUILDERS = [_turn_flight, _turn_add_hotel, _turn_booking, _turn_troubleshoot]


def _completeness(turn_text: str) -> tuple[bool, bool, bool]:
    """(complete_request, has_intent, just_bound_value) for an accumulated turn,
    computed exactly as the live agent computes them: bind slots via the Lexicon,
    infer the intent, ask the Planner what is still missing.
    """
    state = SessionState(session_id="endpoint_corpus")
    matches = LEXICON.find(turn_text, prefer=None)
    bound = False
    for m in matches:
        if m.negated:
            continue
        state.set_slot(m.slot, m.value, confidence=m.confidence, source=SlotSource.TEXT,
                       evidence=m.surface, surface=m.surface)
        bound = True
    intent, _ = LEXICON.intent_for(turn_text)
    if intent is not None and state.intent is None:
        state.set_intent(intent)
    if state.intent is None and bound:
        inferred = PLANNER.infer_intent(state)
        if inferred:
            state.set_intent(inferred)
    has_intent = state.intent is not None
    complete = has_intent and not PLANNER.missing_for(state.intent, state)
    return complete, has_intent, bound


def generate(
    n: int = 4000, *, split: str = "train", seed: int = 20260925, noise: float = 0.0
) -> list[EndpointExample]:
    """Deterministic corpus of prefix examples. Same seed/split/noise => same set.

    One turn yields several examples (one per chunk boundary), so `n` is the
    number of turns, not examples; the returned list is longer.
    """
    held_out = split != "train"
    rng = random.Random(f"{seed}:{split}:{noise}")
    out: list[EndpointExample] = []

    for _ in range(n):
        chunks = rng.choice(_BUILDERS)(rng, held_out=held_out)
        accumulated: list[str] = []
        elapsed = 0.0
        last_idx = len(chunks) - 1
        for i, chunk in enumerate(chunks):
            text = _noisify(rng, chunk.text, level=noise)
            accumulated.append(text)
            turn_text = " ".join(accumulated)
            elapsed += _speak_ms(rng, text)
            complete, has_intent, just_bound = _completeness(turn_text)
            out.append(
                EndpointExample(
                    turn_text=turn_text,
                    silence_ms=chunk.trailing_silence_ms,
                    elapsed_ms=elapsed,
                    complete_request=complete,
                    has_intent=has_intent,
                    just_bound_value=just_bound,
                    label=(i == last_idx),
                )
            )
            elapsed += chunk.trailing_silence_ms
    return out
