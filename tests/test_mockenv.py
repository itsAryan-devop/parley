"""The mock environment is ground truth, so its guarantees get pinned.

The commit-point behaviour is the important one: if a late cancel could never
leave a live effect, then `COMPLETED_NOW_STALE` would be unreachable and an agent
that mishandles it would pass every test we wrote.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.clock import Clock, run_virtual
from harness.mockenv import EnvConfig, Fault, FaultKind, MockEnvironment, ToolError, World
from harness.mockenv.world import flight_catalogue
from harness.trace import Trace


def _env(**cfg) -> tuple[MockEnvironment, Trace]:
    trace = Trace(session_id="t")
    return MockEnvironment(Clock(), trace, World(), EnvConfig(**cfg)), trace


# ------------------------------------------------------------------ determinism

def test_catalogue_is_stable_across_processes() -> None:
    """Uses sha256, not `hash()`, which is salted per process."""
    a = flight_catalogue("DEL", "BOM", "2026-10-01")
    b = flight_catalogue("DEL", "BOM", "2026-10-01")
    assert a == b
    assert a != flight_catalogue("DEL", "BLR", "2026-10-01")


def test_catalogue_always_offers_morning_and_afternoon() -> None:
    """A `REFINEMENT` must always have something to filter down to."""
    flights = flight_catalogue("DEL", "BOM", "2026-10-01")
    hours = [int(f["depart"][:2]) for f in flights]
    assert any(h < 12 for h in hours) and any(h >= 12 for h in hours)


def test_search_respects_a_morning_refinement() -> None:
    async def main():
        env, _ = _env(latency_ms={"search_flights": 100})
        wide = await env.call("search_flights", {"destination": "BOM", "date": "d"}, "c1", mutating=False)
        narrow = await env.call(
            "search_flights", {"destination": "BOM", "date": "d", "time_of_day": "morning"}, "c2", mutating=False
        )
        return wide["count"], narrow["count"]

    wide, narrow = run_virtual(main())
    assert 0 < narrow < wide


# ------------------------------------------------------------------ latency

def test_latency_is_honoured_on_the_virtual_clock() -> None:
    async def main():
        c = Clock()
        env, _ = _env(latency_ms={"search_flights": 750})
        await env.call("search_flights", {"destination": "BOM"}, "c1", mutating=False)
        return c.now

    assert run_virtual(main()) == pytest.approx(750.0)


def test_calls_run_concurrently() -> None:
    """Non-blocking dispatch is the premise; two 600 ms calls must cost 600 ms."""

    async def main():
        c = Clock()
        env, _ = _env(latency_ms={"search_flights": 600, "search_hotels": 600})
        await asyncio.gather(
            env.call("search_flights", {"destination": "BOM"}, "c1", mutating=False),
            env.call("search_hotels", {"city": "BOM"}, "c2", mutating=False),
        )
        return c.now

    assert run_virtual(main()) == pytest.approx(600.0)


# ------------------------------------------------------------------ commit point

def test_cancel_before_the_commit_point_leaves_no_effect() -> None:
    async def main():
        c = Clock()
        env, _ = _env(latency_ms={"book_flight": 1000}, commit_fraction=0.7)  # commits at 700 ms
        task = asyncio.create_task(env.call("book_flight", {"flight_no": "AI101"}, "c1", mutating=True))
        await c.sleep(300)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return env.world.live_effects()

    assert run_virtual(main()) == []


def test_cancel_after_the_commit_point_leaves_a_live_effect() -> None:
    """The whole reason `COMPLETED_NOW_STALE` exists.

    The cancel is accepted, the caller stops waiting — and the booking is real.
    An agent that treats cancellation as proof of no side effect diverges from
    the environment here, silently.
    """

    async def main():
        c = Clock()
        env, _ = _env(latency_ms={"book_flight": 1000}, commit_fraction=0.7)
        task = asyncio.create_task(env.call("book_flight", {"flight_no": "AI101"}, "c1", mutating=True))
        await c.sleep(800)  # past the 700 ms commit
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return env.world.live_effects()

    live = run_virtual(main())
    assert len(live) == 1
    assert live[0].tool == "book_flight"


def test_read_only_tools_never_touch_the_world() -> None:
    async def main():
        env, _ = _env(latency_ms={"search_flights": 50})
        await env.call("search_flights", {"destination": "BOM"}, "c1", mutating=False)
        return env.world.effects

    assert run_virtual(main()) == []


# ------------------------------------------------------------------ duplicates

def test_duplicate_detection_ignores_call_id() -> None:
    """Two bookings of the same flight are a double-booking however they are labelled."""

    async def main():
        env, _ = _env(latency_ms={"book_flight": 10})
        await env.call("book_flight", {"flight_no": "AI101"}, "c1", mutating=True)
        await env.call("book_flight", {"flight_no": "AI101"}, "c2", mutating=True)
        return env.world.duplicates()

    dupes = run_virtual(main())
    assert len(dupes) == 1
    assert len(next(iter(dupes.values()))) == 2


def test_different_arguments_are_not_duplicates() -> None:
    async def main():
        env, _ = _env(latency_ms={"book_flight": 10})
        await env.call("book_flight", {"flight_no": "AI101"}, "c1", mutating=True)
        await env.call("book_flight", {"flight_no": "6E202"}, "c2", mutating=True)
        return env.world.duplicates()

    assert run_virtual(main()) == {}


def test_compensation_clears_the_effect() -> None:
    async def main():
        env, _ = _env(latency_ms={"book_flight": 10, "cancel_booking": 10})
        await env.call("book_flight", {"flight_no": "AI101"}, "c1", mutating=True)
        await env.call("cancel_booking", {"flight_no": "AI101"}, "c2", mutating=True)
        return env.world.live_effects(), env.world.duplicates()

    live, dupes = run_virtual(main())
    assert live == [] and dupes == {}


def test_compensating_nothing_is_reported_not_swallowed() -> None:
    async def main():
        env, _ = _env(latency_ms={"cancel_booking": 10})
        return await env.call("cancel_booking", {"flight_no": "AI999"}, "c1", mutating=True)

    assert run_virtual(main())["status"] == "nothing_to_cancel"


# ------------------------------------------------------------------ faults

def test_transient_fault_hits_once_then_succeeds() -> None:
    async def main():
        env, _ = _env(
            latency_ms={"search_flights": 50},
            faults=[Fault(tool="search_flights", kind=FaultKind.TRANSIENT, on_call=1)],
        )
        first: str | None = None
        try:
            await env.call("search_flights", {"destination": "BOM"}, "c1", mutating=False)
        except ToolError as e:
            first = str(e)
        second = await env.call("search_flights", {"destination": "BOM"}, "c2", mutating=False)
        return first, second["count"]

    first, count = run_virtual(main())
    assert first is not None and count > 0


def test_a_faulting_mutating_call_commits_nothing() -> None:
    async def main():
        env, _ = _env(
            latency_ms={"book_flight": 100},
            faults=[Fault(tool="book_flight", kind=FaultKind.PERMANENT, on_call=1)],
        )
        with pytest.raises(ToolError):
            await env.call("book_flight", {"flight_no": "AI101"}, "c1", mutating=True)
        return env.world.live_effects()

    assert run_virtual(main()) == []


def test_timeout_fault_is_survivable_with_wait_for() -> None:
    """An agent must be able to bound a hung tool without hanging the scenario."""

    async def main():
        c = Clock()
        env, _ = _env(faults=[Fault(tool="search_flights", kind=FaultKind.TIMEOUT, delay_ms=30_000)])
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                env.call("search_flights", {"destination": "BOM"}, "c1", mutating=False),
                timeout=2.0,  # 2000 ms virtual
            )
        return c.now

    assert run_virtual(main()) == pytest.approx(2000.0)


def test_unknown_tool_fails_informatively() -> None:
    async def main():
        env, _ = _env()
        with pytest.raises(ToolError, match="not implemented"):
            await env.call("summon_a_unicorn", {}, "c1", mutating=False)

    run_virtual(main())


# ------------------------------------------------------------------ tracing

def test_every_call_leaves_dispatch_and_return_records() -> None:
    async def main():
        env, trace = _env(latency_ms={"book_flight": 100})
        await env.call("book_flight", {"flight_no": "AI101"}, "c1", mutating=True)
        return [r.name for r in trace.of_kind(trace.records[0].kind)]

    names = run_virtual(main())
    assert names == ["dispatch", "commit", "return"]
