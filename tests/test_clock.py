"""The clock is load-bearing for the 15% latency block, so it gets its own suite.

If virtual time drifts, every latency number in every trace is wrong and we will
not notice. These tests pin the two properties that matter: time advances only
when the system is idle, and concurrent work overlaps rather than serialises.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from harness.clock import Clock, Deadlock, VirtualTimeLoop, run_virtual


def test_sleeps_are_free() -> None:
    """A 10-second virtual sleep must cost no measurable wall-clock time.

    This is what keeps us inside the 120 s per-scenario cap while still modelling
    realistic tool latency.
    """

    async def main() -> float:
        c = Clock()
        await c.sleep(10_000)
        return c.now

    wall_start = time.perf_counter()
    virtual_elapsed = run_virtual(main())
    wall_elapsed = time.perf_counter() - wall_start

    assert virtual_elapsed == pytest.approx(10_000)
    assert wall_elapsed < 0.5, f"virtual sleep took {wall_elapsed:.3f}s of real time"


def test_concurrent_sleeps_overlap() -> None:
    """Three 100 ms calls in parallel finish at 100 ms, not 300 ms.

    Non-blocking tool execution is the whole premise; if the clock serialised
    them, every latency measurement would flatter a sequential agent.
    """

    async def main() -> tuple[float, list[float]]:
        c = Clock()

        async def work(ms: float) -> float:
            await c.sleep(ms)
            return c.now

        ends = await asyncio.gather(work(100), work(100), work(100))
        return c.now, ends

    end, ends = run_virtual(main())
    assert ends == [100.0, 100.0, 100.0]
    assert end == pytest.approx(100.0)


def test_time_advances_to_the_earliest_deadline() -> None:
    async def main() -> list[float]:
        c = Clock()
        seen: list[float] = []

        async def at(ms: float) -> None:
            await c.sleep(ms)
            seen.append(c.now)

        await asyncio.gather(at(300), at(50), at(120))
        return seen

    assert run_virtual(main()) == [50.0, 120.0, 300.0]


def test_time_does_not_advance_while_work_is_runnable() -> None:
    """Pure computation is instantaneous on the virtual timeline.

    The fast path is rule-based precisely so that it costs zero virtual time;
    this test is the invariant that claim rests on.
    """

    async def main() -> float:
        c = Clock()
        for _ in range(1000):
            await asyncio.sleep(0)
        return c.now

    assert run_virtual(main()) == 0.0


def test_event_ordering_is_deterministic_across_runs() -> None:
    """Same schedule in, same interleaving out — every time.

    Without this, an adversarial-timing regression is indistinguishable from a
    flaky test.
    """

    async def main() -> list[str]:
        c = Clock()
        log: list[str] = []

        async def emit(name: str, ms: float) -> None:
            await c.sleep(ms)
            log.append(f"{name}@{c.now:.0f}")

        await asyncio.gather(
            emit("interrupt", 310),
            emit("tool_result", 300),
            emit("chunk", 300),
            emit("frame", 305),
        )
        return log

    runs = [run_virtual(main()) for _ in range(5)]
    assert all(r == runs[0] for r in runs), f"nondeterministic interleaving: {runs}"
    assert runs[0] == ["tool_result@300", "chunk@300", "frame@305", "interrupt@310"]


def test_sub_millisecond_events_are_not_batched() -> None:
    """Regression: the loop's clock resolution must not coalesce nearby timers.

    asyncio fires every timer within `_clock_resolution` of now in one batch. On
    Windows that is ~15.6 ms, which collapsed a 5 ms-separated interruption into
    the same tick as the event before it — silently turning adversarial timing
    into simultaneous timing. Virtual time is exact, so the resolution is 1 ns.
    """

    async def main() -> list[float]:
        c = Clock()
        seen: list[float] = []

        async def at(ms: float) -> None:
            await c.sleep(ms)
            seen.append(c.now)

        await asyncio.gather(at(300.0), at(300.5), at(300.01), at(315.0))
        return seen

    assert run_virtual(main()) == [300.0, 300.01, 300.5, 315.0]


def test_wait_for_timeouts_are_virtual() -> None:
    """`asyncio.wait_for` must respect virtual time without any adaptation.

    This is the payoff of overriding `loop.time()`: the agent can use ordinary
    asyncio timeout machinery and still run against the real kit unchanged.
    """

    async def main() -> tuple[bool, float]:
        c = Clock()
        try:
            await asyncio.wait_for(c.sleep(500), timeout=0.1)  # 100 ms
            return False, c.now
        except asyncio.TimeoutError:
            return True, c.now

    timed_out, at = run_virtual(main())
    assert timed_out is True
    assert at == pytest.approx(100.0)


def test_cancellation_lands_at_the_next_await() -> None:
    """A cancel is a request, not a fact — the premise of the effect ledger.

    Here the task is cancelled while parked on a sleep, so it never reaches its
    side effect. The opposite case (side effect already committed) is what
    `COMPLETED_NOW_STALE` exists for; see test_kernel.
    """

    async def main() -> tuple[list[str], float]:
        c = Clock()
        effects: list[str] = []

        async def tool() -> None:
            await c.sleep(200)
            effects.append("side-effect")

        task = asyncio.create_task(tool())
        await c.sleep(50)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return effects, c.now

    effects, at = run_virtual(main())
    assert effects == [], "cancel landed before the side effect"
    assert at == pytest.approx(50.0)


def test_deadlock_is_raised_not_hung() -> None:
    """A queue nobody feeds must fail fast rather than burn the 120 s cap."""

    async def main() -> None:
        q: asyncio.Queue[int] = asyncio.Queue()
        await q.get()

    with pytest.raises(Deadlock):
        run_virtual(main())


def test_loop_internals_are_probed() -> None:
    loop = VirtualTimeLoop()
    try:
        loop.probe()  # must not raise on a supported Python
        assert loop.now_ms == 0.0
    finally:
        loop.close()


def test_clock_deadline_context_manager() -> None:
    async def main() -> tuple[bool, float]:
        c = Clock()
        hit = False
        try:
            async with c.deadline(80):
                await c.sleep(400)
        except (asyncio.TimeoutError, TimeoutError):
            hit = True
        return hit, c.now

    hit, at = run_virtual(main())
    assert hit and at == pytest.approx(80.0)
