"""Inbound events — the left-hand queue of the interface contract.

Guide §3.1 inputs: transcribed text chunks (with end-of-turn markers), raw audio
clips (WAV), video frames (PNG), interruption signals, asynchronous tool results,
and scenario tool manifests.

Every event carries `t`, a *virtual* clock timestamp in milliseconds. Wall-clock
time never enters the agent: the harness owns the timeline, which is what makes
replay deterministic and latency scoring meaningful.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    SESSION_START = "session_start"
    TOOL_MANIFEST = "tool_manifest"
    TRANSCRIPT_CHUNK = "transcript_chunk"
    AUDIO_CLIP = "audio_clip"
    VIDEO_FRAME = "video_frame"
    INTERRUPTION = "interruption"
    TOOL_RESULT = "tool_result"
    SESSION_END = "session_end"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float = 0.0
    """Virtual clock, milliseconds since session start."""
    seq: int = 0
    """Monotonic ordering tiebreak for events sharing a timestamp."""


class SessionStart(_Base):
    type: Literal[EventType.SESSION_START] = EventType.SESSION_START
    session_id: str
    scenario: str | None = None
    modality_mix: list[str] = Field(default_factory=lambda: ["text"])


class ToolManifestEvent(_Base):
    type: Literal[EventType.TOOL_MANIFEST] = EventType.TOOL_MANIFEST
    manifest: Any
    """Raw manifest payload; parsed by `protocol.manifest.parse_manifest`."""


class TranscriptChunk(_Base):
    type: Literal[EventType.TRANSCRIPT_CHUNK] = EventType.TRANSCRIPT_CHUNK
    text: str
    end_of_turn: bool = False
    """The end-of-turn marker named in the guide. Partial chunks arrive with
    False and may be superseded by later chunks in the same turn."""
    silence_ms: float | None = None
    """Trailing silence the recogniser observed after these words, if it reports
    it. Optional and defaulted: existing scenarios carry no value and the agent
    falls back to inter-chunk timing. The learned endpointer reads it as its
    timing cue — a recogniser can measure this pause even on the turns where it
    fails to set `end_of_turn`, which is exactly when endpointing has to work."""
    speaker: str = "user"


class AudioClip(_Base):
    type: Literal[EventType.AUDIO_CLIP] = EventType.AUDIO_CLIP
    clip_id: str
    path: str | None = None
    data_b64: str | None = None
    sample_rate: int | None = None
    duration_ms: float | None = None
    mime: str = "audio/wav"


class VideoFrame(_Base):
    type: Literal[EventType.VIDEO_FRAME] = EventType.VIDEO_FRAME
    frame_id: str
    path: str | None = None
    data_b64: str | None = None
    width: int | None = None
    height: int | None = None
    mime: str = "image/png"


class InterruptionSignal(_Base):
    """A bare barge-in signal from VAD.

    Carries no semantics: the *meaning* of the interruption arrives separately as
    transcript chunks. Classifying it (§4 of the design note) is the agent's job,
    not the harness's.
    """

    type: Literal[EventType.INTERRUPTION] = EventType.INTERRUPTION
    source: str = "vad"


class ToolResult(_Base):
    type: Literal[EventType.TOOL_RESULT] = EventType.TOOL_RESULT
    call_id: str
    ok: bool = True
    result: Any = None
    error: str | None = None
    latency_ms: float | None = None
    retryable: bool = False


class SessionEnd(_Base):
    type: Literal[EventType.SESSION_END] = EventType.SESSION_END
    reason: str = "complete"


Event = Annotated[
    Union[
        SessionStart,
        ToolManifestEvent,
        TranscriptChunk,
        AudioClip,
        VideoFrame,
        InterruptionSignal,
        ToolResult,
        SessionEnd,
    ],
    Field(discriminator="type"),
]


class EventEnvelope(BaseModel):
    """Wrapper used only for parsing a heterogeneous event off the wire."""

    model_config = ConfigDict(extra="forbid")

    event: Event


def parse_event(payload: dict[str, Any]) -> Any:
    """Parse one inbound event dict into its concrete type."""
    return EventEnvelope(event=payload).event
