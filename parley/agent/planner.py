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

    @property
    def plannable(self) -> list[ToolSpec]:
        """Every tool a plan may contain.

        Verifiers and compensators are excluded: they are the kernel's business,
        dispatched by name when an effect needs resolving, never as part of a
        plan. Including them would let the planner "helpfully" cancel a booking.
        """
        return [
            spec
            for spec in self.manifest.tools.values()
            if spec.verifies is None and spec.inverse_of is None
        ]

    def candidates(self, intent: str | None) -> list[ToolSpec]:
        """Tools serving `intent`, plus tools that declare no intent at all."""
        return [s for s in self.plannable if s.intent is None or s.intent == intent]

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

        for spec in self.plannable:
            bound = self.bind_args(spec, state)
            if bound is None:
                continue
            args, read = bound
            if not args:
                continue

            # A tool belonging to another goal is still worth running when the
            # user just handed it exactly what it needs. "...and a hotel in Goa"
            # alongside a live flight search is an *addition*, not a switch, and
            # gating purely on the single current intent meant the hotel search
            # never ran. Requiring a freshly-bound parameter is what stops this
            # from re-planning the whole manifest every turn.
            serves_current_goal = spec.intent is None or spec.intent == state.intent
            if not serves_current_goal and not (read & just_bound):
                continue

            if spec.mutating:
                # `committed` already means the user asked for the action *this
                # turn* — a commit verb was spoken. That is the whole gate.
                #
                # It used to additionally require a freshly-bound parameter,
                # which silently broke the commonest shape there is: bind the
                # subject in one turn ("look at this panel"), ask for the action
                # in the next ("raise a ticket for that"). Nothing was bound by
                # the second turn, so the ticket was never raised. Re-firing on
                # every committal turn is safe precisely because the idempotency
                # ledger suppresses the duplicate and now says so out loud.
                if not committed:
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

    def infer_intent(self, state: SessionState) -> str | None:
        """Work out the goal from the slots that are bound, when nobody said it.

        A camera frame binds `label` without anyone uttering a goal word, and
        with `intent` left None the intent-tagged tools were all filtered out —
        so a perfectly good perception led to no tool call at all. If the bound
        slots are consumable by exactly one goal's tools, that is the goal.
        Ambiguous evidence returns None rather than a guess.
        """
        bound = set(state.slots)
        if not bound:
            return None

        scored: dict[str, int] = {}
        for intent in self.manifest.intents():
            overlap = len(bound & self.manifest.params_for_intent(intent))
            if overlap:
                scored[intent] = overlap

        if not scored:
            return None
        ranked = sorted(scored.items(), key=lambda kv: -kv[1])
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return None
        return ranked[0][0]

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
