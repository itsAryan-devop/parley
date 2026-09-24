"""Fit the interruption classifier and freeze it into the repo.

Run offline, never at scenario time:

    python scripts/train_classifier.py

Writes `parley/agent/interruption_model.json` (a few kB) and prints accuracy on
the training phrasings, on held-out phrasings, and a rules-vs-model comparison.

The held-out number is the one that means anything. `EVAL_TEMPLATES` shares no
surface forms with `TRAIN_TEMPLATES`, so it measures generalisation to phrasings
neither the rules nor the training data contain — which is the only reason to
carry a model alongside a rule engine at all.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from parley.agent.corpus import Example, generate
from parley.agent.lexicon import Lexicon
from parley.agent.model import WEIGHTS_PATH, InterruptionModel
from parley.agent.nlu import Interpreter, classify_by_rules, extract_features
from parley.kernel.policy import InterruptionKind
from parley.protocol import parse_manifest

MANIFEST = parse_manifest(
    [
        {"name": "search_flights", "description": "search available flights", "read_only": True,
         "intent": "book_flight",
         "params": [{"name": "origin"}, {"name": "destination"}, {"name": "date"},
                    {"name": "time_of_day", "enum": ["morning", "afternoon", "evening"]}]},
        {"name": "book_flight", "mutating": True, "intent": "book_flight",
         "params": [{"name": "flight_no"}]},
        {"name": "search_hotels", "description": "search available hotels", "read_only": True,
         "intent": "book_hotel", "params": [{"name": "city"}, {"name": "date"}]},
    ]
)

LEXICON = Lexicon.for_manifest(MANIFEST)


def featurise(examples: list[Example]) -> tuple[np.ndarray, list[str], list[str], list[dict]]:
    rows: list[dict[str, float]] = []
    labels: list[str] = []
    for ex in examples:
        feats, *_ = extract_features(ex.turn, ex.state, LEXICON, in_flight=ex.in_flight)
        rows.append(feats)
        labels.append(ex.label.value)

    order = sorted(rows[0])
    X = np.array([[r.get(k, 0.0) for k in order] for r in rows])
    return X, order, labels, rows


def rule_accuracy(rows: list[dict], labels: list[str]) -> tuple[float, Counter]:
    correct = 0
    misses: Counter = Counter()
    for feats, truth in zip(rows, labels):
        kind, _, _ = classify_by_rules(feats)
        if kind.value == truth:
            correct += 1
        else:
            misses[f"{truth} -> {kind.value}"] += 1
    return correct / len(labels), misses


def interpreter_accuracy(examples: list[Example], model: InterruptionModel | None) -> float:
    """Accuracy of the thing that actually ships.

    Neither component's solo score is the number that matters — the agent runs
    `Interpreter`, which arbitrates between them. Measuring the parts and
    shipping the whole is how an ensemble quietly ends up worse than its best
    member.
    """
    interp = Interpreter(LEXICON, model=model)
    correct = sum(
        interp.interpret(ex.turn, ex.state, in_flight=ex.in_flight).kind is ex.label
        for ex in examples
    )
    return correct / len(examples)


def main() -> int:
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        print("scikit-learn is a dev dependency: pip install -e '.[dev]'", file=sys.stderr)
        return 1

    # Evaluation sees held-out phrasings *and* ASR-style noise, because 30% of
    # the hidden set is audio and the transcript the classifier reads will not
    # be the words the user said.
    #
    # Training is augmented with the same kind of noise at a spread of levels.
    # Fitting on clean text alone cost the model 16 accuracy points under noise
    # (0.73 vs 0.89 for rules) -- a textbook train/serve distribution shift, and
    # the reason the augmented mixture below exists.
    train = (
        generate(4000, split="train")
        + generate(1500, split="train", seed=7, noise=0.25)
        + generate(1500, split="train", seed=11, noise=0.5)
    )
    held_out = generate(800, split="eval")
    noisy = generate(800, split="eval", noise=0.45)

    X, order, y, train_rows = featurise(train)
    Xe, order_e, ye, eval_rows = featurise(held_out)
    Xn, order_n, yn, noisy_rows = featurise(noisy)
    assert order == order_e == order_n, "feature order drifted between splits"

    # Multinomial (softmax) is the default for multiclass with lbfgs; the
    # explicit `multi_class` argument was removed in scikit-learn 1.7.
    clf = LogisticRegression(max_iter=2000, C=1.0, random_state=0).fit(X, y)

    model = InterruptionModel.from_sklearn(
        clf,
        order,
        metadata={
            "trained_on": "generated corpus (parley.agent.corpus)",
            "n_train": len(train),
            "n_held_out": len(held_out),
            "note": "held-out split shares no surface forms with training templates",
        },
    )

    train_acc = float(clf.score(X, y))
    eval_acc = float(clf.score(Xe, ye))
    noisy_acc = float(clf.score(Xn, yn))
    rules_train, _ = rule_accuracy(train_rows, y)
    rules_eval, _ = rule_accuracy(eval_rows, ye)
    rules_noisy, rule_misses = rule_accuracy(noisy_rows, yn)

    model.metadata |= {
        "accuracy_train": round(train_acc, 4),
        "accuracy_held_out": round(eval_acc, 4),
        "accuracy_held_out_noisy": round(noisy_acc, 4),
        "rules_accuracy_train": round(rules_train, 4),
        "rules_accuracy_held_out": round(rules_eval, 4),
        "rules_accuracy_held_out_noisy": round(rules_noisy, 4),
    }
    path = model.save(WEIGHTS_PATH)

    print(f"features : {len(order)}    classes : {len(clf.classes_)}\n")
    print(f"{'split':<28} {'rules':>8} {'model':>8}")
    print(f"{'-' * 46}")
    print(f"{'train phrasings':<28} {rules_train:>8.3f} {train_acc:>8.3f}")
    print(f"{'held-out phrasings':<28} {rules_eval:>8.3f} {eval_acc:>8.3f}")
    print(f"{'held-out + ASR noise':<28} {rules_noisy:>8.3f} {noisy_acc:>8.3f}  <- the meaningful one")
    print()

    if rule_misses:
        print("rule misses under noise:")
        for pair, n in rule_misses.most_common(10):
            print(f"  {n:4d}  {pair}")
        print()

    # Where the two paths disagree is what the arbitration policy exists to handle.
    disagreements: Counter = Counter()
    model_saves = 0
    model_harms = 0
    for feats, truth in zip(noisy_rows, yn):
        rule_kind, _, _ = classify_by_rules(feats)
        model_kind, _ = model.predict(feats)
        if rule_kind is not model_kind:
            disagreements[f"rule={rule_kind.value} model={model_kind.value} truth={truth}"] += 1
            if model_kind.value == truth:
                model_saves += 1
            elif rule_kind.value == truth:
                model_harms += 1

    print(f"disagreements under noise: {sum(disagreements.values())}/{len(yn)}"
          f"   model right {model_saves}, rule right {model_harms}")
    for pair, n in disagreements.most_common(8):
        print(f"  {n:4d}  {pair}")

    # ---- the decision: does shipping the model beat shipping rules alone? ----
    # Tuned on a dev split and reported on a test split, both noisy, neither the
    # set the model was fitted on.
    dev = generate(800, split="eval", seed=101, noise=0.45)
    test = generate(800, split="eval", seed=202, noise=0.45)

    print("\n" + "=" * 62)
    print("ENSEMBLE DECISION — accuracy of the Interpreter as it actually ships")
    print("=" * 62)
    print(f"{'configuration':<28} {'dev':>10} {'test':>10}")
    print("-" * 62)
    rules_only = (interpreter_accuracy(dev, None), interpreter_accuracy(test, None))
    with_model = (interpreter_accuracy(dev, model), interpreter_accuracy(test, model))
    print(f"{'rules only':<28} {rules_only[0]:>10.3f} {rules_only[1]:>10.3f}")
    print(f"{'rules + model arbitrated':<28} {with_model[0]:>10.3f} {with_model[1]:>10.3f}")

    keep = with_model[0] > rules_only[0]
    print()
    if keep:
        print(f"VERDICT: keep the model ({with_model[1] - rules_only[1]:+.3f} on test)")
    else:
        print("VERDICT: the model does not beat rules alone on the dev split.")
        print("         Shipping it as a confidence signal only; rules decide.")
    model.metadata |= {
        "ensemble_dev_rules_only": round(rules_only[0], 4),
        "ensemble_dev_with_model": round(with_model[0], 4),
        "ensemble_test_rules_only": round(rules_only[1], 4),
        "ensemble_test_with_model": round(with_model[1], 4),
        "verdict": "model_arbitrates" if keep else "rules_decide_model_advises",
    }
    model.save(WEIGHTS_PATH)

    print(f"\nwrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
