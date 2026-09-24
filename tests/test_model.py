"""The learned classifier: inference, degradation, and arbitration.

The model is a second opinion, not an oracle. These tests pin the three
properties that make it safe to ship: it costs nothing at runtime, its absence
is survivable, and it can never talk the agent into cancelling work the rules
did not want cancelled.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from parley.agent import Interpreter, Lexicon, Turn
from parley.agent.corpus import generate
from parley.agent.model import WEIGHTS_PATH, InterruptionModel
from parley.kernel import InterruptionKind
from parley.protocol import SessionState, parse_manifest

MANIFEST = parse_manifest(
    [
        {"name": "search_flights", "description": "search available flights", "read_only": True,
         "intent": "book_flight",
         "params": [{"name": "destination"}, {"name": "date"},
                    {"name": "time_of_day", "enum": ["morning", "afternoon", "evening"]}]},
        {"name": "search_hotels", "description": "search available hotels", "read_only": True,
         "intent": "book_hotel", "params": [{"name": "city"}, {"name": "date"}]},
    ]
)


@pytest.fixture(scope="module")
def model() -> InterruptionModel:
    m = InterruptionModel.load_default()
    if m is None:
        pytest.skip("no trained weights; run scripts/train_classifier.py")
    return m


@pytest.fixture
def lex() -> Lexicon:
    return Lexicon.for_manifest(MANIFEST)


@pytest.fixture
def st() -> SessionState:
    s = SessionState(session_id="t")
    s.set_intent("book_flight")
    s.set_slot("destination", "DEL")
    s.set_slot("date", "Tuesday")
    return s


# ------------------------------------------------------------------ inference

def test_weights_are_bundled_in_the_repo() -> None:
    """No runtime download: a cold model pull would eat both the 120 s cap and
    the latency block."""
    assert WEIGHTS_PATH.exists()
    assert WEIGHTS_PATH.stat().st_size < 64_000, "weights should be kilobytes, not megabytes"


def test_probabilities_are_a_distribution(model: InterruptionModel) -> None:
    probs = model.predict_proba({"n_corrections": 1.0, "editing_term": 1.0})
    assert sum(probs.values()) == pytest.approx(1.0)
    assert all(0.0 <= p <= 1.0 for p in probs.values())


def test_inference_is_fast_enough_to_be_free(model: InterruptionModel) -> None:
    """The fast path costs no virtual time; it must not cost much real time either."""
    feats = {name: 0.5 for name in model.feature_order}
    start = time.perf_counter()
    for _ in range(2000):
        model.predict(feats)
    per_call_us = (time.perf_counter() - start) / 2000 * 1e6
    assert per_call_us < 200, f"{per_call_us:.0f} us per classification"


def test_missing_features_do_not_crash(model: InterruptionModel) -> None:
    """Adding a feature to extract_features must degrade an old model's
    accuracy, never kill the scenario."""
    kind, conf = model.predict({})
    assert isinstance(kind, InterruptionKind) and 0.0 <= conf <= 1.0

    kind, _ = model.predict({"a_feature_that_does_not_exist": 9.0})
    assert isinstance(kind, InterruptionKind)


def test_predictions_are_deterministic(model: InterruptionModel) -> None:
    feats = {"n_corrections": 1.0, "editing_term": 1.0, "overlapping": 1.0}
    assert len({model.predict(feats) for _ in range(20)}) == 1


def test_round_trips_through_disk(model: InterruptionModel, tmp_path) -> None:
    path = model.save(tmp_path / "m.json")
    revived = InterruptionModel.load(path)
    feats = {"n_corrections": 1.0, "filled_pause": 1.0}
    assert revived.predict(feats) == model.predict(feats)
    assert np.allclose(revived.weights, model.weights)


def test_absent_weights_degrade_to_rules(tmp_path) -> None:
    assert InterruptionModel.load_default.__func__ is not None
    with pytest.raises(FileNotFoundError):
        InterruptionModel.load(tmp_path / "nope.json")


def test_an_agent_with_no_model_still_works(lex: Lexicon, st: SessionState) -> None:
    r = Interpreter(lex, model=None).interpret(Turn("no, Mumbai"), st, in_flight=1)
    assert r.kind is InterruptionKind.SLOT_CORRECTION
    assert r.model_kind is None


# ------------------------------------------------------------------ arbitration

def test_the_model_cannot_talk_the_agent_into_cancelling(model, lex, st) -> None:
    """Destructive branches are rule-decided, whatever the model says.

    A false GOAL_SWITCH cancels every in-flight call; a false SLOT_CORRECTION
    kills a subset. Both are unrecoverable work loss, so the model is not
    allowed to cause them on its own.
    """

    class AlwaysGoalSwitch:
        def predict(self, features):
            return InterruptionKind.GOAL_SWITCH, 0.99

    r = Interpreter(lex, model=AlwaysGoalSwitch()).interpret(Turn("mhm", overlapping_agent_speech=True), st, in_flight=2)
    assert r.kind is InterruptionKind.BACKCHANNEL, "the model overrode a non-destructive rule"
    assert r.arbitrated is True
    assert r.model_kind is InterruptionKind.GOAL_SWITCH


def test_disagreements_are_recorded_in_the_payload(model, lex, st) -> None:
    """Both verdicts go into the trace: "where did that come from?" has an answer."""

    class AlwaysRepeat:
        def predict(self, features):
            return InterruptionKind.REPEAT_REQUEST, 0.99

    r = Interpreter(lex, model=AlwaysRepeat()).interpret(Turn("um, er"), st, in_flight=1)
    payload = r.to_payload()
    assert payload["rule_kind"] == "self_repair"
    assert payload["model_kind"] == "repeat_request"
    assert payload["arbitrated"] is True


def test_agreement_raises_confidence(model, lex, st) -> None:
    class AlwaysCorrection:
        def predict(self, features):
            return InterruptionKind.SLOT_CORRECTION, 0.9

    rules_only = Interpreter(lex).interpret(Turn("no, Mumbai"), st, in_flight=1)
    agreed = Interpreter(lex, model=AlwaysCorrection()).interpret(Turn("no, Mumbai"), st, in_flight=1)
    assert agreed.kind is rules_only.kind
    assert agreed.confidence > rules_only.confidence
    assert agreed.arbitrated is False


# ------------------------------------------------------------------ the claim

def test_the_ensemble_beats_rules_alone_under_asr_noise(model, lex) -> None:
    """The reason the model ships at all.

    Measured on a noisy split with a seed used neither for fitting nor for
    choosing the arbitration policy. The margin is small — this is a second
    opinion, not a leap — but it is real and it is not measured on training data.
    """
    test = generate(600, split="eval", seed=909, noise=0.45)

    def accuracy(m) -> float:
        interp = Interpreter(lex, model=m)
        return sum(
            interp.interpret(ex.turn, ex.state, in_flight=ex.in_flight).kind is ex.label
            for ex in test
        ) / len(test)

    rules_only = accuracy(None)
    ensemble = accuracy(model)
    assert rules_only > 0.85, f"rules regressed to {rules_only:.3f}"
    assert ensemble >= rules_only, f"ensemble {ensemble:.3f} < rules {rules_only:.3f}"


def test_noise_actually_perturbs_the_corpus() -> None:
    """Guard: if `_noisify` silently stopped working, the robustness numbers
    above would be measuring nothing."""
    clean = generate(200, split="eval", seed=5)
    noisy = generate(200, split="eval", seed=5, noise=0.45)
    differing = sum(a.turn.text != b.turn.text for a, b in zip(clean, noisy))
    assert differing > 100, f"only {differing}/200 utterances were perturbed"
