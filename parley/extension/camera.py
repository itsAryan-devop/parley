"""Camera-based device troubleshooting -- the LiveKit-free half of the extension.

`fdb/agent_extension.py` is the live voice agent; everything it decides lives
here so it can be tested offline against `media/scenarios/frames/`.

Three rules carry over from PARLEY's kernel unchanged:

1. **Ask, don't guess.** `parley.multimodal.ground_frame` already abstains when
   it is unsure: two labels too close to separate, or a frame that resembles
   nothing it was fitted on. `summarise` turns that into a result the LLM
   cannot mistake for a diagnosis -- `status: "ask"` or `"retake"` with the
   exact question to say, and no label.
2. **Grounded remedies only.** A diagnosed label comes with the matching page
   from the device manual (`harness.mockenv.world.MANUAL_PAGES`), so the agent
   reads out steps it was given rather than steps it made up.
3. **Never the same frame twice.** Diagnoses go through `parley.fdb.ToolGuard`
   (the ported idempotency ledger) keyed on the frame's pixels, one guard per
   session. On a live camera, sensor noise makes consecutive frames differ, so
   asking again after the user moves the camera is a genuinely new look; an
   identical frame (a still image, a frozen track) returns the earlier answer.

State is per session: `LatestFrame` holds one frame and nothing else, and is
cleared when the session or the video track ends. Nothing is cached across
sessions.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import itertools
from dataclasses import dataclass
from typing import Any

from harness.mockenv.world import MANUAL_PAGES
from parley.fdb.guard import ToolGuard
from parley.multimodal import ground_frame
from parley.protocol.events import VideoFrame

TOOL_NAME = "diagnose_device_frame"
MAX_WIDTH = 960
"""Frames are downscaled to this width before encoding; OCR normalises to the
same width, so nothing it could read is lost."""

RETAKE_QUESTION = (
    "I can't make out the device clearly enough to diagnose it. Could you hold the "
    "camera closer and square-on to the display or the lights, without glare?"
)
NO_FRAME = {
    "status": "no_frame",
    "say": "I can't see your camera yet. Could you turn it on and point it at the device?",
}


@dataclass
class Snapshot:
    rgba: bytes
    width: int
    height: int
    seq: int


class LatestFrame:
    """The most recent camera frame and nothing else. One per session."""

    def __init__(self) -> None:
        self._snap: Snapshot | None = None
        self._seq = itertools.count(1)

    def set(self, rgba: bytes, width: int, height: int) -> None:
        self._snap = Snapshot(bytes(rgba), width, height, next(self._seq))

    def get(self) -> Snapshot | None:
        return self._snap

    def clear(self) -> None:
        self._snap = None


def rgba_to_png(rgba: bytes, width: int, height: int, max_width: int = MAX_WIDTH) -> bytes:
    from PIL import Image

    img = Image.frombytes("RGBA", (width, height), rgba).convert("RGB")
    if width > max_width:
        img = img.resize((max_width, round(height * max_width / width)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _manual(label: str) -> dict[str, Any] | None:
    page = MANUAL_PAGES.get(label)
    if page is None:
        return None
    return {"title": page["title"], "meaning": page["meaning"], "steps": page["steps"]}


def summarise(perception: Any) -> dict[str, Any]:
    """A perception -> the tool result the LLM sees. Exactly one of three outcomes."""
    if perception.label is not None:
        out: dict[str, Any] = {
            "status": "diagnosed",
            "label": perception.label,
            "confidence": round(perception.confidence, 2),
        }
        if perception.evidence:
            out["evidence"] = perception.evidence
        manual = _manual(perception.label)
        if manual:
            out["manual"] = manual
        return out

    if perception.ambiguous and perception.question:
        # Rivals' manual pages ride along so that once the user answers, the
        # agent can give the right steps without guessing or re-diagnosing.
        return {
            "status": "ask",
            "question": perception.question,
            "candidates": list(perception.candidates),
            "manual_if_confirmed": {
                c: m for c in perception.candidates if (m := _manual(c)) is not None
            },
        }

    return {
        "status": "retake",
        "question": perception.question or RETAKE_QUESTION,
        "reason": perception.error or "low confidence",
    }


def _ground(png: bytes, frame_id: str) -> Any:
    # ground_frame is async in signature but CPU-bound (OCR ~1 s); run it off
    # the event loop so the voice pipeline keeps streaming audio meanwhile.
    frame = VideoFrame(frame_id=frame_id, data_b64=base64.b64encode(png).decode("ascii"))
    return asyncio.run(ground_frame(frame))


class FrameDiagnoser:
    """Diagnoses frames at most once each. One per session."""

    def __init__(self) -> None:
        self.guard = ToolGuard(grace_s=0.0)  # dedup only; no commit window needed

    async def diagnose(self, png: bytes, frame_id: str = "camera") -> tuple[dict[str, Any], bool]:
        """Return `(result, executed)`; `executed` is False for a repeated frame."""
        digest = hashlib.sha256(png).hexdigest()[:16]

        async def execute() -> dict[str, Any]:
            perception = await asyncio.to_thread(_ground, png, frame_id)
            return summarise(perception)

        result, executed = await self.guard.run(TOOL_NAME, {"frame": digest}, execute)
        if not executed and isinstance(result.get("result"), dict):
            result = {**result["result"], "repeated": "same frame as before; not re-diagnosed"}
        return result, executed


async def diagnose_latest(latest: LatestFrame, diagnoser: FrameDiagnoser) -> dict[str, Any]:
    """What the agent's tool does: grab the latest frame and diagnose it."""
    snap = latest.get()
    if snap is None:
        return dict(NO_FRAME)
    png = await asyncio.to_thread(rgba_to_png, snap.rgba, snap.width, snap.height)
    result, _ = await diagnoser.diagnose(png, frame_id=f"camera-{snap.seq}")
    return result
