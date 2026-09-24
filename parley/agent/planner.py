"""Planning: which tools to call, with what, and whether to guess.

Entirely manifest-driven. The planner never names a tool; it asks the manifest
which tools serve the current intent, which of their required parameters are
bound, and whether each is read-only. An unseen tool with a well-formed schema
is planned exactly like a familiar one, which is the property the public suite's
"unseen tools" line is testing for.

The speculation rule falls out of the theme's own scope note — *"full-duplex:
begin retrieving before the utterance ends"*:

    end_of_turn not yet seen  ->  read-only calls go out SPECULATIVELY
    end_of_turn seen          ->  calls are CONFIRMED

Combined with the dispatcher's join semantics, that is the entire latency-hiding
story: the search starts mid-sentence, and when the turn ends the confirmation
*adopts* the call already in flight instead of issuing a second one. The result
is ready at max(turn, tool) rather than turn + tool, and a wrong guess costs a
cancelled read-only call and nothing else.

State-modifying tools are never speculated, so a half-finished sentence can
never book anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..protocol.manifest import ToolManifest, ToolSpec
from ..protocol.state import SessionState

SPECULATE_THRESHOLD = 0.7
"""Minimum slot confidence before guessing. Misses are free, so this is low."""


@dataclass
class PlannedCall:
    tool: str
    args: dict[str, Any]
    read_slots: set[str] = field(default_factory=set)
    speculative: bool = False
    mutating: bool = False
    reason: str = ""


class Planner:
    def __init__(self, manifest: ToolManifest) -> None:
        self.manifest = manifest

    # ------------------------------------------------------------------

    def candidates(self, intent: str | None) -> list[ToolSpec]:
        """Tools serving `intent`, plus tools that declare no intent at all.

        Verifiers and compensators are excluded: they are the kernel's business
        and are dispatched by name when an effect needs resolving, never as part
        of a plan.
        """
        return [
            spec
            for spec in self.manifest.tools.values()
            if spec.verifies is None
            and spec.inverse_of is None
            and (spec.intent is None or spec.intent == intent)
        ]

    def bind_args(self, spec: ToolSpec, state: SessionState) -> tuple[dict[str, Any], set[str]] | None:
        """Fill a tool's parameters from state, or None if a required one is missing.

        Returns the *canonical* values — the surface form the user spoke is for
        speech, not for tool arguments.
        """
        args: dict[str, Any] = {}
        read: set[str] = set()

        for param in spec.params:
            slot = state.slots.get(param.name)
            if slot is None:
                if param.required:
                    return None
                continue
            args[param.name] = slot.value
            read.add(param.name)

        return args, read

    def plan(
        self,
        state: SessionState,
        *,
        end_of_turn: bool,
        committed: bool = False,
        just_bound: set[str] | None = None,
    ) -> list[PlannedCall]:
        """Decide what to call now.

        `committed` gates state-modifying tools: they are planned only when the
        user has actually asked for the action this turn. `just_bound` is the
        set of slots the current turn supplied, used so that a mutating tool
        fires when its last missing parameter arrives rather than on every
        subsequent turn.
        """
        just_bound = just_bound or set()
        out: list[PlannedCall] = []

        for spec in self.candidates(state.intent):
            bound = self.bind_args(spec, state)
            if bound is None:
                continue
            args, read = bound
            if not args:
                continue

            if spec.mutating:
                if not committed:
                    continue
                # Fire when this turn completed the call, not on every turn that
                # happens to have the parameters lying around.
                if not (read & just_bound):
                    continue
                out.append(
                    PlannedCall(
                        tool=spec.name, args=args, read_slots=read,
                        speculative=False, mutating=True,
                        reason="all required parameters bound and the user asked for it",
                    )
                )
                continue

            confident = all(state.confidence(n) >= SPECULATE_THRESHOLD for n in read)
            if not end_of_turn and not confident:
                continue

            out.append(
                PlannedCall(
                    tool=spec.name, args=args, read_slots=read,
                    speculative=not end_of_turn,
                    mutating=False,
                    reason=(
                        "read-only and slots are confident; starting before the turn ends"
                        if not end_of_turn
                        else "turn complete"
                    ),
                )
            )

        return out

    def slots_to_keep(self, new_intent: str | None) -> set[str]:
        """Which slots survive a goal switch.

        A slot survives iff some tool serving the new goal could consume it —
        dates and party sizes carry over from flights to hotels, flight numbers
        do not. Computed from the manifest so it stays right for unseen tools.
        """
        return self.manifest.params_for_intent(new_intent)

    def missing_for(self, intent: str | None, state: SessionState) -> list[str]:
        """Required parameters still unbound, for asking a specific question."""
        missing: list[str] = []
        for spec in self.candidates(intent):
            if spec.mutating:
                continue
            for name in sorted(spec.required_params):
                if name not in state.slots and name not in missing:
                    missing.append(name)
        return missing
