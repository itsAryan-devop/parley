"""Idempotency ledger — the mechanism behind "zero duplicate state-changing calls".

The guide's customer-support use case is "dynamically adjusting parameters
mid-booking **without double-booking**", and IHBench reports duplicate tool calls
as one of the four standard failure modes of deployed voice agents
([RESEARCH.md](../../docs/RESEARCH.md) R1.2). Both describe the same thing: a
state change that happens twice because the plan was revised between dispatch
and completion.

A key is derived from (tool, intent, resolved arguments) and claimed **before**
dispatch, not after. Claiming after is a race: two re-plans a few milliseconds
apart both check an empty ledger and both dispatch.

The subtlety is what counts as "already done". Four settled states, three
different answers:

    succeeded    -> suppress. The effect exists.
    in flight    -> join. The effect is on its way; a second call would duplicate it.
    failed       -> allow. A retry after a transient fault is not a duplicate.
    compensated  -> allow. The effect was undone, so redoing it is a new action.

Collapsing "failed" into "already done" would break retry-after-fault, which the
public suite names explicitly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..protocol.manifest import ToolSpec


class ClaimResult(str, Enum):
    FRESH = "fresh"
    """No prior claim. Dispatch."""
    IN_FLIGHT = "in_flight"
    """An identical call is already running. Join it; do not dispatch."""
    ALREADY_SUCCEEDED = "already_succeeded"
    """The effect exists. Suppress; do not dispatch."""
    RETRYABLE = "retryable"
    """A prior attempt failed or was compensated. Dispatch."""


class EntryState(str, Enum):
    IN_FLIGHT = "in_flight"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATED = "compensated"


@dataclass
class LedgerEntry:
    key: str
    tool: str
    state: EntryState
    call_ids: list[str] = field(default_factory=list)
    claimed_at: float = 0.0
    settled_at: float | None = None


def derive_key(spec: ToolSpec, intent: str | None, args: dict[str, Any]) -> str:
    """Stable identity for a state-modifying action.

    Restricted to `spec.idempotency_params` when the manifest declares them, so
    a manifest can say that two bookings differing only in, say, a client-side
    request id are the same action.

    sha256 rather than `hash()` because the latter is salted per process, and a
    key that changes between runs is not an idempotency key.
    """
    names = spec.idempotency_params if spec.idempotency_params is not None else sorted(args)
    body = "|".join(f"{n}={args.get(n)!r}" for n in sorted(names))
    raw = f"{spec.name}|{intent or ''}|{body}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class IdempotencyLedger:
    """Session-scoped. No cross-session caching, per the guide's state scope."""

    def __init__(self) -> None:
        self._entries: dict[str, LedgerEntry] = {}

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def get(self, key: str) -> LedgerEntry | None:
        return self._entries.get(key)

    def claim(self, key: str, tool: str, call_id: str, t: float) -> tuple[ClaimResult, LedgerEntry]:
        """Reserve `key` for `call_id`. Call this *before* dispatch."""
        entry = self._entries.get(key)

        if entry is None:
            entry = LedgerEntry(key=key, tool=tool, state=EntryState.IN_FLIGHT, claimed_at=t)
            entry.call_ids.append(call_id)
            self._entries[key] = entry
            return ClaimResult.FRESH, entry

        if entry.state is EntryState.IN_FLIGHT:
            return ClaimResult.IN_FLIGHT, entry

        if entry.state is EntryState.SUCCEEDED:
            return ClaimResult.ALREADY_SUCCEEDED, entry

        # FAILED or COMPENSATED: the world does not hold this effect, so doing
        # it now is a new action rather than a repeat of an old one.
        entry.state = EntryState.IN_FLIGHT
        entry.call_ids.append(call_id)
        entry.claimed_at = t
        entry.settled_at = None
        return ClaimResult.RETRYABLE, entry

    def settle(self, key: str, state: EntryState, t: float) -> LedgerEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        entry.state = state
        entry.settled_at = t
        return entry

    def succeeded(self, key: str, t: float) -> LedgerEntry | None:
        return self.settle(key, EntryState.SUCCEEDED, t)

    def failed(self, key: str, t: float) -> LedgerEntry | None:
        return self.settle(key, EntryState.FAILED, t)

    def compensated(self, key: str, t: float) -> LedgerEntry | None:
        return self.settle(key, EntryState.COMPENSATED, t)

    # -- introspection for the trace and the scorer ------------------------

    @property
    def entries(self) -> list[LedgerEntry]:
        return list(self._entries.values())

    def committed_keys(self) -> set[str]:
        return {k for k, e in self._entries.items() if e.state is EntryState.SUCCEEDED}

    def to_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "key": e.key,
                "tool": e.tool,
                "state": e.state.value,
                "call_ids": e.call_ids,
                "claimed_at": e.claimed_at,
                "settled_at": e.settled_at,
            }
            for e in self._entries.values()
        ]
