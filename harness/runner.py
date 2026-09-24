"""Replay a scenario against the agent and capture everything.

The producer/consumer split here is the point. Events are pushed onto a queue by
a task running on the virtual clock, and the agent drains that queue. The two
run concurrently, which is what makes the trace's timestamps mean what they say:
an interruption scheduled for t=310 ms is *enqueued* at 310 ms whether or not
the agent is mid-decode, so "time to first response after the interruption"
measures the agent rather than the harness.

A hard wall-clock guard mirrors the guide's 120 s per-scenario cap. It is
enforced on virtual time as well, because a livelocked agent would otherwise
spin the virtual clock forever without ever sleeping in reality.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from parley.agent.agent import ParleyAgent
from parley.agent.model import InterruptionModel
from parley.protocol.manifest import parse_manifest

from .clock import Clock, run_virtual
from .mockenv import EnvConfig, Fault, MockEnvironment, World
from .scenario import Scenario
from .trace import Trace

VIRTUAL_CAP_MS = 120_000.0
"""The guide's 120 s per-scenario cap, applied to the virtual timeline."""


@dataclass
class RunResult:
    scenario: Scenario
    trace: Trace
    world: World
    agent: ParleyAgent
    actions: list[Any] = field(default_factory=list)
    final: Any = None
    error: str | None = None

    @property
    def snapshot(self) -> Any:
        return self.agent.state.snapshot()


async def _produce(events: list[Any], queue: asyncio.Queue, clock: Clock) -> None:
    """Enqueue each event at its scheduled virtual time, regardless of the agent."""
    for event in events:
        delay = event.t - clock.now
        if delay > 0:
            await clock.sleep(delay)
        queue.put_nowait(event)
    queue.put_nowait(None)  # sentinel


async def _drain(queue: asyncio.Queue):
    while True:
        event = await queue.get()
        if event is None:
            return
        yield event


async def run_scenario_async(
    scenario: Scenario,
    *,
    trace_path: str | Path | None = None,
    model: InterruptionModel | None = None,
) -> RunResult:
    clock = Clock()
    trace = Trace(trace_path, session_id=scenario.id)
    world = World()

    env = MockEnvironment(
        clock, trace, world,
        EnvConfig(
            latency_ms=scenario.env.latency_ms,
            default_latency_ms=scenario.env.default_latency_ms,
            commit_fraction=scenario.env.commit_fraction,
            faults=[Fault(**f) for f in scenario.env.faults],
        ),
    )

    actions: list[Any] = []
    agent = ParleyAgent(
        scenario.id,
        clock=clock,
        trace=trace,
        executor=env.call,
        manifest=parse_manifest({"tools": scenario.manifest}) if scenario.manifest else None,
        model=model,
        on_action=actions.append,
    )

    result = RunResult(scenario=scenario, trace=trace, world=world, agent=agent, actions=actions)
    queue: asyncio.Queue = asyncio.Queue()
    events = scenario.build_events()

    producer = asyncio.create_task(_produce(events, queue, clock), name="producer")
    try:
        async with clock.deadline(VIRTUAL_CAP_MS):
            await agent.run(_drain(queue))
    except (asyncio.TimeoutError, TimeoutError):
        result.error = f"exceeded the {VIRTUAL_CAP_MS / 1000:.0f}s virtual cap"
        trace.kernel(clock.now, "scenario_timeout", cap_ms=VIRTUAL_CAP_MS)
    except Exception as exc:  # noqa: BLE001 - a crash is a result, not a stack trace
        result.error = f"{type(exc).__name__}: {exc}"
        trace.kernel(clock.now, "scenario_error", error=result.error)
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)

    if not agent._finalised and result.error is None:
        result.final = await agent.finalise("stream ended")
    else:
        finals = [a for a in actions if getattr(a, "type", None) and a.type.value == "final_response"]
        result.final = finals[-1] if finals else None

    trace.close()
    return result


def run_scenario(
    scenario: Scenario,
    *,
    trace_path: str | Path | None = None,
    model: InterruptionModel | None = None,
) -> RunResult:
    """Synchronous entry point. One fresh virtual loop per scenario."""
    return run_virtual(run_scenario_async(scenario, trace_path=trace_path, model=model))
