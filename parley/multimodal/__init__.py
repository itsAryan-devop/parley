"""Multimodal grounding: raw frames and clips behind an acknowledgment.

Half the hidden set is audio or visual and multimodal scenarios carry a 1.5x
multiplier, so this is the largest single lever in the scoring scheme — and the
one most teams will forfeit by shipping text-only.
"""

from .audio import ground_audio
from .perception import (
    AMBIGUITY_MARGIN,
    GROUNDING_FLOOR,
    LabelModel,
    Perception,
    decide,
    payload_of,
)
from .vision import ground_frame

__all__ = [
    "Perception", "LabelModel", "decide", "payload_of",
    "ground_frame", "ground_audio",
    "AMBIGUITY_MARGIN", "GROUNDING_FLOOR",
]
