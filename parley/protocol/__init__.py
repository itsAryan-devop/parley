"""Typed wire contracts for the Theme 5 interface: events in, actions out."""

from .actions import (
    Action,
    ActionType,
    Cancel,
    Claim,
    ClaimKind,
    Clarify,
    FinalResponse,
    Speak,
    SpeechKind,
    ToolCall,
    parse_action,
)
from .events import (
    AudioClip,
    Event,
    EventType,
    InterruptionSignal,
    SessionEnd,
    SessionStart,
    ToolManifestEvent,
    ToolResult,
    TranscriptChunk,
    VideoFrame,
    parse_event,
)
from .manifest import ManifestError, ParamSpec, ToolManifest, ToolSpec, parse_manifest
from .state import (
    SessionState,
    Slot,
    SlotMeta,
    SlotSource,
    StateDelta,
    StateSnapshot,
)

__all__ = [
    # events
    "Event", "EventType", "SessionStart", "ToolManifestEvent", "TranscriptChunk",
    "AudioClip", "VideoFrame", "InterruptionSignal", "ToolResult", "SessionEnd",
    "parse_event",
    # actions
    "Action", "ActionType", "Speak", "SpeechKind", "ToolCall", "Cancel", "Clarify",
    "FinalResponse", "Claim", "ClaimKind", "parse_action",
    # state
    "SessionState", "Slot", "SlotMeta", "SlotSource", "StateDelta", "StateSnapshot",
    # manifest
    "ToolManifest", "ToolSpec", "ParamSpec", "parse_manifest", "ManifestError",
]
