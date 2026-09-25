"""Fit the endpointer and freeze it into the repo.

Run offline, never at scenario time:

    python scripts/train_endpointer.py

Writes `parley/agent/endpoint_model.json` (a few hundred bytes) and prints an
honest comparison against the two baselines the learned model has to beat:

    * silence-timeout @ 700 ms  — what a silence-based endpointer / Vosk does
    * lexical-completeness rule  — "done iff the request parses complete"

The number that means anything is the **false-early rate on held-out phrasings**:
declaring the turn over on a prefix that was still mid-sentence. That is the
expensive error (it dispatches on an incomplete request or interrupts the user),
so the threshold is chosen to bound it, not to maximise raw accuracy.

If a fitted neural model ever beats this on the held-out split it can be exported
to ONNX and loaded the same way; the decision and the numbers behind it are
written into the weights' metadata so the claim in the docs is reproducible.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import utf8

utf8()

import numpy as np

from parley.agent.endpoint_corpus import generate
from parley.agent.endpointer import (
    FEATURE_ORDER,
    WEIGHTS_PATH,
    EndpointModel,
    SilenceTimeoutBaseline,
    endpoint_features,
)

FALSE_EARLY_BUDGET = 0.03
"""Maximum tolerable rate of declaring an endpoint on a genuine mid-turn prefix,
on the dev split. The threshold is the lowest one that stays under this — lowest,
because a lower threshold catches more real endpoints, and a false-early is the
expensive error. 3% is an order of magnitude below either baseline's rate, so
the model is still overwhelmingly the safer choice while recovering recall a 1%
budget threw away."""


def featurise(examples) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    rows = [ex.features() for ex in examples]
    X = np.array([[r.get(k, 0.0) for k in FEATURE_ORDER] for r in rows])
    y = np.array([1 if ex.label else 0 for ex in examples])
    return X, y, rows


def rates(pred_done: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """Accuracy plus the two error rates, named by what they cost."""
    done = y == 1
    mid = y == 0
    acc = float((pred_done == y).mean())
    # false-early: predicted done while actually mid-turn (over the mid-turn set).
    false_early = float(pred_done[mid].mean()) if mid.any() else 0.0
    # false-late: predicted mid-turn while actually done (over the done set).
    false_late = float((1 - pred_done[done]).mean()) if done.any() else 0.0
    # recall of real endpoints.
    recall = float(pred_done[done].mean()) if done.any() else 0.0
    return {"acc": acc, "false_early": false_early, "false_late": false_late, "recall": recall}


def sweep_threshold(model: EndpointModel, rows: list[dict], y: np.ndarray) -> float:
    """Lowest threshold whose dev false-early rate stays under budget."""
    probs = np.array([model.probability(r) for r in rows])
    best = 0.5
    for thr in np.linspace(0.30, 0.95, 66):
        pred = (probs >= thr).astype(int)
        if rates(pred, y)["false_early"] <= FALSE_EARLY_BUDGET:
            best = float(round(thr, 3))
            break
    else:
        best = 0.95
    return best


def baseline_rates(name_fn, rows: list[dict], y: np.ndarray) -> dict[str, float]:
    pred = np.array([1 if name_fn(r) else 0 for r in rows])
    return rates(pred, y)


def main() -> int:
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        print("scikit-learn is a dev dependency: pip install -e '.[dev]'", file=sys.stderr)
        return 1

    train = generate(4000, split="train") + generate(1200, split="train", seed=31, noise=0.35)
    dev = generate(1000, split="eval", seed=101)
    test = generate(1000, split="eval", seed=202, noise=0.3)

    Xtr, ytr, _ = featurise(train)
    Xdev, ydev, dev_rows = featurise(dev)
    Xte, yte, test_rows = featurise(test)

    # Class weighting nudges the fit away from the majority (mid-turn prefixes
    # outnumber endpoints ~3:1), but the threshold sweep is what actually pins
    # the false-early rate.
    clf = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced",
                             random_state=0).fit(Xtr, ytr)

    model = EndpointModel.from_sklearn(clf, FEATURE_ORDER, threshold=0.5)
    model.threshold = sweep_threshold(model, dev_rows, ydev)

    def model_done(r):
        return model.is_endpoint(r)[0]

    silence = SilenceTimeoutBaseline(700.0)
    lexical = lambda r: bool(r.get("complete_request", 0.0)) and bool(r.get("trailing_content", 0.0))

    m_dev = baseline_rates(model_done, dev_rows, ydev)
    m_test = baseline_rates(model_done, test_rows, yte)
    s_dev = baseline_rates(lambda r: silence.is_endpoint(r)[0], dev_rows, ydev)
    s_test = baseline_rates(lambda r: silence.is_endpoint(r)[0], test_rows, yte)
    l_dev = baseline_rates(lexical, dev_rows, ydev)
    l_test = baseline_rates(lexical, test_rows, yte)

    # CPU inference latency on the hot path.
    sample = test_rows[0]
    t0 = time.perf_counter()
    N = 20000
    for _ in range(N):
        model.probability(sample)
    per_call_us = (time.perf_counter() - t0) / N * 1e6

    def row(name, d, t):
        return (f"{name:<26} {d['acc']:>6.3f} {d['false_early']:>8.3f} "
                f"{d['false_late']:>8.3f} {t['acc']:>7.3f} {t['false_early']:>9.3f}")

    print(f"features : {len(FEATURE_ORDER)}    "
          f"train {len(train)}  dev {len(dev)}  test {len(test)}  (prefix examples)\n")
    print(f"{'configuration':<26} {'devAcc':>6} {'devFE':>8} {'devFL':>8} {'testAcc':>7} {'testFE':>9}")
    print("-" * 70)
    print(row("silence-timeout 700ms", s_dev, s_test))
    print(row("lexical-completeness", l_dev, l_test))
    print(row("learned endpointer", m_dev, m_test))
    print()
    print(f"chosen threshold : {model.threshold:.3f}   "
          f"(false-early budget {FALSE_EARLY_BUDGET:.0%})")
    print(f"inference latency: {per_call_us:.1f} us/call  (target < 1000 us)\n")

    # The honest primary metrics are accuracy and the false-early rate (the
    # expensive error). Recall is reported but is deliberately traded down for
    # safety: a missed endpoint defers to the marker or session end, a false one
    # acts on a half-sentence. "Beats" means better on both accuracy and
    # false-early than each baseline — not recall parity with a reckless timeout.
    beats = (
        m_test["acc"] > s_test["acc"] and m_test["acc"] > l_test["acc"]
        and m_test["false_early"] < s_test["false_early"]
        and m_test["false_early"] < l_test["false_early"]
    )
    if beats:
        verdict = ("learned endpointer beats both baselines on accuracy and "
                   "false-early; used to override a missing/late/wrong marker "
                   f"(recall {m_test['recall']:.2f}, conservative by design)")
    else:
        verdict = "shipped as a confidence signal; agent still trusts the marker first"
    print(f"VERDICT: {verdict}")

    model.metadata = {
        "trained_on": "generated prefix corpus (parley.agent.endpoint_corpus)",
        "n_train_examples": len(train),
        "note": "held-out split shares no openers with training; includes the "
                "mid-turn-long-pause adversarial case",
        "false_early_budget": FALSE_EARLY_BUDGET,
        "threshold": model.threshold,
        "dev": {"model": _round(m_dev), "silence700": _round(s_dev), "lexical": _round(l_dev)},
        "test": {"model": _round(m_test), "silence700": _round(s_test), "lexical": _round(l_test)},
        "inference_us_per_call": round(per_call_us, 2),
        "verdict": verdict,
        "seed": {"train": 20260925, "dev": 101, "test": 202},
    }
    path = model.save(WEIGHTS_PATH)
    print(f"\nwrote {path} ({path.stat().st_size} bytes)")
    return 0


def _round(d: dict[str, float]) -> dict[str, float]:
    return {k: round(v, 4) for k, v in d.items()}


if __name__ == "__main__":
    raise SystemExit(main())
