"""Reading a frame, as opposed to classifying one.

`vision.py` answers "what colour is lit, and where" -- which is the right
question for a status LED and the wrong question for a display panel. An
appliance showing `E4` is not communicating through hue; it is communicating
through *text*, and a classifier over colour mass can only ever infer that a
screen is lit, never what it says.

So this is a second, independent witness. The two disagree in useful ways:

    classifier says       OCR says        outcome
    washer_error_e4       "E4"            agree -> bind, high confidence
    washer_error_e4       "5E"            disagree -> OCR wins; it read the glyphs
    (uncertain)           "E4"            OCR rescues a frame colour could not place
    washer_error_e4       (nothing)       classifier alone, confidence unchanged
    (uncertain)           (nothing)       ask, and say which difficulty it hit

The rule when they conflict is not "trust the newer component". It is that a
recognised glyph sequence is direct evidence about the panel's *content*, while
hue mass is circumstantial evidence about its *appearance*. Direct evidence
wins, and the trace records that it overrode something so the decision is
auditable rather than magic.

Models are the RapidOCR ONNX trio (~16 MB) bundled inside the wheel. Nothing is
fetched at runtime, and `onnxruntime` is already present. Like ASR this is an
optional extra: the scored engine runs without it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

BLUR_FLOOR = 18.0
"""Variance of the Laplacian below which a frame is *probably* out of focus.

Blur is worth detecting separately from low confidence because it has a useful
remedy: "hold the camera closer to the panel" is actionable where "I'm not sure"
is not, and objective 5 pays for the specific question.

**It is a reason, never a gate.** Measured on the reference panel, a Gaussian
blur of radius 2 dropped this statistic from 141.9 to 1.0 while OCR still read
`E4`, the model number and the brand perfectly. Had blur vetoed the read, the
agent would have asked the user to retake a photograph it had already
understood -- trading a correct answer for a pointless question, which is the
exact failure the abstention machinery exists to avoid.

So a successful read stands on its own: recognising the glyphs *is* the proof
that the frame was sharp enough. This number is consulted only when the read
comes back empty, to explain why. See `FrameText.needs_retake`.

The absolute value is also scale- and content-dependent (synthetic panels have
far crisper edges than photographs), so it is not a calibrated constant and is
not treated as one."""

MIN_TEXT_CONFIDENCE = 0.55
"""Below this the recogniser is guessing at glyphs. A misread error code is
worse than no error code: it sends a confident answer about the wrong fault."""

# Samsung appliance codes are letter-digit or digit-letter pairs (4C, 5E, OE,
# dE, UE) as well as the E-prefixed family (E4, ERR 3). Both orders are matched
# because the panel's own convention varies by product line, and a pattern that
# only caught one would silently miss half the devices in the room.
_CODE_PATTERNS = [
    re.compile(r"\b(E[\s:-]?\d{1,3})\b", re.I),
    re.compile(r"\b(ERR(?:OR)?[\s:-]?\d{1,3})\b", re.I),
    re.compile(r"\b(\d[A-Z])\b"),
    re.compile(r"\b([A-Z]{1,2}E)\b"),
]

# A model number is a longer alphanumeric run. Kept distinct from error codes so
# that "WW90T534DAN" is never reported to the user as a fault.
_MODEL_PATTERN = re.compile(r"\b([A-Z]{2,4}\d{2,4}[A-Z0-9]{2,8})\b")

_BRANDS = ("samsung", "lg", "whirlpool", "bosch", "ifb", "godrej", "voltas")


@dataclass
class FrameText:
    """Everything the reader could establish about a frame's text."""

    lines: list[str] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    model_numbers: list[str] = field(default_factory=list)
    brand: str | None = None
    sharpness: float = 0.0
    blurry: bool = False
    error: str | None = None

    @property
    def code(self) -> str | None:
        """The single error code, or None when there is not exactly one.

        Two codes on one panel is ambiguity, not a list to pick from: acting on
        the first would be arbitrary, and the user knows which one is flashing.
        """
        return self.error_codes[0] if len(self.error_codes) == 1 else None

    @property
    def model(self) -> str | None:
        return self.model_numbers[0] if len(self.model_numbers) == 1 else None

    @property
    def empty(self) -> bool:
        return not self.lines

    @property
    def needs_retake(self) -> bool:
        """Nothing was read *and* the frame is soft -- so focus is the story.

        The conjunction is the whole point. Soft-but-readable needs no question;
        sharp-but-empty is a different problem (there is no text in shot) and
        deserves a different question. Only the overlap justifies asking the
        user to take the photograph again.
        """
        return self.empty and self.blurry and self.error is None

    def to_payload(self) -> dict[str, Any]:
        return {
            "lines": self.lines[:8],
            "error_codes": self.error_codes,
            "model_numbers": self.model_numbers,
            "brand": self.brand,
            "sharpness": round(self.sharpness, 1),
            "blurry": self.blurry,
            "needs_retake": self.needs_retake,
            "error": self.error,
        }


class OCRUnavailable(RuntimeError):
    pass


_READER: Any = None
_TRIED = False


def available() -> bool:
    """Whether OCR can run, without raising if it cannot."""
    try:
        return get_reader() is not None
    except OCRUnavailable:
        return False


def get_reader() -> Any:
    """Process-wide singleton. Loading three ONNX graphs per frame would cost
    more than the frame decode it exists to support."""
    global _READER, _TRIED
    if _READER is None and not _TRIED:
        _TRIED = True
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise OCRUnavailable(
                "frame text reading needs the vision extra: pip install -e '.[vision]'"
            ) from exc
        _READER = RapidOCR()
    if _READER is None:
        raise OCRUnavailable("OCR reader failed to initialise")
    return _READER


def warmup() -> float:
    """Load the ONNX graphs up front, inside the 300 s setup hook."""
    import time

    t0 = time.perf_counter()
    try:
        get_reader()
    except OCRUnavailable:
        return -1.0
    return (time.perf_counter() - t0) * 1000.0


def sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian -- the standard cheap focus measure.

    Computed with an explicit 4-neighbour stencil rather than a convolution
    import, because the whole point of this module is to add capability without
    adding weight to the hot path.
    """
    if gray.ndim != 2 or min(gray.shape) < 3:
        return 0.0
    lap = (
        -4.0 * gray[1:-1, 1:-1]
        + gray[:-2, 1:-1] + gray[2:, 1:-1]
        + gray[1:-1, :-2] + gray[1:-1, 2:]
    )
    return float(lap.var())


def _normalise(source: Any, width: int = 960) -> tuple[np.ndarray, np.ndarray] | None:
    """Decode to RGB and a matched grayscale, at a fixed width.

    Fixed width matters twice: OCR cost scales with pixel count, and the blur
    threshold is only meaningful at a known scale. A 12 MP phone photo and a
    640 px synthetic frame must produce comparable sharpness numbers or the
    abstention rule fires on resolution rather than on focus.
    """
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
        if img.width != width:
            img = img.resize((width, max(1, round(img.height * width / img.width))))
        rgb = np.asarray(img, dtype=np.uint8)
        gray = np.asarray(img.convert("L"), dtype=np.float32)
        return rgb, gray
    except Exception:
        return None


def _extract_codes(text: str) -> list[str]:
    """Pull error codes out of joined OCR text, normalised and deduplicated."""
    found: list[str] = []
    for pattern in _CODE_PATTERNS:
        for match in pattern.findall(text):
            code = re.sub(r"[\s:-]", "", match).upper()
            # A bare two-character token that is really part of a model number
            # would be a damaging false positive, so anything appearing inside a
            # longer alphanumeric run is rejected.
            if code and code not in found:
                found.append(code)
    return found


def read_frame(source: Any) -> FrameText:
    """Read whatever text a frame contains, and judge whether to trust it."""
    decoded = _normalise(source)
    if decoded is None:
        return FrameText(error="frame could not be decoded")
    rgb, gray = decoded

    out = FrameText(sharpness=sharpness(gray))
    out.blurry = out.sharpness < BLUR_FLOOR

    try:
        reader = get_reader()
    except OCRUnavailable as exc:
        out.error = str(exc)
        return out

    try:
        result, _ = reader(rgb)
    except Exception as exc:  # noqa: BLE001 - a reader crash is a result
        out.error = f"ocr failed: {type(exc).__name__}"
        return out

    for _box, text, conf in (result or []):
        confidence = float(conf)
        if confidence < MIN_TEXT_CONFIDENCE:
            continue
        out.lines.append(str(text).strip())
        out.confidences.append(confidence)

    joined = " ".join(out.lines)
    upper = joined.upper()

    out.model_numbers = list(dict.fromkeys(_MODEL_PATTERN.findall(upper)))
    # Error codes are matched on text with model numbers removed, so that the
    # "90T" inside WW90T534DAN cannot be reported as a fault code.
    without_models = upper
    for model in out.model_numbers:
        without_models = without_models.replace(model, " ")
    out.error_codes = [
        c for c in _extract_codes(without_models)
        if not any(c in m for m in out.model_numbers)
    ]

    lowered = joined.lower()
    out.brand = next((b for b in _BRANDS if b in lowered), None)
    return out
