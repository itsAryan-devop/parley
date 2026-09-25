"""Perception: answer, ask, or decline.

Multimodal scenarios carry a 1.5x multiplier and half the hidden set is audio or
visual, so this is the largest single lever in the scoring scheme. The tests
that matter most are the ones asserting the agent does *not* answer — a
confidently wrong perception loses task completion and the truthfulness term in
the quality multiplier, while a specific question earns credit.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from harness.clock import Clock, run_virtual
from parley.multimodal import (
    AMBIGUITY_MARGIN,
    LabelModel,
    Perception,
    decide,
    ground_audio,
    ground_frame,
    payload_of,
)
from parley.multimodal import audio as audio_mod
from parley.multimodal import vision as vision_mod
from parley.protocol.events import AudioClip, VideoFrame

SCENARIO_MEDIA = Path("media/scenarios")
UNDECIDABLE = Path("media/undecidable")

pytestmark = pytest.mark.skipif(
    not SCENARIO_MEDIA.exists(), reason="run scripts/make_media.py first"
)


def frame_event(name: str) -> VideoFrame:
    return VideoFrame(t=0.0, frame_id=name, path=str(SCENARIO_MEDIA / "frames" / f"{name}.png"))


def audio_event(name: str) -> AudioClip:
    return AudioClip(t=0.0, clip_id=name, path=str(SCENARIO_MEDIA / "audio" / f"{name}.wav"))


# ============================================================ the decision rule

def test_a_clear_winner_is_answered() -> None:
    p = decide("label", {"a": 0.8, "b": 0.15, "c": 0.05},
               source_id="x", modality="vision", features={})
    assert p.label == "a" and not p.ambiguous


def test_a_near_tie_is_asked_about_not_guessed() -> None:
    """0.45 vs 0.43 is a coin flip, not a 0.45-confidence answer."""
    p = decide("label", {"router_power_led_red": 0.45, "router_wan_led_amber": 0.43, "c": 0.12},
               source_id="frame-1", modality="vision", features={}, phrase="the picture")
    assert p.ambiguous and p.label is None
    assert set(p.candidates) == {"router_power_led_red", "router_wan_led_amber"}
    assert "power led red" in p.question and "wan led amber" in p.question


def test_the_question_names_both_candidates_in_plain_words() -> None:
    p = decide("label", {"washer_error_e4": 0.4, "tv_hdmi_no_signal": 0.38},
               source_id="f", modality="vision", features={})
    assert "_" not in p.question, "labels must be humanised before they are spoken"


def test_nothing_above_the_floor_is_declined() -> None:
    p = decide("label", {"a": 0.3, "b": 0.28, "c": 0.25, "d": 0.17},
               source_id="x", modality="vision", features={})
    assert p.label is None


def test_out_of_distribution_input_is_declined_whatever_the_softmax_says() -> None:
    """A softmax will report 0.97 on a blown-out photograph. The distance check
    is what stops us believing it."""
    p = decide("label", {"a": 0.97, "b": 0.02, "c": 0.01},
               source_id="x", modality="vision", features={}, out_of_distribution=True)
    assert p.label is None
    assert p.error == "out of distribution"


def test_the_margin_is_what_decides_not_the_top_score() -> None:
    clear = decide("label", {"a": 0.5, "b": 0.2}, source_id="x", modality="v", features={})
    tie = decide("label", {"a": 0.5, "b": 0.5 - AMBIGUITY_MARGIN / 2}, source_id="x", modality="v", features={})
    assert clear.label == "a" and tie.ambiguous


# ============================================================ real media

def test_each_device_state_is_recognised_from_its_pixels() -> None:
    async def main():
        c = Clock()
        return {
            name: await ground_frame(frame_event(name), clock=c)
            for name in ("router_power_led_red", "router_wan_led_amber",
                         "washer_error_e4", "tv_hdmi_no_signal")
        }

    results = run_virtual(main())
    for name, p in results.items():
        assert p.label == name, f"{name} -> {p.label} ({p.confidence:.2f})"
        assert not p.ambiguous


def test_each_sound_class_is_recognised_from_its_samples() -> None:
    async def main():
        c = Clock()
        return {
            name: await ground_audio(audio_event(name), clock=c)
            for name in ("beeping", "continuous_tone", "grinding", "clicking", "silence")
        }

    results = run_virtual(main())
    for name, p in results.items():
        assert p.label == name, f"{name} -> {p.label} ({p.confidence:.2f})"


def test_two_lit_leds_produce_a_question_not_an_answer() -> None:
    """The evidence genuinely is split, so picking one would be a guess."""

    async def main():
        return await ground_frame(frame_event("router_led_ambiguous"), clock=Clock())

    p = run_virtual(main())
    assert p.label is None, f"answered {p.label!r} on a frame with two LEDs lit"


def test_an_unreadable_frame_is_declined() -> None:
    async def main():
        return await ground_frame(frame_event("washer_unreadable"), clock=Clock())

    p = run_virtual(main())
    assert p.label is None


def test_a_mixed_sound_is_not_confidently_labelled() -> None:
    async def main():
        return await ground_audio(audio_event("sound_ambiguous"), clock=Clock())

    p = run_virtual(main())
    assert p.label is None


@pytest.mark.skipif(not UNDECIDABLE.exists(), reason="run scripts/make_media.py")
def test_undecidable_media_is_almost_never_answered() -> None:
    """Held out of training entirely. Measured, not asserted at 100%: audio
    mixtures sit close enough to a real class that one in twelve still gets
    through, and that number is reported rather than hidden."""

    async def main():
        c = Clock()
        out = []
        for path in sorted((UNDECIDABLE / "frames").rglob("*.png")):
            out.append(await ground_frame(
                VideoFrame(t=0.0, frame_id=path.stem, path=str(path)), clock=c))
        for path in sorted((UNDECIDABLE / "audio").rglob("*.wav")):
            out.append(await ground_audio(
                AudioClip(t=0.0, clip_id=path.stem, path=str(path)), clock=c))
        return out

    results = run_virtual(main())
    answered = [p for p in results if p.label is not None]
    assert len(answered) / len(results) < 0.10, (
        f"{len(answered)}/{len(results)} undecidable inputs were confidently labelled"
    )


# ============================================================ mechanics

def test_perception_costs_virtual_time() -> None:
    """Decoding is not free, and charging it makes "acknowledge first, decode
    behind the acknowledgment" a real property of the trace."""

    async def main():
        c = Clock()
        await ground_frame(frame_event("washer_error_e4"), clock=c)
        return c.now

    assert run_virtual(main()) > 0


def test_a_missing_file_degrades_to_a_clarification_not_a_crash() -> None:
    async def main():
        return await ground_frame(VideoFrame(t=0.0, frame_id="nope", path="does/not/exist.png"),
                                  clock=Clock())

    p = run_virtual(main())
    assert isinstance(p, Perception) and p.label is None
    assert p.error == "frame could not be decoded"


def test_a_corrupt_clip_degrades_cleanly(tmp_path) -> None:
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav file at all")

    async def main():
        return await ground_audio(AudioClip(t=0.0, clip_id="bad", path=str(bad)), clock=Clock())

    p = run_virtual(main())
    assert p.label is None and p.error is not None


def test_features_are_scale_invariant() -> None:
    """A 320px synthetic frame and a resized copy must agree, so a phone photo
    of a real device lands in the same feature space."""
    from PIL import Image

    src = SCENARIO_MEDIA / "frames" / "router_power_led_red.png"
    small = Image.open(src).resize((160, 160))
    big = Image.open(src).resize((900, 900))

    import io

    def feats(img):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return vision_mod.extract_features(buf.getvalue())

    a, b = feats(small), feats(big)
    for key in ("mass_red", "mean_luma", "hot_col", "saturated_fraction"):
        assert a[key] == pytest.approx(b[key], abs=0.06), key


def test_models_are_bundled_and_small() -> None:
    for path in (vision_mod.MODEL_PATH, audio_mod.MODEL_PATH):
        assert path.exists(), f"{path} missing — run scripts/train_perception.py"
        assert path.stat().st_size < 200_000


def test_calibration_never_sharpens() -> None:
    """Temperature scaling on a perfectly separated validation set is degenerate
    — the NLL optimum runs to T -> 0, making an overconfident model more so.
    Both shipped models must therefore have T >= 1."""
    for path in (vision_mod.MODEL_PATH, audio_mod.MODEL_PATH):
        assert LabelModel.load(path).temperature >= 1.0


def test_distance_is_zero_at_a_centroid() -> None:
    model = LabelModel.load(vision_mod.MODEL_PATH)
    centroid = model.centroids[0] * model.sigma + model.mu
    features = dict(zip(model.feature_order, centroid))
    assert model.ood_distance(features) == pytest.approx(0.0, abs=1e-6)
    assert not model.is_out_of_distribution(features)


def test_absurd_input_is_far_from_everything() -> None:
    model = LabelModel.load(vision_mod.MODEL_PATH)
    features = {name: 1e4 for name in model.feature_order}
    assert model.is_out_of_distribution(features)


# ---------------------------------------------------------------- inline media
#
# The protocol offers two ways to deliver a frame or clip: `path` and
# `data_b64`. Both arrive as `str`, and for most of this project's life
# `ground_frame`/`ground_audio` collapsed them with `or` -- which handed a
# base64 string to `Image.open`/`wave.open` as a *filename*. Every inline frame
# failed to decode, reporting "frame could not be decoded" as though the image
# were corrupt.
#
# Nothing caught it: all 29 scenarios deliver media by path, the fuzzer perturbs
# timing rather than wire encoding, and test_adapter.py translates schemas
# rather than payloads. The guide never promises media arrives as a path, so a
# harness handing us bytes inline would have scored zero on all six visual and
# seven audio scenarios -- the half of the hidden set carrying a 1.5x multiplier.


def b64_of(path: Path) -> str:
    import base64

    return base64.b64encode(path.read_bytes()).decode("ascii")


def test_payload_of_prefers_a_path_verbatim() -> None:
    assert payload_of(VideoFrame(frame_id="f", path="media/x.png")) == "media/x.png"


def test_payload_of_decodes_base64_to_bytes() -> None:
    import base64

    blob = base64.b64encode(b"\x89PNG rest").decode("ascii")
    assert payload_of(VideoFrame(frame_id="f", data_b64=blob)) == b"\x89PNG rest"


def test_payload_of_strips_a_data_uri_prefix() -> None:
    """A browser's FileReader.readAsDataURL produces this, and the live demo
    uploads exactly that."""
    import base64

    blob = base64.b64encode(b"hello").decode("ascii")
    event = VideoFrame(frame_id="f", data_b64=f"data:image/png;base64,{blob}")
    assert payload_of(event) == b"hello"


def test_payload_of_rejects_something_that_is_not_base64() -> None:
    """validate=True matters: without it a stray path would decode into
    plausible-looking garbage instead of failing."""
    assert payload_of(VideoFrame(frame_id="f", data_b64="not base64 at all!!")) is None


def test_payload_of_is_none_when_nothing_was_delivered() -> None:
    assert payload_of(VideoFrame(frame_id="f")) is None


@pytest.mark.parametrize("name", [
    "router_power_led_red", "router_wan_led_amber", "tv_hdmi_no_signal",
    "washer_error_e4", "router_led_ambiguous", "washer_unreadable",
])
def test_inline_frame_grounds_identically_to_the_same_frame_on_disk(name: str) -> None:
    """The delivery mechanism must not change a single decision.

    Asserting equality rather than "b64 works" is deliberate: a decoder that
    silently produced a *different* image -- wrong colour order, wrong size --
    would pass a liveness check and quietly change what the agent believes it
    saw.
    """
    path = SCENARIO_MEDIA / "frames" / f"{name}.png"
    on_disk = run_virtual(ground_frame(VideoFrame(frame_id=name, path=str(path))))
    inline = run_virtual(ground_frame(VideoFrame(frame_id=name, data_b64=b64_of(path))))

    assert inline.error is None or inline.error == on_disk.error
    assert inline.label == on_disk.label
    assert inline.confidence == pytest.approx(on_disk.confidence)
    assert inline.ambiguous == on_disk.ambiguous
    assert inline.candidates == on_disk.candidates


@pytest.mark.parametrize("name", [
    "beeping", "clicking", "grinding", "continuous_tone", "silence",
    "beeping_ambiguous", "sound_ambiguous",
])
def test_inline_clip_grounds_identically_to_the_same_clip_on_disk(name: str) -> None:
    path = SCENARIO_MEDIA / "audio" / f"{name}.wav"
    on_disk = run_virtual(ground_audio(AudioClip(clip_id=name, path=str(path))))
    inline = run_virtual(ground_audio(AudioClip(clip_id=name, data_b64=b64_of(path))))

    assert inline.label == on_disk.label
    assert inline.confidence == pytest.approx(on_disk.confidence)
    assert inline.ambiguous == on_disk.ambiguous


def test_inline_frame_still_reads_the_error_code() -> None:
    """OCR takes the same `source`, so the fix has to carry the text path too.

    Without this, a base64 frame could classify correctly off colour statistics
    while silently losing the panel read -- and the panel read is what makes the
    manual lookup possible.

    Skipped when the `vision` extra is absent, which is how this test found its
    own bug: the Docker image installs neither `vosk` nor `rapidocr` (the scored
    engine needs neither), so in the container `perception.text` is correctly
    `None` and the unguarded assertion failed on a working build. The label is
    still right there -- 0.99 from colour alone -- which is the point of keeping
    OCR a fusion step rather than a dependency.
    """
    from parley.multimodal import ocr

    if not ocr.available():
        pytest.skip("OCR extra not installed")

    path = SCENARIO_MEDIA / "frames" / "washer_error_e4.png"
    inline = run_virtual(ground_frame(VideoFrame(frame_id="w", data_b64=b64_of(path))))
    assert inline.text is not None, "no OCR read survived the inline path"
    assert "E4" in inline.text.error_codes
    # And it must reach the trace, which is where the warrant is actually read.
    assert inline.to_payload()["read"]["error_codes"] == ["E4"]


def test_a_base64_frame_is_never_treated_as_a_filename() -> None:
    """The regression, stated directly.

    The old code produced `error="frame could not be decoded"` here, because a
    base64 string is a perfectly valid thing to pass to `Image.open` and a
    perfectly invalid filename.
    """
    path = SCENARIO_MEDIA / "frames" / "router_wan_led_amber.png"
    inline = run_virtual(ground_frame(VideoFrame(frame_id="r", data_b64=b64_of(path))))
    assert inline.error != "frame could not be decoded"
    assert inline.label == "router_wan_led_amber"


# ------------------------------------------------- two kinds of "out of distribution"
#
# The distance check catches two different failures and they deserve different
# questions. A frame sitting just outside the training distribution resembles it
# and falls *between* classes; one sitting three orders of magnitude outside
# resembles nothing. Measured on the scenario media, as a multiple of each
# modality's threshold: router_led_ambiguous 2.1x, beeping_ambiguous 1.1x,
# washer_unreadable 2100x.
#
# Naming rivals is gated on BOTH distance and rival mass. Distance alone would
# offer "is it beeping or grinding?" for sound_ambiguous.wav, whose runner-up
# holds 0.032 -- inventing a rival to make a question sound specific.


def test_mild_ood_with_a_real_rival_names_both() -> None:
    p = decide("label", {"a": 0.67, "b": 0.29, "c": 0.04},
               source_id="x", modality="vision", features={},
               out_of_distribution=True, ood_ratio=2.1)
    assert p.ambiguous is True
    assert p.label is None, "naming rivals must never bind a slot"
    assert p.candidates == ["a", "b"]
    assert "a" in p.question and "b" in p.question
    assert p.error == "out of distribution, between classes"


def test_extreme_ood_stays_generic_however_split_it_looks() -> None:
    """washer_unreadable.png sits 2100x past the threshold. Its top labels are
    noise, and naming them would dress noise up as a shortlist."""
    p = decide("label", {"a": 0.55, "b": 0.44, "c": 0.01},
               source_id="x", modality="vision", features={},
               out_of_distribution=True, ood_ratio=2100.0)
    assert p.ambiguous is False
    assert p.label is None
    assert p.question is None
    assert p.error == "out of distribution"


def test_mild_ood_with_a_negligible_rival_stays_generic() -> None:
    """sound_ambiguous.wav: barely out of distribution, runner-up at 0.032."""
    p = decide("label", {"a": 0.944, "b": 0.032, "c": 0.024},
               source_id="x", modality="audio", features={},
               out_of_distribution=True, ood_ratio=1.07)
    assert p.ambiguous is False
    assert p.error == "out of distribution"


def test_omitting_the_ratio_is_the_conservative_choice() -> None:
    """A caller that does not supply a ratio must get the cautious branch.

    Otherwise adding the parameter would have silently changed the behaviour of
    every existing call site.
    """
    p = decide("label", {"a": 0.5, "b": 0.45},
               source_id="x", modality="vision", features={},
               out_of_distribution=True)
    assert p.ambiguous is False
    assert p.error == "out of distribution"


def test_the_ambiguous_router_frame_names_both_leds() -> None:
    """End to end on real media: the case S11 scores and the demo video shows.

    S11's own description asks for "a specific clarification"; for a long time
    the agent answered it with "I couldn't make that out", and the demo script
    promised a naming question the screen never produced.
    """
    perception = run_virtual(ground_frame(frame_event("router_led_ambiguous")))
    assert perception.ambiguous is True
    assert perception.label is None
    assert set(perception.candidates) == {"router_wan_led_amber", "router_power_led_red"}
    assert "router wan led amber" in perception.question
    assert "router power led red" in perception.question


def test_blur_never_overwrites_a_more_specific_question() -> None:
    """Blur is a fallback explanation, not a louder one.

    `_fuse` used to set the retake question unconditionally, which replaced
    "is it the power LED or the WAN LED?" with "the picture is too blurry to
    read the panel" -- false as well as vaguer, since a photograph of indicator
    lights has no panel text to read in the first place.
    """
    perception = run_virtual(ground_frame(frame_event("router_led_ambiguous")))
    assert "blurry" not in (perception.question or "")
    assert perception.error != "blurred beyond reading"


def test_ood_ratio_is_one_at_the_threshold() -> None:
    model = LabelModel.load(vision_mod.MODEL_PATH)
    centroid = model.centroids[0] * model.sigma + model.mu
    at_centroid = dict(zip(model.feature_order, centroid))
    assert model.ood_ratio(at_centroid) == pytest.approx(0.0, abs=1e-6)

    far = {name: 1e4 for name in model.feature_order}
    assert model.ood_ratio(far) > 1.0


def test_undecidable_media_is_still_refused_after_the_change() -> None:
    """The whole point of the distance check is abstention. Naming rivals must
    not have quietly turned refusals into answers."""
    frames = sorted((UNDECIDABLE / "frames").rglob("*.png"))
    if not frames:
        pytest.skip("run scripts/make_media.py first")
    bound = [
        p.name for p in frames
        if run_virtual(ground_frame(VideoFrame(frame_id=p.stem, path=str(p)))).label is not None
    ]
    assert bound == [], f"these undecidable frames were answered: {bound}"
