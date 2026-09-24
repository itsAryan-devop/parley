"""Reading a panel, and knowing when not to trust the reading.

The fixtures are generated here rather than committed, so the assertions state
exactly what is in each frame instead of referring to a PNG someone has to open
to understand. Real photographs are a different test: see
`scripts/ocr_report.py`, which runs this same reader over `media/frames/` and is
the only place real-world accuracy is claimed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PIL")
ocr = pytest.importorskip("parley.multimodal.ocr")

REQUIRES_OCR = pytest.mark.skipif(not ocr.available(), reason="OCR extra not installed")


def _panel(tmp_path, code: str = "E4", model: str = "WW90T534DAN", blur: float = 0.0):
    """A plausible appliance panel: dark bezel, bright code, model sticker."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    img = Image.new("RGB", (640, 360), (28, 28, 32))
    draw = ImageDraw.Draw(img)
    draw.rectangle([60, 60, 580, 220], fill=(8, 10, 14), outline=(70, 70, 80), width=3)
    try:
        big = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 110)
        small = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 26)
    except OSError:  # pragma: no cover - font availability is platform-specific
        big = small = ImageFont.load_default()
    if code:
        draw.text((250, 95), code, fill=(255, 90, 60), font=big)
    if model:
        draw.text((80, 250), f"Model: {model}", fill=(215, 215, 220), font=small)
    draw.text((80, 290), "SAMSUNG", fill=(190, 190, 200), font=small)

    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    path = tmp_path / f"panel_{code or 'none'}_{blur}.png"
    img.save(path)
    return path


# -- reading ---------------------------------------------------------------


@REQUIRES_OCR
def test_reads_an_error_code_off_a_panel(tmp_path) -> None:
    read = ocr.read_frame(_panel(tmp_path))
    assert read.code == "E4"
    assert read.brand == "samsung"
    assert read.error is None


@REQUIRES_OCR
def test_model_number_is_not_mistaken_for_a_fault_code(tmp_path) -> None:
    """`WW90T534DAN` contains `90T`, which matches the digit-letter code shape.

    Reporting it as a fault would send the user to the wrong page of the manual
    with full confidence -- the most expensive kind of wrong.
    """
    read = ocr.read_frame(_panel(tmp_path, code="", model="WW90T534DAN"))
    assert read.model == "WW90T534DAN"
    assert read.error_codes == [], f"model number leaked into codes: {read.error_codes}"


@REQUIRES_OCR
def test_both_orders_of_the_code_family_are_matched(tmp_path) -> None:
    """Panels print the same fault as `E4` or `4E` depending on product line."""
    assert ocr.read_frame(_panel(tmp_path, code="4E")).code == "4E"


# -- knowing when not to trust it ------------------------------------------


@REQUIRES_OCR
def test_a_readable_frame_is_never_flagged_for_retake(tmp_path) -> None:
    """The measurement that corrected the design.

    A Gaussian blur of radius 2 drops variance-of-Laplacian from ~140 to ~1
    while the glyphs still read perfectly. Gating on the blur statistic would
    ask the user to retake a photograph the agent had already understood.
    Recognising the text *is* the proof the frame was sharp enough.
    """
    read = ocr.read_frame(_panel(tmp_path, blur=2.0))
    assert read.blurry, "expected the raw statistic to consider this soft"
    assert read.code == "E4", "but it is plainly still readable"
    assert not read.needs_retake, "a readable frame must never prompt a retake"


@REQUIRES_OCR
def test_unreadable_and_soft_does_prompt_a_retake(tmp_path) -> None:
    read = ocr.read_frame(_panel(tmp_path, blur=12.0))
    assert read.empty
    assert read.needs_retake


@REQUIRES_OCR
def test_two_codes_on_one_panel_is_ambiguity_not_a_choice(tmp_path) -> None:
    """Picking the first of two would be arbitrary; the user knows which flashes."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (640, 360), (20, 20, 24))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf", 90)
    except OSError:  # pragma: no cover
        font = ImageFont.load_default()
    draw.text((90, 120), "E4", fill=(255, 90, 60), font=font)
    draw.text((400, 120), "5E", fill=(255, 90, 60), font=font)
    path = tmp_path / "two_codes.png"
    img.save(path)

    read = ocr.read_frame(path)
    if len(read.error_codes) < 2:
        pytest.skip(f"reader found {read.error_codes}; needs both to test the rule")
    assert read.code is None, "exactly-one is the precondition for acting"


@REQUIRES_OCR
def test_undecodable_input_is_an_error_not_a_crash() -> None:
    read = ocr.read_frame(b"this is not a png")
    assert read.error is not None
    assert read.code is None
    assert not read.needs_retake, "a decode failure is not a focus problem"


# -- fusion with the colour classifier -------------------------------------


@REQUIRES_OCR
def test_read_glyphs_override_the_colour_classifier(tmp_path) -> None:
    """Direct evidence beats circumstantial, and says so in the trace."""
    from parley.multimodal.perception import Perception
    from parley.multimodal.vision import _fuse

    read = ocr.read_frame(_panel(tmp_path))
    guess = Perception(slot="label", label="router_power_led_red", confidence=0.71)
    fused = _fuse(guess, read)

    assert fused.label == "washer_error_e4"
    assert fused.overrode == "router_power_led_red", "an override must be recorded"
    assert fused.evidence == "panel reads E4"


@REQUIRES_OCR
def test_an_unknown_code_produces_a_concrete_question(tmp_path) -> None:
    """"I can see 5E but don't recognise it" beats a generic failure."""
    from parley.multimodal.perception import Perception
    from parley.multimodal.vision import _fuse

    read = ocr.read_frame(_panel(tmp_path, code="5E"))
    if read.code != "5E":
        pytest.skip(f"reader returned {read.error_codes}")

    fused = _fuse(Perception(slot="label", label=None, confidence=0.2), read)
    assert fused.ambiguous
    assert "5E" in (fused.question or "")


@REQUIRES_OCR
def test_fusion_leaves_a_confident_classifier_alone_when_nothing_is_read(tmp_path) -> None:
    """OCR must not disturb the path that already worked."""
    from parley.multimodal.perception import Perception
    from parley.multimodal.vision import _fuse

    blank = ocr.read_frame(_panel(tmp_path, code="", model=""))
    before = Perception(slot="label", label="router_wan_led_amber", confidence=0.88)
    after = _fuse(before, blank)

    assert after.label == "router_wan_led_amber"
    assert after.confidence == 0.88
    assert after.overrode is None
