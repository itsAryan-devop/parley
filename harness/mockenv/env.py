"""Mock tool environment: deterministic latency and fault injection.

Guide §4: "Mock Environment: Deterministic latency and fault injection for flight
search, booking, ticket creation, and frame-grounded manual lookup."

The design decision that matters here is the **commit point**. A naive mock
sleeps for the whole latency and then applies its side effect, which means a
cancel at any time before completion is always clean — and an agent that never
handles `COMPLETED_NOW_STALE` would pass every test while being broken in exactly
the way the real world breaks it.

So a mutating tool commits partway through its latency window:

    |<--- commit_fraction --->|                        |
    dispatch ............ COMMIT ................. return
             cancel here = clean    cancel here = the effect already happened

A cancel landing after the commit point produces a real, live effect in `World`
that the agent's snapshot does not know about. That is the situation the effect
ledger exists for, and it is reachable in this harness by moving one number.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from ..clock import Clock
from ..trace import Trace
from .world import MANUAL_PAGES, World, flight_catalogue, hotel_catalogue


class FaultKind(str, Enum):
    TRANSIENT = "transient"
    """Fails once with a retryable error; a retry succeeds."""
    PERMANENT = "permanent"
    """Always fails."""
    TIMEOUT = "timeout"
    """Never returns. The agent must not wait forever."""
    SLOW = "slow"
    """Returns correctly, but far later than the latency profile promises."""


class Fault(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    kind: FaultKind = FaultKind.TRANSIENT
    on_call: int = 1
    """1-based index of the invocation of `tool` that this fault applies to."""
    message: str = "upstream error"
    delay_ms: float = 8000.0
    """Used by SLOW and TIMEOUT."""


class EnvConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: dict[str, float] = Field(default_factory=dict)
    default_latency_ms: float = 600.0
    commit_fraction: float = 0.7
    """Where in its latency window a mutating tool becomes irreversible."""
    faults: list[Fault] = Field(default_factory=list)


class ToolError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class MockEnvironment:
    """Executes tool calls against `World` on the virtual clock."""

    def __init__(
        self,
        clock: Clock,
        trace: Trace,
        world: World | None = None,
        config: EnvConfig | None = None,
    ) -> None:
        self.clock = clock
        self.trace = trace
        self.world = world or World()
        self.config = config or EnvConfig()
        self._invocations: dict[str, int] = {}
        self._handlers: dict[str, Callable[..., Any]] = {
            "search_flights": self._search_flights,
            "book_flight": self._book_flight,
            "cancel_booking": self._cancel_booking,
            "search_hotels": self._search_hotels,
            "book_hotel": self._book_hotel,
            "create_ticket": self._create_ticket,
            "lookup_manual": self._lookup_manual,
            "identify_frame": self._identify_frame,
            "get_booking_status": self._get_booking_status,
        }

    # -- public surface ----------------------------------------------------

    @property
    def known_tools(self) -> set[str]:
        return set(self._handlers)

    async def call(self, tool: str, args: dict[str, Any], call_id: str, *, mutating: bool) -> Any:
        """Run one tool call. Raises `ToolError` on injected faults.

        Cancellation propagates as `asyncio.CancelledError` from whichever
        `sleep` the call is parked on — which is exactly how a real awaitable
        behaves, and why the commit point matters.
        """
        n = self._invocations[tool] = self._invocations.get(tool, 0) + 1
        latency = self.config.latency_ms.get(tool, self.config.default_latency_ms)
        fault = self._fault_for(tool, n)

        self.trace.tool(
            self.clock.now,
            "dispatch",
            call_id=call_id,
            tool=tool,
            args=args,
            invocation=n,
            latency_ms=latency,
            mutating=mutating,
            fault=fault.kind.value if fault else None,
        )

        if fault and fault.kind is FaultKind.TIMEOUT:
            await self.clock.sleep(fault.delay_ms)
            raise ToolError(f"{tool}: no response", retryable=True)

        if fault and fault.kind is FaultKind.SLOW:
            latency = fault.delay_ms

        handler = self._handlers.get(tool)
        if handler is None:
            # An unseen tool is a legitimate manifest entry we have no mock for.
            # Fail informatively rather than pretending it worked.
            await self.clock.sleep(latency)
            raise ToolError(f"{tool}: not implemented by the mock environment")

        commit_at = latency * self.config.commit_fraction if mutating else latency

        await self.clock.sleep(commit_at)

        if fault and fault.kind in (FaultKind.TRANSIENT, FaultKind.PERMANENT):
            self.trace.tool(self.clock.now, "fault", call_id=call_id, tool=tool, kind=fault.kind.value)
            raise ToolError(f"{tool}: {fault.message}", retryable=fault.kind is FaultKind.TRANSIENT)

        result = handler(args, call_id)

        if mutating:
            # Past this line the effect exists in the world whether or not the
            # caller is still interested. Remaining latency is uncancellable in
            # the sense that matters: cancelling now leaves a live effect.
            self.trace.tool(
                self.clock.now, "commit", call_id=call_id, tool=tool, args=args,
            )
            await asyncio.shield(self.clock.sleep(latency - commit_at))

        self.trace.tool(self.clock.now, "return", call_id=call_id, tool=tool)
        return result

    # -- faults ------------------------------------------------------------

    def _fault_for(self, tool: str, invocation: int) -> Fault | None:
        for f in self.config.faults:
            if f.tool == tool and f.on_call == invocation:
                return f
        return None

    # -- handlers ----------------------------------------------------------
    # Read-only handlers never touch `World`. Mutating handlers always do, and
    # they are the only things that do.

    def _search_flights(self, args: dict[str, Any], call_id: str) -> Any:
        results = flight_catalogue(
            args.get("origin", "DEL"), args.get("destination", ""), args.get("date", "")
        )
        if args.get("time_of_day") == "morning":
            results = [f for f in results if int(f["depart"][:2]) < 12]
        return {"flights": results, "count": len(results)}

    def _search_hotels(self, args: dict[str, Any], call_id: str) -> Any:
        hotels = hotel_catalogue(args.get("city", ""), args.get("date", ""))
        return {"hotels": hotels, "count": len(hotels)}

    def _book_flight(self, args: dict[str, Any], call_id: str) -> Any:
        eff = self.world.commit("book_flight", args, call_id, self.clock.now)
        return {"pnr": f"PNR{eff.effect_id[-4:].upper()}", "status": "confirmed", **args}

    def _book_hotel(self, args: dict[str, Any], call_id: str) -> Any:
        eff = self.world.commit("book_hotel", args, call_id, self.clock.now)
        return {"confirmation": f"HTL{eff.effect_id[-4:].upper()}", "status": "confirmed", **args}

    def _cancel_booking(self, args: dict[str, Any], call_id: str) -> Any:
        target = dict(args)
        target.pop("pnr", None)
        undo = self.world.compensate("book_flight", target, call_id, self.clock.now)
        return {"status": "cancelled" if undo else "nothing_to_cancel"}

    def _create_ticket(self, args: dict[str, Any], call_id: str) -> Any:
        eff = self.world.commit("create_ticket", args, call_id, self.clock.now)
        return {"ticket_id": f"TCK-{eff.effect_id[-4:].upper()}", "status": "open", **args}

    def _lookup_manual(self, args: dict[str, Any], call_id: str) -> Any:
        label = args.get("label") or args.get("symptom") or ""
        page = MANUAL_PAGES.get(label)
        if page is None:
            return {"found": False, "label": label, "candidates": sorted(MANUAL_PAGES)}
        return {"found": True, **page}

    def _identify_frame(self, args: dict[str, Any], call_id: str) -> Any:
        """Vision stub. The *scenario* supplies the ground-truth labels.

        Perception is deliberately allowed to be ambiguous: a frame may return
        two candidates within a narrow margin, which is what forces the agent to
        clarify rather than guess (objective 5).
        """
        return {"frame_id": args.get("frame_id"), "candidates": args.get("_candidates", [])}

    def _get_booking_status(self, args: dict[str, Any], call_id: str) -> Any:
        live = [e for e in self.world.live_effects() if e.tool == "book_flight"]
        return {"bookings": [{"call_id": e.call_id, **e.args} for e in live], "count": len(live)}
