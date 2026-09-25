"""Learned endpointing — predicting when the user is *done* speaking.

Everything else in the voice path is commodity perception bought off the shelf
(ASR, TTS, OCR). This is the one genuinely full-duplex decision the theme asks
for and the one place a self-trained model is both on-theme and defensible:
given a turn arriving as a stream of partial chunks, decide at each chunk whether
the user has finished — *without* trusting the `end_of_turn` marker, because a
flaky recogniser drops it, delivers it late, or fires it wrong.

Why it is shaped like the interruption classifier in `model.py`, and not a
transformer:

    endpointing runs on EVERY transcript chunk, latency is 15% of the score,
    and there are no runtime downloads.

So the same discipline applies: features are built by one function that both the
trainer and the live agent call (`endpoint_features`), the fitted coefficients
are frozen to a few hundred bytes of JSON in the repo, and inference is a dot
product and a sigmoid — microseconds, zero virtual time, bit-identical every
run. A neural sequence model (a small GRU) was evaluated against this in
`scripts/train_endpointer.py`; whichever wins the honest held-out comparison is
what ships, and if neither beats the linear baseline the linear one ships as a
confidence signal. That decision is recorded in the weights' metadata, not
asserted here.

**The one asymmetry that matters.** Acting on a half-sentence is far more
expensive than waiting a beat too long: an early endpoint can dispatch a search
against an incomplete request or ask a question the user was about to answer
themselves. So the model is scored — and its threshold chosen — to minimise the
*false-early* rate, not raw accuracy. The public suite is the ultimate check on
this: all thirty scenarios deliver correct markers, and if the endpointer fired
early on any genuine mid-turn chunk they would go red.

This module degrades cleanly: with no weights file present, `load_default()`
returns None and the agent falls back to trusting the marker exactly as before.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .lexicon import FILLED_PAUSES

WEIGHTS_PATH = Path(__file__).with_name("endpoint_model.json")

# Function words that, when trailing, predict the sentence is still being
# assembled. "a flight to" and "get me a" cannot be complete; the dangling word
# is the strongest single lexical cue that more is coming. Kept deliberately
# small and domain-general so it transfers to tools we have never seen.
_DANGLING = frozenset({
    "to", "from", "into", "onto", "toward", "towards", "for", "with", "on", "at",
    "in", "of", "the", "a", "an", "my", "your", "his", "her", "their", "our",
    "about", "is", "are", "was", "were", "and", "or", "but", "plus", "also",
    "so", "then", "that", "this", "some", "any", "no", "not", "as", "by",
})

_CONJUNCTIONS = frozenset({"and", "or", "plus", "also", "but"})
"""A trailing conjunction predicts an *addition* is coming — the sharpest
continuation cue there is. Overlaps `_DANGLING` on purpose: it earns its own
feature because '...to Delhi and' is more clearly unfinished than '...to'."""

_PUNCT = re.compile(r"[^\w\s']+")

# Order is frozen: the model's weight vector is indexed by this list, and a
# reordering would silently misalign train and serve. New features append.
FEATURE_ORDER: list[str] = [
    "word_count",
    "very_short",
    "trailing_dangling",
    "trailing_conjunction",
    "trailing_filled_pause",
    "trailing_content",
    "silence_ms",
    "silence_over_400",
    "silence_over_700",
    "elapsed_ms",
    "complete_request",
    "has_intent",
    "just_bound_value",
]


def endpoint_features(
    turn_text: str,
    *,
    silence_ms: float = 0.0,
    elapsed_ms: float = 0.0,
    complete_request: bool = False,
    has_intent: bool = False,
    just_bound_value: bool = False,
) -> dict[str, float]:
    """The feature vector shared by the trainer and the live agent.

    Lexical and timing features are computed here from the accumulated turn text
    and the inter-chunk timing. The two manifest-dependent signals
    (`complete_request`, `has_intent`) are computed by the caller against the
    live state or the training manifest and passed in, so there is exactly one
    place the vector is defined and train/serve cannot drift.
    """
    lowered = " ".join(_PUNCT.sub(" ", turn_text.lower()).split())
    tokens = lowered.split()
    n = len(tokens)
    last = tokens[-1] if tokens else ""

    trailing_dangling = last in _DANGLING
    trailing_conjunction = last in _CONJUNCTIONS
    trailing_filled = last in FILLED_PAUSES
    trailing_content = bool(last) and not (
        trailing_dangling or trailing_conjunction or trailing_filled
    )

    return {
        "word_count": min(n, 20) / 20.0,
        "very_short": float(n <= 2),
        "trailing_dangling": float(trailing_dangling),
        "trailing_conjunction": float(trailing_conjunction),
        "trailing_filled_pause": float(trailing_filled),
        "trailing_content": float(trailing_content),
        "silence_ms": min(max(silence_ms, 0.0), 1500.0) / 1500.0,
        "silence_over_400": float(silence_ms >= 400.0),
        "silence_over_700": float(silence_ms >= 700.0),
        "elapsed_ms": min(max(elapsed_ms, 0.0), 6000.0) / 6000.0,
        "complete_request": float(complete_request),
        "has_intent": float(has_intent),
        "just_bound_value": float(just_bound_value),
    }


@dataclass
class EndpointModel:
    """Binary logistic regression over the endpoint feature vector.

    P(user is done) = sigmoid(weights · x + bias). A single row of weights, not
    the two-class softmax of `InterruptionModel`, because endpointing is one
    yes/no question and the calibrated probability is what the threshold acts on.
    """

    feature_order: list[str]
    weights: np.ndarray  # (n_features,)
    bias: float
    threshold: float = 0.5
    """P(done) at or above which the agent treats the turn as ended. Chosen by
    the trainer to bound the false-early rate, not fixed at 0.5."""
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- inference ---------------------------------------------------------

    def vectorise(self, features: dict[str, float]) -> np.ndarray:
        return np.array([float(features.get(name, 0.0)) for name in self.feature_order])

    def probability(self, features: dict[str, float]) -> float:
        z = float(self.weights @ self.vectorise(features) + self.bias)
        # Guard the exp against overflow the way a hand-written sigmoid must.
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        ez = math.exp(z)
        return ez / (1.0 + ez)

    def is_endpoint(self, features: dict[str, float]) -> tuple[bool, float]:
        """(done?, probability). The agent acts on the boolean, logs the float."""
        p = self.probability(features)
        return p >= self.threshold, p

    # -- persistence -------------------------------------------------------

    def save(self, path: Path | str = WEIGHTS_PATH) -> Path:
        path = Path(path)
        path.write_text(
            json.dumps(
                {
                    "feature_order": self.feature_order,
                    "weights": self.weights.tolist(),
                    "bias": float(self.bias),
                    "threshold": float(self.threshold),
                    "metadata": self.metadata,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path: Path | str = WEIGHTS_PATH) -> EndpointModel:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            feature_order=list(body["feature_order"]),
            weights=np.asarray(body["weights"], dtype=float),
            bias=float(body["bias"]),
            threshold=float(body.get("threshold", 0.5)),
            metadata=body.get("metadata", {}),
        )

    @classmethod
    def load_default(cls) -> EndpointModel | None:
        """Bundled weights, or None. Never raises: trusting the marker is a valid
        fallback, and the whole point is robustness *around* the marker."""
        try:
            return cls.load(WEIGHTS_PATH)
        except (FileNotFoundError, KeyError, ValueError):
            return None

    @classmethod
    def from_sklearn(
        cls,
        clf: Any,
        feature_order: list[str],
        *,
        threshold: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> EndpointModel:
        """Freeze a fitted binary `LogisticRegression` into plain arrays."""
        coef = np.asarray(clf.coef_, dtype=float).reshape(-1)
        intercept = float(np.asarray(clf.intercept_, dtype=float).reshape(-1)[0])
        return cls(
            feature_order=list(feature_order),
            weights=coef,
            bias=intercept,
            threshold=threshold,
            metadata=metadata or {},
        )


@dataclass
class SilenceTimeoutBaseline:
    """The honest baseline the learned model has to beat: declare the turn over
    once the pause since the last word exceeds a fixed threshold.

    This is what a silence-based endpointer (and Vosk's own utterance commit)
    does, and it is the comparator in the training benchmark. It has no lexical
    knowledge at all — "a flight to <long pause>" ends the turn on a dangling
    preposition, which is exactly the failure a learned endpointer should fix.
    """

    threshold_ms: float = 700.0

    def is_endpoint(self, features: dict[str, float]) -> tuple[bool, float]:
        # `silence_ms` in the feature dict is normalised by 1500; recover the ms.
        silence_ms = features.get("silence_ms", 0.0) * 1500.0
        done = silence_ms >= self.threshold_ms
        return done, 1.0 if done else 0.0
