"""The learned interruption classifier — training offline, inference in numpy.

Why a model at all, when the rules in `nlu.py` already work: the rules encode
the phrasings we thought of. The residue — paraphrases, unusual word order,
cues we did not list — is what a learned model is for, and it also gives a
calibrated confidence the rules cannot.

Why it is shaped like this, rather than a transformer:

    120 s wall-clock cap per scenario, 300 s warm-up, and no runtime downloads.

A model that pulls weights at start-up eats both the cap and the latency block.
So training happens offline with scikit-learn (a *dev* dependency), the fitted
coefficients are exported to a few kilobytes of JSON that lives in the repo, and
inference is a matrix multiply and a softmax — microseconds, zero virtual time,
bit-identical every run.

The feature vector is built by `nlu.extract_features`, the *same* function the
rules use. Train and serve cannot drift apart because there is only one
implementation.

This module degrades cleanly: with no weights file present, `load_default()`
returns None and the `Interpreter` runs on rules alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..kernel.policy import InterruptionKind

WEIGHTS_PATH = Path(__file__).with_name("interruption_model.json")


@dataclass
class InterruptionModel:
    """Multinomial logistic regression over the shared feature vector."""

    classes: list[InterruptionKind]
    feature_order: list[str]
    weights: np.ndarray  # (n_classes, n_features)
    bias: np.ndarray  # (n_classes,)
    metadata: dict[str, Any]

    # -- inference ---------------------------------------------------------

    def vectorise(self, features: dict[str, float]) -> np.ndarray:
        """Build the input vector in the order the model was trained on.

        Unknown feature names are ignored and missing ones are zero, so adding a
        feature to `extract_features` degrades an old model's accuracy rather
        than crashing the session mid-scenario.
        """
        return np.array([float(features.get(name, 0.0)) for name in self.feature_order])

    def predict(self, features: dict[str, float]) -> tuple[InterruptionKind, float]:
        logits = self.weights @ self.vectorise(features) + self.bias
        logits -= logits.max()  # stabilise before exp
        probs = np.exp(logits)
        probs /= probs.sum()
        idx = int(probs.argmax())
        return self.classes[idx], float(probs[idx])

    def predict_proba(self, features: dict[str, float]) -> dict[InterruptionKind, float]:
        logits = self.weights @ self.vectorise(features) + self.bias
        logits -= logits.max()
        probs = np.exp(logits)
        probs /= probs.sum()
        return {k: float(p) for k, p in zip(self.classes, probs)}

    # -- persistence -------------------------------------------------------

    def save(self, path: Path | str = WEIGHTS_PATH) -> Path:
        path = Path(path)
        path.write_text(
            json.dumps(
                {
                    "classes": [c.value for c in self.classes],
                    "feature_order": self.feature_order,
                    "weights": self.weights.tolist(),
                    "bias": self.bias.tolist(),
                    "metadata": self.metadata,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path: Path | str = WEIGHTS_PATH) -> InterruptionModel:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            classes=[InterruptionKind(c) for c in body["classes"]],
            feature_order=list(body["feature_order"]),
            weights=np.asarray(body["weights"], dtype=float),
            bias=np.asarray(body["bias"], dtype=float),
            metadata=body.get("metadata", {}),
        )

    @classmethod
    def load_default(cls) -> InterruptionModel | None:
        """Bundled weights, or None. Never raises: rules alone are a valid agent."""
        try:
            return cls.load(WEIGHTS_PATH)
        except (FileNotFoundError, KeyError, ValueError):
            return None

    @classmethod
    def from_sklearn(
        cls, clf: Any, feature_order: list[str], metadata: dict[str, Any] | None = None
    ) -> InterruptionModel:
        """Freeze a fitted `LogisticRegression` into plain arrays.

        Binary sklearn models keep a single coefficient row; expanding it to the
        two-row form here means inference has exactly one code path.
        """
        coef = np.asarray(clf.coef_, dtype=float)
        intercept = np.asarray(clf.intercept_, dtype=float)
        if coef.shape[0] == 1:
            coef = np.vstack([-coef[0], coef[0]])
            intercept = np.array([-intercept[0], intercept[0]])
        return cls(
            classes=[InterruptionKind(c) for c in clf.classes_],
            feature_order=list(feature_order),
            weights=coef,
            bias=intercept,
            metadata=metadata or {},
        )
