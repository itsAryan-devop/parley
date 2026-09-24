"""Session state: the one object the whole system agrees on.

Snapshot accuracy is scored in *both* the 40% (task completion) and 10% (safety &
protocol) blocks, so `SessionState` is deliberately the only mutable thing in the
system and it is mutated only through the named operations at the bottom of this
module. Nothing else may reach into `.slots`.

The `revision` counter is what makes slot-dataflow cancellation possible: a call
dispatched while the state was at revision *r*, having read slot *s*, is stale
exactly when `slots[s].revision > r`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SlotSource(str, Enum):
    """Where a slot value came from. Drives grounding in the final response."""

    TEXT = "text"
    AUDIO = "audio"
    VISION = "vision"
    INFERRED = "inferred"
    DEFAULT = "default"


class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: Any = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: SlotSource = SlotSource.TEXT
    revision: int = 0
    """State revision at which this slot last changed."""
    evidence: str | None = None
    """The transcript span / frame id / audio clip id that produced the value."""
    surface: str | None = None
    """How the user actually said it, when that differs from the canonical value.

    `value` is what goes into tool arguments and into the scored snapshot —
    "BOM". `surface` is what goes into speech — "Mumbai". Saying the airport
    code back to someone who said a city name is the kind of small unnaturalness
    the quality multiplier is there to catch.
    """

    @property
    def spoken(self) -> Any:
        return self.surface if self.surface is not None else self.value


class SlotMeta(BaseModel):
    """Per-slot provenance, emitted alongside the flat slot values."""

    model_config = ConfigDict(extra="forbid")

    confidence: float
    source: SlotSource
    revision: int
    evidence: str | None = None


class StateSnapshot(BaseModel):
    """The structured snapshot carried by every final response.

    The guide asks for "intent and slot values"; `slots` is therefore a flat
    name -> value mapping so it is trivially machine-checkable. Provenance rides
    alongside in `slot_meta` rather than contaminating the required shape.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    revision: int
    intent: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    slot_meta: dict[str, SlotMeta] = Field(default_factory=dict)


class StateDelta(BaseModel):
    """What a single mutation changed. Handed to the kernel to drive invalidation."""

    model_config = ConfigDict(extra="forbid")

    revision: int
    changed_slots: list[str] = Field(default_factory=list)
    """Slots whose value was *corrected* — these invalidate their readers."""
    added_slots: list[str] = Field(default_factory=list)
    """Slots bound for the first time — these invalidate nothing."""
    cleared_slots: list[str] = Field(default_factory=list)
    intent_changed: bool = False
    previous_intent: str | None = None

    @property
    def invalidating_slots(self) -> set[str]:
        """Slot names whose change should invalidate in-flight readers.

        Adding a slot that was previously unbound cannot invalidate anything: no
        in-flight call can have read a value that did not exist.
        """
        return set(self.changed_slots) | set(self.cleared_slots)


class SessionState(BaseModel):
    """Session-scoped only. No cross-session caching (guide §6)."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    intent: str | None = None
    slots: dict[str, Slot] = Field(default_factory=dict)
    revision: int = 0

    tombstones: dict[str, int] = Field(default_factory=dict)
    """Slots that were cleared, and the revision at which that happened.

    A cleared slot and a slot that never existed look identical once the entry
    is gone, but they mean opposite things for staleness: clearing `seat`
    invalidates every in-flight call that read it, whereas a call claiming to
    have read a slot that was never bound cannot have been invalidated by
    anything. Deleting outright conflated the two and marked healthy calls
    stale. The tombstone keeps just enough history to tell them apart.
    """

    # ---- the closed set of mutations -------------------------------------

    def _bump(self) -> int:
        self.revision += 1
        return self.revision

    def set_slot(
        self,
        name: str,
        value: Any,
        *,
        confidence: float = 1.0,
        source: SlotSource = SlotSource.TEXT,
        evidence: str | None = None,
        surface: str | None = None,
    ) -> StateDelta:
        """Bind a slot. Correcting an existing binding invalidates its readers;
        binding a fresh slot does not."""
        existed = name in self.slots
        same_value = existed and self.slots[name].value == value
        rev = self.revision if same_value else self._bump()

        self.tombstones.pop(name, None)  # re-binding revives the slot
        self.slots[name] = Slot(
            name=name,
            value=value,
            confidence=confidence,
            source=source,
            revision=rev,
            evidence=evidence,
            surface=surface,
        )

        if same_value:
            return StateDelta(revision=rev)
        if existed:
            return StateDelta(revision=rev, changed_slots=[name])
        return StateDelta(revision=rev, added_slots=[name])

    def patch_slot(self, name: str, value: Any, **kw: Any) -> StateDelta:
        """A localized correction (guide objective 3).

        Identical mechanics to `set_slot`; the separate name exists so that call
        sites — and the trace — record the *speech act* that caused the change.
        """
        return self.set_slot(name, value, **kw)

    def clear_slot(self, name: str) -> StateDelta:
        if name not in self.slots:
            return StateDelta(revision=self.revision)
        rev = self._bump()
        del self.slots[name]
        self.tombstones[name] = rev
        return StateDelta(revision=rev, cleared_slots=[name])

    def set_intent(self, intent: str | None) -> StateDelta:
        if intent == self.intent:
            return StateDelta(revision=self.revision)
        prev = self.intent
        rev = self._bump()
        self.intent = intent
        return StateDelta(revision=rev, intent_changed=True, previous_intent=prev)

    def retain_for_goal_switch(self, new_intent: str | None, keep: set[str]) -> StateDelta:
        """Switch goals while keeping slots that are still applicable.

        `keep` is computed by the kernel from the tool manifest (which parameter
        names the new intent's tools accept), never hardcoded here — the guide
        warns that unseen tools appear in both suites.
        """
        prev = self.intent
        dropped = [n for n in self.slots if n not in keep]
        changed = prev != new_intent or bool(dropped)
        rev = self._bump() if changed else self.revision

        for n in dropped:
            del self.slots[n]
            self.tombstones[n] = rev
        self.intent = new_intent

        return StateDelta(
            revision=rev,
            cleared_slots=dropped,
            intent_changed=prev != new_intent,
            previous_intent=prev,
        )

    # ---- read-only views --------------------------------------------------

    def get(self, name: str, default: Any = None) -> Any:
        slot = self.slots.get(name)
        return default if slot is None else slot.value

    def confidence(self, name: str) -> float:
        slot = self.slots.get(name)
        return 0.0 if slot is None else slot.confidence

    def is_stale(self, read_slots: set[str], dispatched_revision: int) -> bool:
        """Did any slot this call depended on change after the call went out?

        Three cases, and the third is the one that matters:
          * still bound   -> stale iff it changed after dispatch
          * tombstoned    -> stale iff it was cleared after dispatch
          * never seen    -> not stale; nothing that does not exist can have
                             invalidated this call
        """
        for name in read_slots:
            slot = self.slots.get(name)
            if slot is not None:
                if slot.revision > dispatched_revision:
                    return True
            elif self.tombstones.get(name, -1) > dispatched_revision:
                return True
        return False

    def snapshot(self) -> StateSnapshot:
        return StateSnapshot(
            session_id=self.session_id,
            revision=self.revision,
            intent=self.intent,
            slots={n: s.value for n, s in self.slots.items()},
            slot_meta={
                n: SlotMeta(
                    confidence=s.confidence,
                    source=s.source,
                    revision=s.revision,
                    evidence=s.evidence,
                )
                for n, s in self.slots.items()
            },
        )
