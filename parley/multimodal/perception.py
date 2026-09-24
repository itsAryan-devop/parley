"""Perception results, and the tiny classifier both modalities share.

Objective 5: "Process raw audio and frames behind conversational
acknowledgments; **clarify ambiguous perceptions**." The second half is the part
most systems skip, and it is the part that scores — a specific question earns
credit where a confident wrong answer loses it twice (task completion, and the
truthfulness term in the quality multiplier).

So `Perception` has three outcomes, not two:

    confident   -> bind the slot and carry on
    ambiguous   -> two labels too close to separate; ASK, naming both
    unreadable  -> nothing above the floor; ASK, describing the difficulty

`ambiguous` is decided by the *margin* between the top two labels rather than by
the top score alone. A frame that is 0.45/0.43 between a red power LED and an
amber WAN LED is not a 0.45-confidence answer — it is a coin flip, and the only
honest move is to ask which one the user means.

`LabelModel` is a softmax over named labels with weights loaded from JSON. Same
reasoning as the interruption classifier: train offline, ship kilobytes, infer
in numpy, download nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

AMBIGUITY_MARGIN = 0.15
"""Top-two probabilities closer than this are a coin flip, not an answer."""

GROUNDING_FLOOR = 0.35
"""Below this nothing is claimed at all."""


@dataclass
class Perception:
    slot: str
    label: str | None
    confidence: float
    candidates: list[str] = field(default_factory=list)
    ambiguous: bool = False
    question: str | None = None
    source_id: str = ""
    modality: str = "vision"
    features: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    evidence: str | None = None
    """What the label rests on, in words, when it is something more specific
    than "the classifier said so" -- e.g. `panel reads E4`. Carried into the
    trace so a grounded claim can cite its warrant rather than assert it."""
    overrode: str | None = None
    """Set when a stronger witness contradicted the classifier. A silent
    override is indistinguishable from a bug three weeks later."""
    text: Any = None
    """The `ocr.FrameText` behind the decision, when text was read."""

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "slot": self.slot,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "candidates": self.candidates,
            "ambiguous": self.ambiguous,
            "question": self.question,
            "source_id": self.source_id,
            "modality": self.modality,
            "error": self.error,
            "features": {k: round(v, 4) for k, v in self.features.items()},
        }
        if self.evidence:
            payload["evidence"] = self.evidence
        if self.overrode:
            payload["overrode"] = self.overrode
        if self.text is not None and hasattr(self.text, "to_payload"):
            payload["read"] = self.text.to_payload()
        return payload


def decide(
    slot: str,
    probs: dict[str, float],
    *,
    source_id: str,
    modality: str,
    features: dict[str, float],
    phrase: str = "what I'm looking at",
    out_of_distribution: bool = False,
) -> Perception:
    """Turn a probability distribution into one of the three outcomes."""
    if not probs:
        return Perception(
            slot=slot, label=None, confidence=0.0, source_id=source_id,
            modality=modality, features=features, error="no classifier available",
        )

    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    (top_label, top_p) = ranked[0]
    runner_p = ranked[1][1] if len(ranked) > 1 else 0.0

    if out_of_distribution:
        # The input does not resemble anything the classifier was fitted on, so
        # its probabilities are extrapolation rather than evidence. A softmax
        # will happily report 0.97 for a blown-out photograph; the distance
        # check is what stops us believing it.
        return Perception(
            slot=slot, label=None, confidence=top_p,
            candidates=[lbl for lbl, _ in ranked[:3]],
            source_id=source_id, modality=modality, features=features,
            error="out of distribution",
        )

    if top_p < GROUNDING_FLOOR:
        return Perception(
            slot=slot, label=None, confidence=top_p,
            candidates=[lbl for lbl, _ in ranked[:3]],
            source_id=source_id, modality=modality, features=features,
        )

    if top_p - runner_p < AMBIGUITY_MARGIN:
        pair = [ranked[0][0], ranked[1][0]]
        return Perception(
            slot=slot, label=None, confidence=top_p, candidates=pair,
            ambiguous=True,
            question=f"I can't tell from {phrase} — is it {_human(pair[0])} or {_human(pair[1])}?",
            source_id=source_id, modality=modality, features=features,
        )

    return Perception(
        slot=slot, label=top_label, confidence=top_p,
        candidates=[lbl for lbl, _ in ranked[:3]],
        source_id=source_id, modality=modality, features=features,
    )


def _human(label: str) -> str:
    return label.replace("_", " ")


@dataclass
class LabelModel:
    """Calibrated softmax over named labels, with an abstention rule.

    A plain logistic regression is badly overconfident away from its training
    distribution — measured here, it labelled *blown-out photographs* and
    *frames with two LEDs lit at once* with ~0.9 confidence, which is precisely
    the confidently-wrong perception objective 5 penalises twice.

    Two additions fix it, and neither costs anything at runtime:

    **Temperature.** Logits are divided by a scalar fitted on held-out data, so
    the reported probability means something. Without it the top-two margin is
    saturated and the ambiguity test can never fire.

    **Mahalanobis abstention.** The distance from the input to the nearest class
    centroid, in whitened feature space. Beyond a threshold set from the
    training distribution's own spread, the classifier is extrapolating rather
    than recognising, and the honest output is "I can't tell" rather than a
    number.

    Together these turn "always answers" into "answers, asks, or declines" —
    which is the three-outcome behaviour `decide` needs and the scoring rewards.
    """

    labels: list[str]
    feature_order: list[str]
    weights: np.ndarray
    bias: np.ndarray
    mu: np.ndarray
    sigma: np.ndarray
    temperature: float = 1.0
    centroids: np.ndarray | None = None
    precision: np.ndarray | None = None
    ood_threshold: float = float("inf")
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- inference ---------------------------------------------------------

    def _z(self, features: dict[str, float]) -> np.ndarray:
        x = np.array([float(features.get(n, 0.0)) for n in self.feature_order])
        return (x - self.mu) / self.sigma

    def probs(self, features: dict[str, float]) -> dict[str, float]:
        logits = (self.weights @ self._z(features) + self.bias) / max(self.temperature, 1e-6)
        logits -= logits.max()
        e = np.exp(logits)
        e /= e.sum()
        return {lbl: float(p) for lbl, p in zip(self.labels, e)}

    def ood_distance(self, features: dict[str, float]) -> float:
        """Mahalanobis distance to the nearest class centroid."""
        if self.centroids is None or self.precision is None:
            return 0.0
        z = self._z(features)
        deltas = z[None, :] - self.centroids
        d2 = np.einsum("ij,jk,ik->i", deltas, self.precision, deltas)
        return float(np.sqrt(max(d2.min(), 0.0)))

    def is_out_of_distribution(self, features: dict[str, float]) -> bool:
        return self.ood_distance(features) > self.ood_threshold

    # -- persistence -------------------------------------------------------

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.write_text(
            json.dumps(
                {
                    "labels": self.labels,
                    "feature_order": self.feature_order,
                    "weights": self.weights.tolist(),
                    "bias": self.bias.tolist(),
                    "mu": self.mu.tolist(),
                    "sigma": self.sigma.tolist(),
                    "temperature": self.temperature,
                    "centroids": None if self.centroids is None else self.centroids.tolist(),
                    "precision": None if self.precision is None else self.precision.tolist(),
                    "ood_threshold": self.ood_threshold,
                    "metadata": self.metadata,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path: Path | str) -> LabelModel:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        n = len(body["feature_order"])
        return cls(
            labels=list(body["labels"]),
            feature_order=list(body["feature_order"]),
            weights=np.asarray(body["weights"], dtype=float),
            bias=np.asarray(body["bias"], dtype=float),
            mu=np.asarray(body.get("mu", np.zeros(n)), dtype=float),
            sigma=np.asarray(body.get("sigma", np.ones(n)), dtype=float),
            temperature=float(body.get("temperature", 1.0)),
            centroids=None if body.get("centroids") is None else np.asarray(body["centroids"], dtype=float),
            precision=None if body.get("precision") is None else np.asarray(body["precision"], dtype=float),
            ood_threshold=float(body.get("ood_threshold", float("inf"))),
            metadata=body.get("metadata", {}),
        )

    @classmethod
    def load_or_none(cls, path: Path | str) -> LabelModel | None:
        try:
            return cls.load(path)
        except (FileNotFoundError, KeyError, ValueError):
            return None
