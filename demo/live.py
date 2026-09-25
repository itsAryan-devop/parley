"""Run the agent on a real clock, against a live microphone.

Everything else in this repository runs on the virtual clock, where a 1400 ms
tool call costs nothing and the whole 29-scenario suite finishes in a second.
That is the right trade for scoring and replay. It is the wrong trade for a
demonstration: the entire claim of this project is that a correction cancels
*only* the work that depended on it, and nobody can see that happen if the work
begins and ends in the same microsecond.

So this module swaps one object -- the clock -- and nothing else. `LiveClock`
implements the same two-method surface the agent actually uses (`now`, `sleep`),
backed by `perf_counter` and `asyncio.sleep`. The agent, kernel, floor manager,
planner and mock environment are imported unmodified and cannot tell the
difference. That is not a convenience; it is the adapter boundary from
`tests/test_adapter.py` holding for a third time, now across *time itself*.

The event source is the other half. `parley.multimodal.asr.StreamingASR` already
turns PCM into `TranscriptChunk`s, and it does not care whether the PCM came from
a WAV file on disk or a browser's microphone three milliseconds ago. Live speech
is therefore not a new code path -- it is the same code path with a different
producer.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

from harness.mockenv import EnvConfig, Fault, MockEnvironment, World
from harness.trace import RecordKind, Trace, TraceRecord
from parley.agent.agent import ParleyAgent
from parley.agent.model import InterruptionModel
from parley.protocol.events import parse_event
from parley.protocol.manifest import parse_manifest

ROOT = Path(__file__).resolve().parents[1]

DEMO_LATENCY_MS = {
    # Deliberately slower than the scenario suite. A 1400 ms search is realistic
    # and scores fine, but it gives a human roughly one second to get a
    # correction out -- and a demo that depends on the presenter interrupting
    # inside one second is a demo that fails on stage. These numbers are for the
    # camera only; nothing scored uses them.
    "search_flights": 6000.0,
    "search_hotels": 6000.0,
    "book_flight": 2500.0,
    "book_hotel": 2500.0,
    "cancel_booking": 600.0,
    "get_booking_status": 400.0,
    "lookup_manual": 1800.0,
    "diagnose_sound": 1600.0,
    "create_ticket": 2200.0,
}


class LiveClock:
    """Milliseconds since session start, on the real monotonic clock.

    Deliberately the *same* surface as `harness.clock.Clock`. `perf_counter` and
    not `time.time`, because the latter can step backwards when the OS syncs
    NTP, and a clock that goes backwards mid-session would make a completed call
    look like it finished before it started.
    """

    __slots__ = ("_t0",)

    def __init__(self) -> None:
        self._t0 = time.perf_counter()

    @property
    def now(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    async def sleep(self, ms: float) -> None:
        await asyncio.sleep(max(0.0, ms) / 1000.0)

    def deadline(self, ms: float) -> Any:
        return asyncio.timeout(max(0.0, ms) / 1000.0)


class StreamingTrace(Trace):
    """A trace that also tees every record to a live subscriber.

    The browser timeline is not reading a file after the fact -- it is watching
    the same records the scorer would read, as they are written. A cancellation
    appears on screen because `kernel(..., "invalidate")` was emitted, not
    because the UI was separately told to draw one. If the trace is wrong the
    picture is wrong, which is the only honest way to build a demo of a system
    whose whole claim is auditability.
    """

    def __init__(self, on_record: Callable[[TraceRecord], None], **kw: Any) -> None:
        super().__init__(**kw)
        self._on_record = on_record

    def emit(self, t: float, kind: RecordKind, name: str, /, **payload: Any) -> TraceRecord:
        record = super().emit(t, kind, name, **payload)
        try:
            self._on_record(record)
        except Exception:  # noqa: BLE001 - a broken socket must not kill the agent
            pass
        return record


MANIFEST_SOURCES = (
    "S02_slot_correction.json",   # travel: the flagship selective-cancellation case
    "S10_visual_manual.json",     # device support: what a photo or a clip feeds
)


def demo_manifest() -> Any:
    """The union of the travel and device-support manifests.

    Read from the scenario files rather than duplicated here, so the tools the
    demo offers are provably the tools those scenarios score. A copy would drift,
    and a demo running against tools the suite does not exercise is a demo of
    something nobody tested.

    Merging the two is what lets one live session show both halves: correct a
    destination mid-search, then drop in a photo of a router and watch the
    manual lookup come back. Nothing in the agent cares -- the planner derives
    everything from the manifest, so a wider manifest simply means more reachable
    goals. Names are deduplicated with the first occurrence winning; the two
    files overlap on nothing today, and this keeps it honest if they ever do.
    """
    tools: list[Any] = []
    seen: set[str] = set()
    for name in MANIFEST_SOURCES:
        data = json.loads((ROOT / "scenarios" / name).read_text("utf-8"))
        for spec in data["manifest"]:
            if spec["name"] not in seen:
                seen.add(spec["name"])
                tools.append(spec)
    return tools


class LiveSession:
    """One browser connection: a real agent, a real clock, a live queue.

    Mirrors `harness.runner.run_scenario_async` in structure -- producer pushes
    events, agent drains them -- with the producer being a websocket instead of
    a scripted list.
    """

    def __init__(
        self,
        session_id: str,
        *,
        emit: Callable[[dict[str, Any]], None],
        model: InterruptionModel | None = None,
        latency_ms: dict[str, float] | None = None,
        faults: list[Fault] | None = None,
    ) -> None:
        self.session_id = session_id
        self.emit = emit
        self.clock = LiveClock()
        self.world = World()
        self.trace = StreamingTrace(self._on_record, session_id=session_id)

        self.env = MockEnvironment(
            self.clock, self.trace, self.world,
            EnvConfig(
                latency_ms=dict(latency_ms or DEMO_LATENCY_MS),
                default_latency_ms=1500.0,
                commit_fraction=0.7,
                faults=list(faults or []),
            ),
        )

        self.agent = ParleyAgent(
            session_id,
            clock=self.clock,
            trace=self.trace,
            executor=self.env.call,
            manifest=parse_manifest({"tools": demo_manifest()}),
            model=model,
            on_action=self._on_action,
        )

        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    # -- plumbing ----------------------------------------------------------

    def _on_record(self, record: TraceRecord) -> None:
        self.emit({"type": "trace", "record": record.model_dump(mode="json")})

    def _on_action(self, action: Any) -> None:
        self.emit({"type": "action", "action": action.model_dump(mode="json")})

    async def _drain(self):
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"session:{self.session_id}")

    async def _run(self) -> None:
        try:
            await self.agent.run(self._drain())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface it, never die silently
            self.emit({"type": "error", "error": f"{type(exc).__name__}: {exc}"})

    def feed(self, payload: dict[str, Any]) -> None:
        """Push one inbound event, stamping it with the live clock.

        The browser does not get to choose `t`. Timestamps are assigned here so
        the trace's timeline is the session's own, exactly as the harness does
        it -- a client that lied about `t` could otherwise make its own latency
        look better than it was.
        """
        payload = dict(payload)
        payload["t"] = round(self.clock.now, 1)
        self._queue.put_nowait(parse_event(payload))

    async def finish(self, reason: str = "complete") -> Any:
        self._queue.put_nowait(None)
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=30.0)
            except (asyncio.TimeoutError, TimeoutError):
                self._task.cancel()
        return None

    def snapshot(self) -> dict[str, Any]:
        return self.agent.state.snapshot().model_dump(mode="json")
