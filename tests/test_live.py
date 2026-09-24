"""The live demo runs the *same* agent, on a real clock, and still selects.

`demo/live.py` exists to be shown to people, which is exactly the kind of code
that rots: nothing scored imports it, so nothing scored notices when it breaks.
These tests make the demo's central claim falsifiable.

The claim is not "the demo starts". It is that swapping the virtual clock for a
wall clock changes *nothing about the decisions* -- that `S02_slot_correction`,
the scenario the whole project is built around, still cancels exactly the flight
search and still spares the hotel search when the milliseconds are real. If that
ever stops being true, the thing being demonstrated on stage is not the thing
being scored, and this suite should fail loudly rather than let anyone find out
in front of a jury.

Latencies here are deliberately small. These tests spend real wall-clock time --
the only ones in the suite that do -- so they buy their evidence as cheaply as
they can.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from demo.live import LiveClock, LiveSession
from parley.agent.model import InterruptionModel

FAST = {
    "search_flights": 500.0,
    "search_hotels": 500.0,
    "book_flight": 300.0,
    "cancel_booking": 100.0,
    "get_booking_status": 60.0,
}


# ------------------------------------------------------------------ the clock


def test_live_clock_is_milliseconds_from_zero():
    clock = LiveClock()
    assert clock.now < 50.0, "a fresh clock starts near zero, not at the epoch"


def test_live_clock_never_goes_backwards():
    """A clock that steps back would make a call settle before it dispatched.

    `perf_counter` rather than `time.time` is the whole reason this holds: the
    latter can jump backwards when the OS corrects itself against NTP, and a
    demo that ran during a resync would emit a trace the scorer would reject.
    """
    clock = LiveClock()
    samples = [clock.now for _ in range(2000)]
    assert samples == sorted(samples)


def test_live_clock_measures_real_elapsed_time():
    clock = LiveClock()
    time.sleep(0.05)
    assert 40.0 <= clock.now <= 250.0


async def test_live_clock_sleep_actually_sleeps():
    clock = LiveClock()
    before = clock.now
    await clock.sleep(60.0)
    assert clock.now - before >= 50.0


def test_live_clock_offers_what_the_agent_uses():
    """The agent only ever touches `now` and `sleep`; the runner adds `deadline`.

    Asserted rather than assumed, because the day someone adds a `clock.call_at`
    to the agent, the demo breaks at runtime in front of an audience instead of
    here.
    """
    clock = LiveClock()
    for attr in ("now", "sleep", "deadline"):
        assert hasattr(clock, attr)


# ------------------------------------------------------------------ the thesis


@pytest.fixture(scope="module")
def model():
    return InterruptionModel.load_default()


async def _run(model, turns: list[tuple[float, str]], settle: float = 1.2):
    """Feed turns at the given offsets in seconds, then let the session drain."""
    actions: list[dict] = []
    session = LiveSession(
        "test", emit=lambda p: actions.append(p), model=model, latency_ms=FAST
    )
    session.start()
    session.feed({"type": "session_start", "session_id": "test"})

    last = 0.0
    for at, text in turns:
        await asyncio.sleep(max(0.0, at - last))
        last = at
        session.feed({"type": "transcript_chunk", "text": text, "end_of_turn": True})

    await asyncio.sleep(settle)
    session.feed({"type": "session_end", "reason": "test over"})
    await session.finish()
    return session, [a["action"] for a in actions if a["type"] == "action"]


async def test_correction_cancels_only_the_dependent_call_on_a_real_clock(model):
    """The flagship claim, with the milliseconds real rather than virtual."""
    session, actions = await _run(model, [
        (0.00, "find me a flight to Delhi on Tuesday"),
        (0.10, "and a hotel in Goa"),
        (0.25, "no wait, Mumbai"),
    ])

    dispatched = {a["call_id"]: a["tool"] for a in actions if a["type"] == "tool_call"}
    cancelled = {dispatched[a["call_id"]] for a in actions if a["type"] == "cancel"}

    assert "search_flights" in cancelled, "the call that read `destination` must die"
    assert "search_hotels" not in cancelled, (
        "the hotel search never read `destination` and must survive -- "
        "cancelling it is the over-cancellation this project exists to avoid"
    )


async def test_the_surviving_call_still_reaches_the_final_answer(model):
    """Surviving is not enough; the spared work must still be *used*.

    A kernel could pass the test above by declining to cancel and then quietly
    discarding the result anyway. The user-visible proof is that the hotel search
    reaches the answer.
    """
    session, actions = await _run(model, [
        (0.00, "find me a flight to Delhi on Tuesday"),
        (0.10, "and a hotel in Goa"),
        (0.25, "no wait, Mumbai"),
    ])

    finals = [a for a in actions if a["type"] == "final_response"]
    assert finals, "the session produced no final response"
    assert "hotel" in finals[-1]["text"].lower()


async def test_corrected_slot_is_the_one_that_survives_in_state(model):
    session, _ = await _run(model, [
        (0.00, "find me a flight to Delhi on Tuesday"),
        (0.20, "no wait, Mumbai"),
    ])
    # `slots` is the flat name -> value mapping the guide asks for; provenance
    # rides alongside in `slot_meta` rather than complicating the scored shape.
    assert session.snapshot()["slots"]["destination"] == "BOM"


async def test_timestamps_are_assigned_by_the_server_not_the_client(model):
    """A client must not be able to backdate its own turn.

    `LiveSession.feed` overwrites `t` unconditionally. Without that, a browser
    could stamp every chunk with t=0 and make the agent's response latency --
    15% of the score -- look arbitrarily good.
    """
    actions: list[dict] = []
    session = LiveSession("t", emit=lambda p: actions.append(p), model=model, latency_ms=FAST)
    session.start()
    session.feed({"type": "session_start", "session_id": "t"})
    await asyncio.sleep(0.08)
    session.feed({
        "type": "transcript_chunk", "text": "flight to Delhi",
        "end_of_turn": True, "t": 0.0,       # the lie
    })
    await asyncio.sleep(0.4)
    await session.finish()

    chunks = [r for r in session.trace.records if r.name == "transcript_chunk"]
    assert chunks, "the chunk never reached the trace"
    assert chunks[0].t > 50.0, "the client's t=0 was trusted"
