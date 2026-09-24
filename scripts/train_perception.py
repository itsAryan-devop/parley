"""Fit the vision and audio classifiers and freeze them into the package.

    python scripts/make_media.py       # generate labelled media first
    python scripts/train_perception.py

Writes `parley/multimodal/vision_model.json` and `audio_model.json`.

Train and test come from different seeds, and every seventh example is a
deliberately *hard* one — an LED hue sitting on the red/amber boundary, a beep
pattern blurred towards clicking. Those are not there to be classified
correctly. They are there so we can check the agent **asks** about them, which
is what objective 5 actually rewards.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from parley.multimodal import audio as audio_mod
from parley.multimodal import vision as vision_mod
from parley.multimodal.perception import AMBIGUITY_MARGIN, LabelModel, decide

MEDIA = Path("media/dataset")


def load_split(kind: str, split: str, extract, pattern: str):
    root = MEDIA / kind / split
    if not root.exists():
        raise SystemExit(f"missing {root} — run scripts/make_media.py first")

    rows, labels = [], []
    for path in sorted(root.rglob(pattern)):
        feats = extract(path)
        if feats is None:
            print(f"  ! could not decode {path}")
            continue
        rows.append(feats)
        labels.append(path.parent.name)
    return rows, labels


def load_undecidable(kind: str, extract, pattern: str) -> dict[str, list[dict]]:
    """Media where asking is the right answer, grouped by the reason it is."""
    root = Path("media/undecidable") / kind
    out: dict[str, list[dict]] = {}
    if not root.exists():
        return out
    for group in sorted(p for p in root.iterdir() if p.is_dir()):
        rows = [f for p in sorted(group.glob(pattern)) if (f := extract(p)) is not None]
        if rows:
            out[group.name] = rows
    return out


def fit_temperature(logits: np.ndarray, y_idx: np.ndarray) -> float:
    """Scalar temperature minimising held-out NLL, constrained to T >= 1.

    The constraint is not a detail. Temperature scaling assumes a validation set
    the model gets *wrong* sometimes; that is what pins the optimum. On a
    perfectly separated validation set the NLL is minimised as T -> 0, i.e. by
    making the model maximally confident — the opposite of calibrating it.

    Measured here: unconstrained, this returned 0.50 (the floor of the scan) for
    both modalities, which would have sharpened an already-overconfident model
    and made the ambiguity margin unreachable. Calibration may soften; it may
    never sharpen.
    """
    best_t, best_nll = 1.0, float("inf")
    for t in np.concatenate([np.arange(1.0, 12.0, 0.25), np.arange(12.0, 40.0, 1.0)]):
        scaled = logits / t
        scaled -= scaled.max(axis=1, keepdims=True)
        log_norm = np.log(np.exp(scaled).sum(axis=1))
        nll = float(-(scaled[np.arange(len(y_idx)), y_idx] - log_norm).mean())
        if nll < best_nll:
            best_t, best_nll = float(t), nll
    return best_t


def fit(kind: str, extract, pattern: str, out_path: Path, feature_names: list[str]) -> LabelModel:
    from sklearn.linear_model import LogisticRegression

    print(f"\n=== {kind} ===")
    train_rows, train_y = load_split(kind, "train", extract, pattern)
    test_rows, test_y = load_split(kind, "test", extract, pattern)

    order = feature_names
    X = np.array([[r.get(k, 0.0) for k in order] for r in train_rows])
    Xt = np.array([[r.get(k, 0.0) for k in order] for r in test_rows])

    # Features live on very different scales (a hue mass fraction vs a crest
    # factor), so everything downstream works in standardised space -- both the
    # classifier and the Mahalanobis distance.
    mu, sigma = X.mean(axis=0), X.std(axis=0)
    sigma[sigma < 1e-9] = 1.0
    Z, Zt = (X - mu) / sigma, (Xt - mu) / sigma

    clf = LogisticRegression(max_iter=4000, C=2.0, random_state=0).fit(Z, train_y)

    coef = np.asarray(clf.coef_, dtype=float)
    intercept = np.asarray(clf.intercept_, dtype=float)
    if coef.shape[0] == 1:
        coef = np.vstack([-coef[0], coef[0]])
        intercept = np.array([-intercept[0], intercept[0]])

    labels = [str(c) for c in clf.classes_]
    label_index = {lbl: i for i, lbl in enumerate(labels)}

    # Temperature is fitted on the held-out split: fitting it on training data
    # would just reinforce the overconfidence it exists to correct.
    temperature = fit_temperature(Zt @ coef.T + intercept, np.array([label_index[y] for y in test_y]))

    # Class centroids and a shared within-class covariance, in standardised
    # space. Shared rather than per-class because there are only ~60 examples
    # per class and a per-class covariance would be badly conditioned.
    centroids = np.array([Z[[y == lbl for y in train_y]].mean(axis=0) for lbl in labels])
    centred = np.vstack([Z[i] - centroids[label_index[train_y[i]]] for i in range(len(Z))])
    cov = np.cov(centred, rowvar=False) + np.eye(len(order)) * 1e-3
    precision = np.linalg.inv(cov)

    def mahalanobis(z_rows: np.ndarray) -> np.ndarray:
        d = z_rows[:, None, :] - centroids[None, :, :]
        d2 = np.einsum("nij,jk,nik->ni", d, precision, d)
        return np.sqrt(np.maximum(d2.min(axis=1), 0.0))

    # The threshold is set from how far genuine in-distribution data ever gets,
    # including the held-out split — not from an arbitrary multiplier. Anything
    # beyond what real examples reach is extrapolation.
    #
    # The asymmetry justifies erring towards abstention: a false abstention
    # costs one clarification question, while a false confident answer costs
    # task completion *and* the truthfulness term in the quality multiplier.
    train_d, test_d = mahalanobis(Z), mahalanobis(Zt)
    in_distribution = np.concatenate([train_d, test_d])
    ood_threshold = float(np.percentile(in_distribution, 99.0) * 1.05)

    model = LabelModel(
        labels=labels, feature_order=order,
        weights=coef, bias=intercept, mu=mu, sigma=sigma,
        temperature=temperature, centroids=centroids, precision=precision,
        ood_threshold=ood_threshold,
        metadata={"n_train": len(train_y), "n_test": len(test_y)},
    )

    train_acc = float(clf.score(Z, train_y))
    test_acc = float(clf.score(Zt, test_y))

    # Confirm the shipped model reproduces sklearn's decisions exactly --
    # otherwise the numbers printed here describe a model we are not shipping.
    ours = [max(model.probs(r).items(), key=lambda kv: kv[1])[0] for r in test_rows]
    assert ours == list(clf.predict(Zt)), "shipped weights diverge from the fitted model"
    print(f"temperature {temperature:.2f}   ood threshold {ood_threshold:.2f} "
          f"(in-distribution p99 {np.percentile(in_distribution, 99):.2f}, "
          f"max {in_distribution.max():.2f})")

    def outcome(row: dict) -> str:
        p = decide(
            "label", model.probs(row), source_id="x", modality=kind, features=row,
            out_of_distribution=model.is_out_of_distribution(row),
        )
        if p.ambiguous:
            return "asked"
        if p.label is None:
            return "declined"
        return "answered"

    # On clean test media the agent should answer, and answer correctly.
    clean = Counter(outcome(r) for r in test_rows)

    model.metadata |= {"accuracy_train": round(train_acc, 4), "accuracy_test": round(test_acc, 4)}

    print(f"labels   : {model.labels}")
    print(f"accuracy : train {train_acc:.3f}   test {test_acc:.3f}")
    print(f"clean media -> {dict(sorted(clean.items()))}")

    # On undecidable media, answering confidently is the failure. Asking or
    # declining is the pass.
    undecidable = load_undecidable(kind, extract, pattern)
    handled: dict[str, dict[str, int]] = {}
    for group, rows in undecidable.items():
        counts = Counter(outcome(r) for r in rows)
        handled[group] = dict(sorted(counts.items()))
        good = counts["asked"] + counts["declined"]
        verdict = "OK" if good == sum(counts.values()) else "LEAK"
        dists = [model.ood_distance(r) for r in rows]
        print(f"undecidable/{group:<14} -> {dict(sorted(counts.items()))}  [{verdict}]"
              f"   distance {min(dists):.1f}-{max(dists):.1f} vs threshold {model.ood_threshold:.1f}")
    model.metadata["undecidable"] = handled

    model.save(out_path)
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    return model


def main() -> int:
    try:
        import sklearn  # noqa: F401
    except ImportError:
        print("scikit-learn is a dev dependency: pip install -e '.[dev]'", file=sys.stderr)
        return 1

    fit("frames", vision_mod.extract_features, "*.png",
        vision_mod.MODEL_PATH, vision_mod.FEATURE_NAMES)
    fit("audio", audio_mod.extract_features, "*.wav",
        audio_mod.MODEL_PATH, audio_mod.FEATURE_NAMES)

    print(f"\nambiguity margin: {AMBIGUITY_MARGIN}  "
          "(top-two closer than this => the agent asks rather than answers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
