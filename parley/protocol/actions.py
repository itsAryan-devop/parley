"""Outbound actions — the right-hand queue of the interface contract.

Guide §3.1 outputs: spoken fillers, non-blocking tool calls with explicit
`call_id`, cancellations, clarification requests, and final responses carrying
structured State Snapshots.

One addition of ours: every `Speak` action carries `claims` — the list of facts
the utterance asserts, each tagged with the evidence the kernel held at the time.
The provable-speech gate (design note §7.2) refuses to emit an utterance whose
claims it cannot back. Because scoring reads trace logs, putting the warrant for
each claim *into the trace* is what makes truthfulness auditable rather than
merely intended.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .state import StateSnapshot


class ActionType(str, Enum):
    SPEAK = "speak"
    TOOL_CALL = "tool_call"
    CANCEL = "cancel"
    CLARIFY = "clarify"
    FINAL_RESPONSE = "final_response"


class SpeechKind(str, Enum):
    """What role an utterance plays in floor management.

    Objective 1 penalises "false completion claims or excessive fillers", so the
    kind is explicit and the floor manager rations each kind separately.
    """

    ACK = "ack"
    """Grounded acknowledgment naming real extracted slots."""
    PROGRESS = "progress"
    """Narration while the slow path runs."""
    FILLER = "filler"
    """Content-free hold. Rationed hardest."""
    REPAIR = "repair"
    """Acknowledging that we absorbed a correction or a stale effect."""
    HANDOFF = "handoff"
    """Yielding the floor on barge-in."""


class ClaimKind(str, Enum):
    SLOT_VALUE = "slot_value"
    """"...to Mumbai" — warranted by a bound slot above the speak threshold."""
    IN_PROGRESS = "in_progress"
    """"...still checking" — warranted by a live CallRecord."""
    COMPLETED = "completed"
    """"...booked" — warranted ONLY by an outcome of COMPLETED_STILL_VALID."""
    PERCEPTION = "perception"
    """"...I can see a red LED" — warranted by a Perception above threshold."""


class Claim(BaseModel):
    """A single assertion inside an utterance, plus its warrant."""

    model_config = ConfigDict(extra="forbid")

    kind: ClaimKind
    subject: str
    """Slot name, call_id, or perception id the claim is about."""
    value: Any = None
    warrant: str
    """Human-readable justification recorded in the trace."""
    confidence: float = 1.0


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float = 0.0
    """Virtual clock at emission, milliseconds."""
    seq: int = 0


class Speak(_Base):
    type: Literal[ActionType.SPEAK] = ActionType.SPEAK
    text: str
    kind: SpeechKind = SpeechKind.ACK
    claims: list[Claim] = Field(default_factory=list)
    interruptible: bool = True
    """Whether a barge-in may cut this utterance short. Fillers always may."""


class ToolCall(_Base):
    type: Literal[ActionType.TOOL_CALL] = ActionType.TOOL_CALL
    call_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)

    read_slots: list[str] = Field(default_factory=list)
    """Slot names whose values fed `args`. The dataflow edge that makes
    selective cancellation possible (design note §6.2)."""
    state_revision: int = 0
    """State revision at dispatch. A read slot with a later revision => stale."""
    mutating: bool = False
    idempotency_key: str | None = None
    speculative: bool = False
    """Dispatched ahead of confirmation. Only ever true for read-only tools."""


class Cancel(_Base):
    type: Literal[ActionType.CANCEL] = ActionType.CANCEL
    call_id: str
    reason: str
    """e.g. 'slot_correction:destination', 'goal_switch', 'superseded'."""
    invalidated_by_slots: list[str] = Field(default_factory=list)


class Clarify(_Base):
    type: Literal[ActionType.CLARIFY] = ActionType.CLARIFY
    question: str
    slot: str | None = None
    options: list[str] = Field(default_factory=list)
    reason: str = "ambiguous"
    """'ambiguous' | 'low_confidence' | 'missing_required' | 'conflict'."""


class FinalResponse(_Base):
    type: Literal[ActionType.FINAL_RESPONSE] = ActionType.FINAL_RESPONSE
    text: str
    state: StateSnapshot
    claims: list[Claim] = Field(default_factory=list)
    grounded_on: list[str] = Field(default_factory=list)
    """call_ids whose results this response is grounded in."""


Action = Annotated[
    Union[Speak, ToolCall, Cancel, Clarify, FinalResponse],
    Field(discriminator="type"),
]


class ActionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action


def parse_action(payload: dict[str, Any]) -> Any:
    return ActionEnvelope(action=payload).action
