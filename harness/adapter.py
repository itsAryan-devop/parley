"""Mapping a foreign event stream onto ours.

The official evaluation kit was never released publicly, so the single largest
risk in this project is that its wire format differs from the one we built
against. The README claims that swapping harnesses is "a boundary change, not a
rewrite". This module is that claim made checkable.

The agent imports nothing from `harness`. It consumes objects with a `.type`
and a `.t`, and it emits actions through a callback. So adapting to a different
kit means writing two functions — one translating their events into ours, one
translating our actions into theirs — and nothing inside `parley/` moves.

`FieldMap` covers the boring 90%: different key names, timestamps in seconds
instead of milliseconds, a `final`/`is_final` flag instead of `end_of_turn`,
tool results nested under a `data` envelope. Anything genuinely structural gets
a hand-written function, and `translate_stream` takes either.

This is deliberately small. Its value is not the code; it is that the boundary
has been exercised with a stream shaped nothing like ours, and the agent came
through unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from parley.protocol.events import parse_event


class AdapterError(ValueError):
    """A foreign event that cannot be mapped. Raised loudly rather than dropped:
    an event silently discarded at the boundary is indistinguishable from an
    agent that ignored it."""


@dataclass
class FieldMap:
    """Declarative translation for a foreign event schema.

    Every field defaults to our own naming, so a kit that already agrees on
    something needs no entry for it.
    """

    type_key: str = "type"
    """Where the foreign event keeps its discriminator."""
    time_key: str = "t"
    time_scale: float = 1.0
    """Multiplier onto milliseconds. Use 1000.0 for a kit working in seconds."""

    type_names: dict[str, str] = field(default_factory=dict)
    """foreign discriminator -> ours ('user_text' -> 'transcript_chunk')."""

    field_names: dict[str, dict[str, str]] = field(default_factory=dict)
    """our event type -> {foreign key: our key}."""

    unwrap: str | None = None
    """Key holding a payload envelope to merge up, e.g. 'data' or 'payload'."""

    defaults: dict[str, dict[str, Any]] = field(default_factory=dict)
    """our event type -> fields to supply when the foreign event omits them."""

    drop: set[str] = field(default_factory=set)
    """Foreign discriminators to ignore deliberately — heartbeats and the like.
    Explicit, so that ignoring something is a decision rather than an accident."""

    def __call__(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        foreign_type = raw.get(self.type_key)
        if foreign_type in self.drop:
            return None

        our_type = self.type_names.get(foreign_type, foreign_type)
        if our_type is None:
            raise AdapterError(f"no mapping for event type {foreign_type!r}")

        body = dict(raw)
        if self.unwrap and isinstance(body.get(self.unwrap), dict):
            envelope = body.pop(self.unwrap)
            body = {**envelope, **body}

        body.pop(self.type_key, None)
        out: dict[str, Any] = {"type": our_type}

        renames = self.field_names.get(our_type, {})
        for key, value in body.items():
            if key == self.time_key:
                continue
            out[renames.get(key, key)] = value

        raw_t = raw.get(self.time_key, body.get(self.time_key, 0.0))
        out["t"] = float(raw_t) * self.time_scale

        for key, value in self.defaults.get(our_type, {}).items():
            out.setdefault(key, value)

        return out


def translate_stream(
    events: Iterable[dict[str, Any]],
    mapper: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> list[Any]:
    """Translate and parse a foreign event stream into ours.

    Ordering is re-established on the translated timestamps, because a kit that
    reports time differently may also deliver in a different order.
    """
    out = []
    for seq, raw in enumerate(events):
        mapped = mapper(raw)
        if mapped is None:
            continue
        mapped.setdefault("seq", seq)
        out.append(parse_event(mapped))
    return sorted(out, key=lambda e: (e.t, e.seq))


def actions_to(
    payloads: Iterable[Any], mapper: Callable[[dict[str, Any]], dict[str, Any]]
) -> list[dict[str, Any]]:
    """Translate our emitted actions into a foreign kit's expected shape."""
    out = []
    for action in payloads:
        body = action.model_dump(mode="json") if hasattr(action, "model_dump") else dict(action)
        out.append(mapper(body))
    return out
