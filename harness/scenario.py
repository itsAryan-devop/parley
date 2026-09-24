"""Scenario definitions and the runner that replays them.

A scenario is a manifest, an environment profile, a timed event script, and a
set of expectations. Nothing about it is agent-specific: it describes what the
world does, not what the agent should do about it.

The one structural decision worth calling out is that events are delivered by a
**producer task on the virtual clock**, not by a loop that waits for the agent
to finish handling each one. Real inbound queues do not pause while you think.
If they did, an interruption arriving during a 140 ms frame decode would be
recorded as arriving *after* the decode, the cancellation grace period would
silently pass the test, and the whole adversarial-timing story would be
fiction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from parley.protocol.events import parse_event


class Expectation(BaseModel):
    """What a correct run looks like. Every field optional; absent means unchecked."""

    model_config = ConfigDict(extra="forbid")

    intent: str | None = None
    slots: dict[str, Any] | None = None
    """Exact final snapshot values for these slot names."""
    absent_slots: list[str] = Field(default_factory=list)
    """Slots that must NOT be in the final snapshot (e.g. dropped on goal switch)."""

    tools_called: list[str] = Field(default_factory=list)
    """Tools that must have been dispatched at least once."""
    tools_not_called: list[str] = Field(default_factory=list)
    cancelled_tools: list[str] = Field(default_factory=list)
    """Tools with at least one cancelled call."""
    survived_tools: list[str] = Field(default_factory=list)
    """Tools whose call must have completed and still been valid — the
    over-cancellation check."""

    live_effects: list[dict[str, Any]] | None = None
    """Exactly these effects should exist in the world at the end."""
    no_duplicate_effects: bool = True

    must_clarify: bool = False
    must_not_claim_completion: bool = False
    max_first_response_ms: float | None = None

    notes: str = ""


class EnvProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: dict[str, float] = Field(default_factory=dict)
    default_latency_ms: float = 600.0
    commit_fraction: float = 0.7
    faults: list[dict[str, Any]] = Field(default_factory=list)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    modality: str = "text"
    """text | audio | visual — mirrors the suite's 50/30/20 split."""
    description: str = ""
    probes: list[str] = Field(default_factory=list)
    """Which behaviours this scenario is designed to exercise."""

    manifest: list[dict[str, Any]] = Field(default_factory=list)
    env: EnvProfile = Field(default_factory=EnvProfile)
    events: list[dict[str, Any]] = Field(default_factory=list)
    expect: Expectation = Field(default_factory=Expectation)

    @classmethod
    def load(cls, path: str | Path) -> Scenario:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(body)

    def build_events(self) -> list[Any]:
        """Materialise the event script, resolving the `@manifest` placeholder.

        The manifest is written once at the top of the file and referenced from
        the event, so a scenario cannot drift out of sync with itself.
        """
        out = []
        for raw in self.events:
            raw = dict(raw)
            if raw.get("manifest") == "@manifest":
                raw["manifest"] = {"tools": self.manifest}
            out.append(parse_event(raw))
        return sorted(out, key=lambda e: (e.t, e.seq))


def load_all(directory: str | Path = "scenarios") -> list[Scenario]:
    return [Scenario.load(p) for p in sorted(Path(directory).glob("*.json"))]
