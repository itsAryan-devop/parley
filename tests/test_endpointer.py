"""The learned endpointer: features, model, and its behaviour in the agent.

Two things are worth locking down. First, the properties the *feature vector*
must have — a trailing preposition reads as unfinished, a long silence after a
complete request reads as done — because those are what the model's usefulness
rests on. Second, that the endpointer never breaks the marker-trusting contract:
a positive `end_of_turn` is always honoured, and with no weights loaded the
agent behaves exactly as it did before endpointing existed.
"""

from __future__ import annotations

import pytest

from parley.agent.endpointer import (
    FEATURE_ORDER,
    EndpointModel,
    SilenceTimeoutBaseline,
    endpoint_features,
)

MODEL = EndpointModel.load_default()


# ------------------------------------------------------------------ features

def test_trailing_preposition_reads_as_unfinished() -> None:
    f = endpoint_features("a flight to", silence_ms=200)
    assert f["trailing_dangling"] == 1.0
    assert f["trailing_content"] == 0.0


def test_trailing_conjunction_is_its_own_signal() -> None:
    f = endpoint_features("a flight to delhi and", silence_ms=900)
    assert f["trailing_conjunction"] == 1.0
    assert f["trailing_dangling"] == 1.0  # 'and' is both


def test_trailing_content_word_reads_as_a_value() -> None:
    f = endpoint_features("a flight to delhi", silence_ms=200)
    assert f["trailing_content"] == 1.0
    assert f["trailing_dangling"] == 0.0


def test_trailing_filled_pause_is_flagged() -> None:
    f = endpoint_features("the uh", silence_ms=300)
    assert f["trailing_filled_pause"] == 1.0


def test_silence_thresholds_and_clamping() -> None:
    assert endpoint_features("x", silence_ms=0)["silence_over_700"] == 0.0
    assert endpoint_features("x", silence_ms=800)["silence_over_700"] == 1.0
    assert endpoint_features("x", silence_ms=500)["silence_over_400"] == 1.0
    # Clamped into [0, 1].
    assert endpoint_features("x", silence_ms=9999)["silence_ms"] == 1.0
    assert endpoint_features("x", silence_ms=-5)["silence_ms"] == 0.0


def test_feature_dict_covers_the_declared_order() -> None:
    f = endpoint_features("a flight to delhi on tuesday", silence_ms=800,
                          complete_request=True, has_intent=True)
    assert set(FEATURE_ORDER) <= set(f)


# --------------------------------------------------------------------- model

@pytest.mark.skipif(MODEL is None, reason="endpoint weights not trained")
def test_model_holds_on_a_dangling_word_and_fires_on_a_complete_request() -> None:
    """The behaviour the whole thing exists for: a complete request with a long
    trailing pause should score higher than the same words still mid-phrase."""
    done_feats = endpoint_features("flight to hyderabad on thursday", silence_ms=1200,
                                   elapsed_ms=2500, complete_request=True,
                                   has_intent=True, just_bound_value=True)
    hold_feats = endpoint_features("flight to hyderabad and", silence_ms=1200,
                                   elapsed_ms=2500, complete_request=False,
                                   has_intent=True, just_bound_value=False)
    p_done = MODEL.probability(done_feats)
    p_hold = MODEL.probability(hold_feats)
    assert p_done > p_hold
    assert p_done >= MODEL.threshold        # confident enough to act
    assert p_hold < MODEL.threshold         # a dangling 'and' must not fire


@pytest.mark.skipif(MODEL is None, reason="endpoint weights not trained")
def test_model_probability_is_a_probability() -> None:
    for txt in ("", "flight", "flight to delhi on tuesday"):
        p = MODEL.probability(endpoint_features(txt, silence_ms=500))
        assert 0.0 <= p <= 1.0


def test_model_is_deterministic() -> None:
    if MODEL is None:
        pytest.skip("endpoint weights not trained")
    f = endpoint_features("flight to delhi on tuesday", silence_ms=900,
                          complete_request=True, has_intent=True)
    assert MODEL.probability(f) == MODEL.probability(f)


def test_missing_features_default_to_zero_not_crash() -> None:
    if MODEL is None:
        pytest.skip("endpoint weights not trained")
    # An old model must survive a feature being added to extract_features.
    assert 0.0 <= MODEL.probability({"word_count": 0.5}) <= 1.0


def test_roundtrip_save_load(tmp_path) -> None:
    if MODEL is None:
        pytest.skip("endpoint weights not trained")
    p = tmp_path / "ep.json"
    MODEL.save(p)
    back = EndpointModel.load(p)
    f = endpoint_features("flight to delhi on tuesday", silence_ms=900,
                          complete_request=True, has_intent=True)
    assert back.probability(f) == MODEL.probability(f)
    assert back.threshold == MODEL.threshold


# ------------------------------------------------------------------ baseline

def test_silence_timeout_baseline_fires_only_past_threshold() -> None:
    base = SilenceTimeoutBaseline(700.0)
    assert base.is_endpoint(endpoint_features("x", silence_ms=800))[0] is True
    assert base.is_endpoint(endpoint_features("x", silence_ms=600))[0] is False
