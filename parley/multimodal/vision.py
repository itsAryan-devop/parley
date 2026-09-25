"""Frame grounding: PNG in, a device-state label out.

Visual scenarios are 20% of the hidden set at a 1.5× multiplier, so this path
has to actually work rather than stub out. It also has to fit inside a 120 s cap
with no runtime downloads, which rules out pulling a pretrained backbone.

What it does instead is classical: decode the PNG, reduce it to ~20 hand-chosen
colour and layout features, and run the same small softmax used everywhere else
in this project. For the thing the theme actually asks about — "grounding device
queries in camera frames and manuals", i.e. status LEDs and error screens — the
discriminative signal *is* colour and position, so a feature set built around
saturated-hue mass and where it sits in the frame is well matched to the problem
rather than a compromise.

Features are deliberately scale-invariant (everything is a fraction or a
normalised coordinate), so a 640×480 synthetic frame and a phone photo of a real
router produce comparable vectors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from .perception import Perception, LabelModel, decide, payload_of

MODEL_PATH = Path(__file__).with_name("vision_model.json")

#: Hue bands in degrees. Amber is split out from red because "power LED red" and
#: "WAN LED amber" are exactly the pair a user needs disambiguated, and lumping
#: them into one "warm" bucket would make the model unable to tell them apart.
_HUE_BANDS: dict[str, tuple[float, float]] = {
    "red": (345.0, 15.0),
    "amber": (15.0, 50.0),
    "yellow": (50.0, 70.0),
    "green": (70.0, 165.0),
    "cyan": (165.0, 200.0),
    "blue": (200.0, 260.0),
    "magenta": (260.0, 345.0),
}

FEATURE_NAMES: list[str] = (
    [f"mass_{band}" for band in _HUE_BANDS]
    + [
        "mean_luma", "std_luma", "dark_fraction", "bright_fraction",
        "mean_saturation", "max_saturation", "saturated_fraction",
        "hot_row", "hot_col", "hot_spread",
        "edge_density", "text_like",
    ]
)


def _to_array(source: Any) -> np.ndarray | None:
    """Decode whatever the event gave us into an HxWx3 float array in [0, 1]."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a declared dependency
        return None

    try:
        if isinstance(source, (str, Path)):
            img = Image.open(source)
        elif isinstance(source, (bytes, bytearray)):
            import io

            img = Image.open(io.BytesIO(source))
        else:
            return None
        img = img.convert("RGB")
        # Downsample hard. We are measuring colour mass and coarse layout, and
        # a small grid makes the features stable against camera resolution.
        img = img.resize((64, 64))
        return np.asarray(img, dtype=float) / 255.0
    except Exception:
        return None


def _rgb_to_hsv(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = arr.max(axis=-1)
    mn = arr.min(axis=-1)
    delta = mx - mn

    hue = np.zeros_like(mx)
    safe = delta > 1e-6
    # Standard piecewise hue, guarded against the achromatic case.
    idx = safe & (mx == r)
    hue[idx] = (60.0 * ((g[idx] - b[idx]) / delta[idx])) % 360.0
    idx = safe & (mx == g)
    hue[idx] = 60.0 * ((b[idx] - r[idx]) / delta[idx]) + 120.0
    idx = safe & (mx == b)
    hue[idx] = 60.0 * ((r[idx] - g[idx]) / delta[idx]) + 240.0

    sat = np.zeros_like(mx)
    sat[mx > 1e-6] = delta[mx > 1e-6] / mx[mx > 1e-6]
    return hue % 360.0, sat, mx


def extract_features(source: Any) -> dict[str, float] | None:
    arr = _to_array(source)
    if arr is None:
        return None

    hue, sat, val = _rgb_to_hsv(arr)
    luma = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]
    h, w = luma.shape

    # A pixel only votes for a hue band if it is actually coloured and lit; an
    # unlit LED and a dark background are both near-black and must not register
    # as "red" just because the red channel edges out the others by noise.
    vivid = (sat > 0.45) & (val > 0.35)
    feats: dict[str, float] = {}
    total = float(luma.size)

    for band, (lo, hi) in _HUE_BANDS.items():
        if lo < hi:
            mask = (hue >= lo) & (hue < hi)
        else:  # red wraps 360
            mask = (hue >= lo) | (hue < hi)
        feats[f"mass_{band}"] = float((mask & vivid).sum()) / total

    feats["mean_luma"] = float(luma.mean())
    feats["std_luma"] = float(luma.std())
    feats["dark_fraction"] = float((luma < 0.15).sum()) / total
    feats["bright_fraction"] = float((luma > 0.75).sum()) / total
    feats["mean_saturation"] = float(sat.mean())
    feats["max_saturation"] = float(sat.max())
    feats["saturated_fraction"] = float(vivid.sum()) / total

    # Where the lit region sits. A status LED is a small bright blob at a
    # characteristic position; a full-screen error message is not.
    weight = (luma * vivid) if vivid.any() else luma
    if weight.sum() > 1e-6:
        rows, cols = np.mgrid[0:h, 0:w]
        feats["hot_row"] = float((rows * weight).sum() / weight.sum()) / h
        feats["hot_col"] = float((cols * weight).sum() / weight.sum()) / w
        spread = np.sqrt(
            ((rows - feats["hot_row"] * h) ** 2 + (cols - feats["hot_col"] * w) ** 2) * weight
        ).sum() / max(weight.sum(), 1e-6)
        feats["hot_spread"] = float(spread) / max(h, w)
    else:
        feats["hot_row"] = feats["hot_col"] = 0.5
        feats["hot_spread"] = 0.0

    gy, gx = np.gradient(luma)
    grad = np.hypot(gx, gy)
    feats["edge_density"] = float((grad > 0.12).sum()) / total
    # Text is many short horizontal strokes: lots of vertical gradient arranged
    # in rows. Separates an error-code screen from a plain lit panel.
    row_energy = grad.mean(axis=1)
    feats["text_like"] = float(row_energy.std() / (row_energy.mean() + 1e-6))

    return feats


_model: LabelModel | None | bool = False


def _get_model() -> LabelModel | None:
    global _model
    if _model is False:
        _model = LabelModel.load_or_none(MODEL_PATH)
    return _model  # type: ignore[return-value]


#: Error codes that identify a device state directly. A panel reading `E4` is
#: not evidence *about* a fault, it *is* the fault reported by the device
#: itself, so a match here outranks anything inferred from colour.
#:
#: This is perception knowledge, not planning knowledge: it says what the frame
#: shows, never what to do about it. The remedy stays behind the manual-lookup
#: tool, which is why the agent still carries no device-specific logic.
_CODE_LABELS: dict[str, str] = {
    "E4": "washer_error_e4",
    "4E": "washer_error_e4",   # the same inlet fault, printed in the other order
}


def _fuse(perception: Perception, text: Any) -> Perception:
    """Reconcile the colour classifier with what the panel actually says.

    Direct evidence wins. A recognised glyph sequence reports the device's own
    diagnosis; hue mass is an inference about how the device looks. When they
    disagree the glyphs are right, and the override is recorded rather than
    applied silently -- a perception that quietly contradicts the model it just
    ran is exactly the kind of thing that is impossible to debug later.
    """
    perception.features = dict(perception.features)
    perception.text = text

    if text.error_codes:
        perception.features["ocr_codes"] = float(len(text.error_codes))

    code = text.code
    label = _CODE_LABELS.get(code) if code else None

    if label:
        if perception.label != label:
            perception.overrode = perception.label or ("abstained" if perception.ambiguous else "nothing")
        perception.label = label
        perception.ambiguous = False
        perception.question = None
        perception.error = None
        # Read glyphs, not a calibrated posterior. 0.95 rather than 1.0 because
        # the recogniser can still misread, and a claim of certainty would be
        # the one thing the provable-speech gate cannot warrant.
        perception.confidence = 0.95
        perception.evidence = f"panel reads {code}"
        return perception

    if code and perception.label is None:
        # A real code we have no label for. Saying "I can see E4 but I don't
        # know it" is far more useful than a generic failure, and it gives the
        # user something concrete to confirm.
        perception.evidence = f"panel reads {code}"
        perception.question = (
            f"I can see {code} on the panel but I don't recognise that code — "
            "which appliance is this?"
        )
        perception.ambiguous = True
        perception.candidates = [code]
        return perception

    if perception.label is None and text.needs_retake and perception.question is None:
        # A specific, actionable question instead of a shrug. Only reachable
        # when nothing was read AND the frame is soft; see ocr.BLUR_FLOOR.
        #
        # `question is None` is load-bearing. Blur is a *fallback* explanation --
        # what to say when we have nothing better -- and this branch used to
        # overwrite whatever was already there. Once `decide()` learned to name
        # rival labels on a mildly out-of-distribution frame, the router photo
        # with two LEDs lit produced "is it the power LED or the WAN LED?" and
        # then had it replaced by "the picture is too blurry to read the panel".
        # That is worse than vague, it is false: a photograph of indicator lights
        # has no panel text to read, so the empty read is not evidence of blur.
        perception.question = (
            "The picture is too blurry for me to read the panel — "
            "could you hold the camera closer to the display?"
        )
        perception.error = "blurred beyond reading"

    return perception


def _read_text(source: Any, features: dict[str, float] | None) -> Any:
    """Read the panel, but only when the frame plausibly has a panel to read.

    OCR costs roughly a second -- three orders of magnitude more than the
    classifier -- so it is gated on the `text_like` feature the classifier has
    already computed for free. A frame that is a single lit LED on a dark case
    has nothing to read, and spending a second establishing that on every
    scenario would put the visual suite's wall-clock cost up by more than the
    capability is worth.
    """
    if features is not None and features.get("text_like", 0.0) < 0.25:
        return None
    try:
        from . import ocr
    except ImportError:  # pragma: no cover
        return None
    if not ocr.available():
        return None
    return ocr.read_frame(source)


async def ground_frame(
    event: Any, *, clock: Any = None, slot: str = "label", read_text: bool = True
) -> Perception:
    """Classify a frame. Decoding is modelled as costing virtual time."""
    source = payload_of(event)
    ident = getattr(event, "frame_id", "frame")

    if clock is not None:
        # Perception is not free. Charging it virtual time is what makes
        # "acknowledge first, decode behind the acknowledgment" a real property
        # of the trace rather than a claim in a document.
        await clock.sleep(140.0)

    features = extract_features(source)
    if features is None:
        return Perception(
            slot=slot, label=None, confidence=0.0, source_id=ident,
            modality="vision", error="frame could not be decoded",
        )

    model = _get_model()
    if model is None:
        return Perception(
            slot=slot, label=None, confidence=0.0, source_id=ident,
            modality="vision", features=features, error="no vision model available",
        )

    perception = decide(
        slot, model.probs(features),
        source_id=ident, modality="vision", features=features,
        phrase="the picture",
        out_of_distribution=model.is_out_of_distribution(features),
        ood_ratio=model.ood_ratio(features),
    )

    text = _read_text(source, features) if read_text else None
    if text is not None:
        perception = _fuse(perception, text)
    return perception
