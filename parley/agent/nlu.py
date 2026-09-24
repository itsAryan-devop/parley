"""Understanding an utterance: what changed, and what kind of interruption it is.

The hard pair is `SELF_REPAIR` vs `SLOT_CORRECTION`. Both look like
"X — no wait — Y" and they demand opposite behaviour: do nothing, versus cancel
that slot's readers. Shriberg's disfluency structure gives the discriminator
(RESEARCH.md R2.1) — both have a reparandum, an interregnum and a repair, and
they differ in **what the repair does to the value**:

    repair supplies a DIFFERENT value for a bound slot  -> SLOT_CORRECTION
    repair RESTATES a bound value, or supplies none     -> SELF_REPAIR

The interregnum type is corroborating evidence, not the decision. "uh, no,
Mumbai" contains both a filled pause and an editing term; the value comparison
still resolves it correctly.

Classification runs at **repair onset**, not at end-of-turn. Waiting for the
end-of-turn marker would forfeit the latency block on exactly the turns that
matter most.

Everything here is pure Python over a feature vector: no inference, no virtual
time consumed. The same feature vector feeds the learned classifier in
`model.py`, which arbitrates only the cases the rules are genuinely unsure about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..kernel.policy import InterruptionKind, InterruptionPolicy, policy_for
from ..protocol.state import SessionState
from .lexicon import (
    BACKCHANNELS,
    EDITING_TERMS,
    FILLED_PAUSES,
    FLOOR_GRABS,
    GOAL_SWITCH_CUES,
    REFINEMENT_CUES,
    REPEAT_CUES,
    Lexicon,
    Match,
)

NARROWING_SLOTS = frozenset({"time_of_day", "max_price", "cabin", "stops", "sort_by"})
"""Slots that narrow an existing result set rather than redefining the query.

Binding one of these for the first time is a refinement — the in-flight search
can still be filtered — whereas *changing* one invalidates the query that used it.
"""


@dataclass
class Turn:
    """One user utterance as the agent sees it."""

    text: str
    t: float = 0.0
    end_of_turn: bool = False
    overlapping_agent_speech: bool = False
    """True when this arrived while we were speaking. Distinguishes barge-in
    from a normal turn start."""


@dataclass
class Interpretation:
    kind: InterruptionKind
    policy: InterruptionPolicy
    confidence: float
    rationale: str

    matches: list[Match] = field(default_factory=list)
    corrections: list[Match] = field(default_factory=list)
    """Bound slots given a *different* value. These drive selective cancellation."""
    additions: list[Match] = field(default_factory=list)
    """Slots bound for the first time. These invalidate nothing."""
    restatements: list[Match] = field(default_factory=list)
    """Bound slots repeated with the same value. Evidence of self-repair."""

    intent: str | None = None
    intent_confidence: float = 0.0
    features: dict[str, float] = field(default_factory=dict)

    rule_kind: InterruptionKind | None = None
    model_kind: InterruptionKind | None = None
    model_confidence: float = 0.0
    arbitrated: bool = False
    """True when rules and model disagreed and the tie-break was applied."""

    @property
    def changed_slots(self) -> set[str]:
        return {m.slot for m in self.corrections}

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "policy": self.policy.to_payload(),
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale,
            "corrections": [{"slot": m.slot, "value": m.value, "span": m.surface} for m in self.corrections],
            "additions": [{"slot": m.slot, "value": m.value, "span": m.surface} for m in self.additions],
            "restatements": [{"slot": m.slot, "value": m.value} for m in self.restatements],
            "intent": self.intent,
            "rule_kind": self.rule_kind.value if self.rule_kind else None,
            "model_kind": self.model_kind.value if self.model_kind else None,
            "arbitrated": self.arbitrated,
            "features": {k: round(v, 3) for k, v in self.features.items()},
        }


_PUNCT = re.compile(r"[^\w\s']+")


def _normalise(text: str) -> str:
    """Lowercase, punctuation to spaces, whitespace collapsed.

    Cue phrases are multi-word ("hold on", "say that again"), so matching has to
    happen on a normalised string rather than on raw tokens. Punctuation is
    where this first went wrong: "uh, no, Mumbai" hid the editing term `no`
    behind a comma, and "what was that?" hid the repeat cue behind a question
    mark — both misclassified in a way that changes what gets cancelled.
    """
    return " ".join(_PUNCT.sub(" ", text.lower()).split())


def _contains_any(normalised: str, phrases) -> bool:
    padded = f" {normalised} "
    return any(f" {p} " in padded for p in phrases)


def _leading_cue(normalised: str, phrases) -> bool:
    """Cue in the first few tokens — where an interregnum actually appears."""
    return _contains_any(" ".join(normalised.split()[:4]), phrases)


def _residue(normalised: str, *phrase_sets) -> list[str]:
    """What is left after deleting every cue phrase.

    An utterance whose residue is empty carried no request — it was pure floor
    management. Phrase-level deletion is what token-level checks cannot do:
    "hold on" is a floor grab, while "hold" and "on" separately are not.
    """
    text = f" {normalised} "
    phrases = sorted(
        (p for s in phrase_sets for p in s), key=len, reverse=True
    )
    for phrase in phrases:
        text = text.replace(f" {phrase} ", " ")
    return text.split()


def extract_features(
    turn: Turn, state: SessionState, lexicon: Lexicon, *, in_flight: int = 0
) -> tuple[dict[str, float], list[Match], list[Match], list[Match], list[Match], str | None, float]:
    """Compute the feature vector shared by the rule path and the model path."""
    text = turn.text.strip()
    lowered = _normalise(text)
    tokens = lowered.split()

    matches = lexicon.find(text)
    corrections: list[Match] = []
    additions: list[Match] = []
    restatements: list[Match] = []

    for m in matches:
        current = state.slots.get(m.slot)
        if current is None:
            additions.append(m)
        elif current.value == m.value:
            restatements.append(m)
        else:
            corrections.append(m)

    intent, intent_conf = lexicon.intent_for(text)
    content_tokens = _residue(lowered, FILLED_PAUSES, EDITING_TERMS)
    floor_residue = _residue(lowered, FILLED_PAUSES, FLOOR_GRABS)

    features = {
        "filled_pause": float(any(t in FILLED_PAUSES for t in tokens)),
        "editing_term": float(_contains_any(lowered, EDITING_TERMS)),
        "goal_switch_cue": float(_contains_any(lowered, GOAL_SWITCH_CUES)),
        "refinement_cue": float(_contains_any(lowered, REFINEMENT_CUES)),
        "repeat_cue": float(_contains_any(lowered, REPEAT_CUES)),
        "floor_grab_only": float(bool(tokens) and not floor_residue),
        "backchannel_only": float(bool(tokens) and _contains_any(lowered, BACKCHANNELS) and len(tokens) <= 2),
        "n_corrections": float(len(corrections)),
        "n_additions": float(len(additions)),
        "n_restatements": float(len(restatements)),
        "narrowing_addition": float(any(m.slot in NARROWING_SLOTS for m in additions)),
        "intent_detected": float(intent is not None),
        "intent_differs": float(intent is not None and state.intent is not None and intent != state.intent),
        "intent_confidence": intent_conf,
        "overlapping": float(turn.overlapping_agent_speech),
        "in_flight": float(min(in_flight, 5)) / 5.0,
        "has_in_flight": float(in_flight > 0),
        "n_content_tokens": float(min(len(content_tokens), 20)) / 20.0,
        "no_content": float(len(content_tokens) == 0),
        "end_of_turn": float(turn.end_of_turn),
    }
    return features, matches, corrections, additions, restatements, intent, intent_conf


def classify_by_rules(f: dict[str, float]) -> tuple[InterruptionKind, float, str]:
    """Deterministic classification over the feature vector.

    Ordered most-specific first. Each branch names the evidence it fired on so
    the trace explains itself — "where did that come from?" is a question the
    jury will ask, and the answer should be in the log.
    """
    # A listener noise is not an interruption. VAD-triggered systems stop
    # speaking on "mhm", which is precisely wrong.
    if f["backchannel_only"] and f["overlapping"]:
        return InterruptionKind.BACKCHANNEL, 0.95, "backchannel while we were speaking"

    if f["repeat_cue"]:
        return InterruptionKind.REPEAT_REQUEST, 0.93, "explicit request to repeat"

    if f["goal_switch_cue"] or (f["intent_differs"] and f["intent_confidence"] >= 0.6):
        return InterruptionKind.GOAL_SWITCH, 0.88, "a different goal was named"

    if f["n_corrections"] > 0:
        return (
            InterruptionKind.SLOT_CORRECTION,
            0.9 if f["editing_term"] else 0.8,
            "a bound slot was given a different value",
        )

    if f["refinement_cue"] or f["narrowing_addition"]:
        if f["has_in_flight"] or f["n_restatements"] > 0:
            return InterruptionKind.REFINEMENT, 0.85, "narrows the query already running"

    # Checked before self-repair, because "wait" is both a floor grab and an
    # editing term. Over our speech it means give me the microphone; in silence
    # it means the speaker is still assembling the sentence.
    if f["overlapping"] and (f["floor_grab_only"] or f["no_content"]):
        return InterruptionKind.BARGE_IN, 0.9, "took the floor without changing the request"

    # Interregnum with no value change: the speaker stumbled and resumed.
    if (f["filled_pause"] or f["editing_term"]) and f["n_corrections"] == 0:
        if f["n_restatements"] > 0 or f["no_content"] or f["n_additions"] == 0:
            return InterruptionKind.SELF_REPAIR, 0.82, "hesitation with no change of value"

    if f["overlapping"] and f["has_in_flight"] and f["n_additions"] == 0:
        return InterruptionKind.BARGE_IN, 0.7, "spoke over us with nothing new"

    return InterruptionKind.NEW_REQUEST, 0.75, "opening or continuing a request"


class Interpreter:
    """Turns an utterance into an interpretation, with a policy attached.

    The learned classifier is optional. When present it runs in parallel, both
    verdicts go into the trace, and disagreements are arbitrated by the rule on
    the branches where a wrong answer destroys work (`GOAL_SWITCH`,
    `SLOT_CORRECTION`) and by the model elsewhere.
    """

    #: Kinds where a false positive cancels real work, so rules win ties.
    DESTRUCTIVE = frozenset({InterruptionKind.GOAL_SWITCH, InterruptionKind.SLOT_CORRECTION})

    def __init__(self, lexicon: Lexicon, model: Any | None = None) -> None:
        self.lexicon = lexicon
        self.model = model

    def interpret(self, turn: Turn, state: SessionState, *, in_flight: int = 0) -> Interpretation:
        features, matches, corrections, additions, restatements, intent, intent_conf = extract_features(
            turn, state, self.lexicon, in_flight=in_flight
        )

        rule_kind, rule_conf, rationale = classify_by_rules(features)
        kind, confidence = rule_kind, rule_conf
        model_kind: InterruptionKind | None = None
        model_conf = 0.0
        arbitrated = False

        if self.model is not None:
            model_kind, model_conf = self.model.predict(features)
            if model_kind is not rule_kind:
                arbitrated = True
                if rule_kind in self.DESTRUCTIVE or model_kind in self.DESTRUCTIVE:
                    kind, confidence = rule_kind, min(rule_conf, 0.75)
                    rationale += "; model disagreed, rule kept (destructive branch)"
                elif model_conf > rule_conf:
                    kind, confidence = model_kind, model_conf
                    rationale += "; model overrode rule"
            else:
                confidence = min(0.99, (rule_conf + model_conf) / 2 + 0.05)

        return Interpretation(
            kind=kind,
            policy=policy_for(kind),
            confidence=confidence,
            rationale=rationale,
            matches=matches,
            corrections=corrections,
            additions=additions,
            restatements=restatements,
            intent=intent,
            intent_confidence=intent_conf,
            features=features,
            rule_kind=rule_kind,
            model_kind=model_kind,
            model_confidence=model_conf,
            arbitrated=arbitrated,
        )
