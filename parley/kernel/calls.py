"""Call records and outcomes — the ledger the 35% block is scored from.

`asyncio.Task.cancel()` is a *request*. It raises `CancelledError` at the task's
next await point; a task that has already passed its final await completes
regardless. So "did we cancel it?" is not a yes/no question, and modelling it as
a boolean is the mistake that produces stale re-runs.

Five terminal outcomes, and the interesting one is the fourth:

    CANCELLED_BEFORE_EFFECT   the cancel landed; nothing happened
    COMPLETED_STILL_VALID     it finished and the current plan still wants it
    COMPLETED_NOW_STALE       it finished anyway and is now wrong
    CANCELLED_UNCERTAIN       we cancelled a state-modifying call and genuinely
                              do not know whether the effect committed
    FAILED                    the tool errored

`CANCELLED_UNCERTAIN` exists because the honest answer to "did the booking go
through?" after a late cancel is *we don't know*. The caller stopped awaiting;
the environment kept going. Collapsing that into CANCELLED_BEFORE_EFFECT is how
an agent's state snapshot silently diverges from the world — the trace looks
clean while task completion fails. It is a non-terminal state: it must be
resolved by a verifier probe, or, failing that, disclosed in the transcript.

Read-only calls skip all of this: they cannot leave an effect, so a cancel is
always CANCELLED_BEFORE_EFFECT.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CallOutcome(str, Enum):
    PENDING = "pending"
    COMPLETED_STILL_VALID = "completed_still_valid"
    COMPLETED_NOW_STALE = "completed_now_stale"
    CANCELLED_BEFORE_EFFECT = "cancelled_before_effect"
    CANCELLED_UNCERTAIN = "cancelled_uncertain"
    FAILED = "failed"
    DUPLICATE_SUPPRESSED = "duplicate_suppressed"
    """Blocked before dispatch by the idempotency ledger. Never reached the tool."""
    COMPENSATED = "compensated"
    """Was COMPLETED_NOW_STALE or CANCELLED_UNCERTAIN; the inverse tool has run."""
    ADOPTED = "adopted"
    """A speculative call that a later confirmation joined. See `Dispatcher`."""

    @property
    def is_terminal(self) -> bool:
        return self not in (CallOutcome.PENDING, CallOutcome.CANCELLED_UNCERTAIN)

    @property
    def needs_resolution(self) -> bool:
        """Outcomes that leave the world and our snapshot possibly disagreeing."""
        return self in (CallOutcome.COMPLETED_NOW_STALE, CallOutcome.CANCELLED_UNCERTAIN)

    @property
    def had_effect(self) -> bool:
        """Did this call definitely change the world?"""
        return self in (CallOutcome.COMPLETED_STILL_VALID, CallOutcome.COMPLETED_NOW_STALE)


@dataclass
class CallRecord:
    """One dispatched tool call, from intent to settled outcome."""

    call_id: str
    tool: str
    args: dict[str, Any]
    read_slots: set[str]
    """Slot names whose values fed `args`. The dataflow edge for invalidation."""
    state_revision: int
    """State revision at dispatch."""
    mutating: bool
    dispatched_at: float

    speculative: bool = False
    idempotency_key: str | None = None

    outcome: CallOutcome = CallOutcome.PENDING
    result: Any = None
    error: str | None = None
    settled_at: float | None = None

    invalidated_by: list[str] = field(default_factory=list)
    """Slot names whose change invalidated this call."""
    cancel_reason: str | None = None
    resolution: str | None = None
    """How a needs_resolution outcome was closed out: 'compensated',
    'verified_no_effect', 'disclosed'."""

    task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    @property
    def in_flight(self) -> bool:
        return self.outcome is CallOutcome.PENDING

    @property
    def signature(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        """Identity by (tool, args) — what speculation join and duplicate
        detection both match on."""
        return (self.tool, tuple(sorted((k, repr(v)) for k, v in self.args.items())))

    def to_payload(self) -> dict[str, Any]:
        """Trace-safe view. Never includes the asyncio task."""
        return {
            "call_id": self.call_id,
            "tool": self.tool,
            "args": self.args,
            "read_slots": sorted(self.read_slots),
            "state_revision": self.state_revision,
            "mutating": self.mutating,
            "speculative": self.speculative,
            "idempotency_key": self.idempotency_key,
            "outcome": self.outcome.value,
            "dispatched_at": self.dispatched_at,
            "settled_at": self.settled_at,
            "invalidated_by": self.invalidated_by,
            "cancel_reason": self.cancel_reason,
            "resolution": self.resolution,
            "error": self.error,
        }


class CallRegistry:
    """Every call the session has made, indexed for the questions we actually ask.

    The only non-obvious index is `readers_of`: given a slot that just changed,
    which in-flight calls consumed it? That query is the entire selective
    cancellation mechanism, so it is a dict lookup rather than a scan.
    """

    def __init__(self) -> None:
        self._calls: dict[str, CallRecord] = {}
        self._by_slot: dict[str, set[str]] = {}
        self._counter = 0

    def next_call_id(self, prefix: str = "call") -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def add(self, record: CallRecord) -> CallRecord:
        self._calls[record.call_id] = record
        for slot in record.read_slots:
            self._by_slot.setdefault(slot, set()).add(record.call_id)
        return record

    def __contains__(self, call_id: object) -> bool:
        return call_id in self._calls

    def __getitem__(self, call_id: str) -> CallRecord:
        return self._calls[call_id]

    def get(self, call_id: str) -> CallRecord | None:
        return self._calls.get(call_id)

    def __len__(self) -> int:
        return len(self._calls)

    def __iter__(self):
        return iter(self._calls.values())

    @property
    def all(self) -> list[CallRecord]:
        return list(self._calls.values())

    def in_flight(self) -> list[CallRecord]:
        return [c for c in self._calls.values() if c.in_flight]

    def readers_of(self, slot: str, *, in_flight_only: bool = True) -> list[CallRecord]:
        """In-flight calls whose arguments depended on `slot`."""
        ids = self._by_slot.get(slot, set())
        out = [self._calls[i] for i in ids]
        return [c for c in out if c.in_flight] if in_flight_only else out

    def invalidated_by(self, slots: set[str]) -> list[CallRecord]:
        """The selective-cancellation set: every in-flight reader of any changed slot.

        Deduplicated and returned in dispatch order so cancellation is itself
        deterministic — adversarial-timing tests depend on that.
        """
        hit: dict[str, CallRecord] = {}
        for slot in slots:
            for call in self.readers_of(slot):
                hit[call.call_id] = call
        return sorted(hit.values(), key=lambda c: (c.dispatched_at, c.call_id))

    def live_match(self, tool: str, args: dict[str, Any]) -> CallRecord | None:
        """An in-flight call with the same (tool, args). The join candidate."""
        probe = (tool, tuple(sorted((k, repr(v)) for k, v in args.items())))
        for call in self._calls.values():
            if call.in_flight and call.signature == probe:
                return call
        return None

    def completed_match(self, tool: str, args: dict[str, Any]) -> CallRecord | None:
        """A finished, still-valid call with the same (tool, args).

        Re-running a read-only tool whose inputs have not changed since it
        answered *is* a stale re-run — it burns latency and pollutes the final
        response with duplicate results. Found this in the very first end-to-end
        trace: three identical flight searches in one session, because the
        planner re-plans every turn and the slots were still bound.
        """
        probe = (tool, tuple(sorted((k, repr(v)) for k, v in args.items())))
        for call in reversed(list(self._calls.values())):
            if call.outcome is CallOutcome.COMPLETED_STILL_VALID and call.signature == probe:
                return call
        return None

    def superseded_speculations(self, tool: str, args: dict[str, Any]) -> list[CallRecord]:
        """In-flight speculative calls of `tool` that this dispatch replaces.

        A guess made from two slots is obsolete once a third binds: same tool,
        different arguments, and nobody will ever want its answer.
        """
        probe = (tool, tuple(sorted((k, repr(v)) for k, v in args.items())))
        return [
            c for c in self._calls.values()
            if c.in_flight and c.speculative and c.tool == tool and c.signature != probe
        ]

    def unresolved(self) -> list[CallRecord]:
        """Calls whose outcome leaves the world and our snapshot possibly disagreeing.

        Must be empty before a final response is emitted.
        """
        return [
            c
            for c in self._calls.values()
            if c.outcome.needs_resolution and c.resolution is None
        ]

    def to_payload(self) -> list[dict[str, Any]]:
        return [c.to_payload() for c in self._calls.values()]
