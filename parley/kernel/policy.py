"""Interruption policy — two orthogonal decisions, not one.

Every production voice framework we surveyed treats an interruption as a single
decision and answers it the same way: flush everything. Pipecat "cancels any
pending tasks in LLM and TTS"; LiveKit stops the TTS stream and clears the
buffer; TASTE2 runs a four-step teardown. See [RESEARCH.md](../../docs/RESEARCH.md) R1.1.

That conflates two questions that have different answers:

    floor  — what happens to our voice?      CONTINUE / ADAPT / YIELD
    work   — what happens to our tool calls? KEEP_ALL / SELECTIVE / CANCEL_ALL

The floor verbs are from the overlapping-speech literature (arXiv 2609.13117);
the work axis is ours. Crossing them gives the matrix below. The naive system is
the diagonal — yield implies cancel everything — and the score lives off it:

                  KEEP_ALL            SELECTIVE          CANCEL_ALL
    CONTINUE      SELF_REPAIR            -                   -
    ADAPT         REFINEMENT             -                   -
    YIELD         BARGE_IN               SLOT_CORRECTION     GOAL_SWITCH
                  REPEAT_REQUEST

`BARGE_IN` and `REPEAT_REQUEST` are the competitively interesting cells: yield
the floor, keep every tool call. Cancelling there throws away valid work, and
re-running it afterwards is precisely the "stale re-run" the 35% block penalises.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FloorPolicy(str, Enum):
    CONTINUE = "continue"
    """Keep speaking. The user was not addressing us."""
    ADAPT = "adapt"
    """Keep the floor but splice the new constraint into what we are saying."""
    YIELD = "yield"
    """Stop speaking and let the user finish."""


class WorkPolicy(str, Enum):
    KEEP_ALL = "keep_all"
    """Touch nothing in flight."""
    SELECTIVE = "selective"
    """Cancel exactly the in-flight readers of the slots that changed."""
    CANCEL_ALL = "cancel_all"
    """Cancel everything; the goal itself is gone."""


class InterruptionKind(str, Enum):
    SLOT_CORRECTION = "slot_correction"
    GOAL_SWITCH = "goal_switch"
    REFINEMENT = "refinement"
    SELF_REPAIR = "self_repair"
    BARGE_IN = "barge_in"
    REPEAT_REQUEST = "repeat_request"
    BACKCHANNEL = "backchannel"
    """"mhm", "right", "okay" — the user signalling attention, not interrupting."""
    NEW_REQUEST = "new_request"
    """Not an interruption: the opening utterance of a turn with nothing in flight."""


@dataclass(frozen=True)
class InterruptionPolicy:
    kind: InterruptionKind
    floor: FloorPolicy
    work: WorkPolicy
    rationale: str

    @property
    def replans(self) -> bool:
        """Whether the planner should reconsider the goal.

        Only a goal switch does. A self-repair explicitly must not: re-planning
        on every disfluency is how an agent manufactures stale re-runs.
        """
        return self.kind is InterruptionKind.GOAL_SWITCH

    @property
    def is_interruption(self) -> bool:
        """Whether this even counts as the user interrupting us.

        A disfluency, a backchannel and the opening of a turn are all things a
        naive VAD reports as interruptions and none of them are.
        """
        return self.kind not in (
            InterruptionKind.SELF_REPAIR,
            InterruptionKind.BACKCHANNEL,
            InterruptionKind.NEW_REQUEST,
        )

    def to_payload(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "floor": self.floor.value,
            "work": self.work.value,
            "rationale": self.rationale,
        }


POLICIES: dict[InterruptionKind, InterruptionPolicy] = {
    InterruptionKind.SLOT_CORRECTION: InterruptionPolicy(
        InterruptionKind.SLOT_CORRECTION,
        FloorPolicy.YIELD,
        WorkPolicy.SELECTIVE,
        "one slot changed; cancel only the calls that read it",
    ),
    InterruptionKind.GOAL_SWITCH: InterruptionPolicy(
        InterruptionKind.GOAL_SWITCH,
        FloorPolicy.YIELD,
        WorkPolicy.CANCEL_ALL,
        "the goal is gone; keep slots the new goal can still consume",
    ),
    InterruptionKind.REFINEMENT: InterruptionPolicy(
        InterruptionKind.REFINEMENT,
        FloorPolicy.ADAPT,
        WorkPolicy.KEEP_ALL,
        "narrowing an existing query; filter the result rather than re-fetch it",
    ),
    InterruptionKind.SELF_REPAIR: InterruptionPolicy(
        InterruptionKind.SELF_REPAIR,
        FloorPolicy.CONTINUE,
        WorkPolicy.KEEP_ALL,
        "a disfluency, not an instruction; suppress re-planning entirely",
    ),
    InterruptionKind.BARGE_IN: InterruptionPolicy(
        InterruptionKind.BARGE_IN,
        FloorPolicy.YIELD,
        WorkPolicy.KEEP_ALL,
        "the user wants the floor, not a different outcome",
    ),
    InterruptionKind.REPEAT_REQUEST: InterruptionPolicy(
        InterruptionKind.REPEAT_REQUEST,
        FloorPolicy.YIELD,
        WorkPolicy.KEEP_ALL,
        "answerable from the transcript; re-running a tool here would be a stale re-run",
    ),
    InterruptionKind.BACKCHANNEL: InterruptionPolicy(
        InterruptionKind.BACKCHANNEL,
        FloorPolicy.CONTINUE,
        WorkPolicy.KEEP_ALL,
        "a listener noise; stopping here would be the VAD's mistake, not the user's request",
    ),
    InterruptionKind.NEW_REQUEST: InterruptionPolicy(
        InterruptionKind.NEW_REQUEST,
        FloorPolicy.YIELD,
        WorkPolicy.KEEP_ALL,
        "opening utterance; nothing in flight to disturb",
    ),
}


def policy_for(kind: InterruptionKind) -> InterruptionPolicy:
    return POLICIES[kind]
