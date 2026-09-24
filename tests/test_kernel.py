"""Coordination kernel — the 35% block, tested hardest.

Structure mirrors the four guarantees in `dispatcher.py`:
  * every call settles and is traced, including cancelled ones
  * state changes are claimed before dispatch, never after
  * confirming an in-flight call joins it instead of re-issuing it
  * cancelling a mutating call is resolved, not assumed clean
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from harness.clock import Clock, run_virtual
from harness.mockenv import EnvConfig, Fault, FaultKind, MockEnvironment, World
from harness.trace import RecordKind, Trace
from parley.kernel import (
    CallOutcome,
    Dispatcher,
    InterruptionKind,
    SpeculationRefused,
    policy_for,
)
from parley.protocol import SessionState, parse_manifest

MANIFEST = parse_manifest(
    [
        {
            "name": "search_flights",
            "read_only": True,
            "intent": "book_flight",
            "params": [{"name": "origin"}, {"name": "destination"}, {"name": "date"}, {"name": "time_of_day"}],
        },
        {
            "name": "book_flight",
            "mutating": True,
            "intent": "book_flight",
            "params": [{"name": "flight_no"}],
        },
        {
            "name": "cancel_booking",
            "mutating": True,
            "inverse_of": "book_flight",
            "params": [{"name": "flight_no"}],
        },
        {
            "name": "get_booking_status",
            "read_only": True,
            "verifies": "book_flight",
        },
        {
            "name": "search_hotels",
            "read_only": True,
            "intent": "book_hotel",
            "params": [{"name": "city"}, {"name": "date"}],
        },
    ]
)


@dataclass
class Kit:
    clock: Clock
    trace: Trace
    state: SessionState
    env: MockEnvironment
    kernel: Dispatcher


def build(**env_cfg) -> Kit:
    """Assemble a kernel over the mock environment. Call inside a virtual loop."""
    clock = Clock()
    trace = Trace(session_id="test")
    state = SessionState(session_id="test")
    env = MockEnvironment(clock, trace, World(), EnvConfig(**env_cfg))
    kernel = Dispatcher(
        clock=clock, trace=trace, state=state, manifest=MANIFEST, executor=env.call
    )
    return Kit(clock, trace, state, env, kernel)


async def settle(*records) -> None:
    await asyncio.gather(*(r.task for r in records if r.task), return_exceptions=True)


# ============================================================ non-blocking dispatch

def test_dispatch_returns_without_waiting() -> None:
    """The fast path must never be behind a tool call."""

    async def main():
        k = build(latency_ms={"search_flights": 900})
        before = k.clock.now
        rec = await k.kernel.dispatch("search_flights", {"destination": "BOM"}, ["destination"])
        after = k.clock.now
        assert rec.in_flight
        await settle(rec)
        return before, after, k.clock.now

    before, after, end = run_virtual(main())
    assert before == after == 0.0, "dispatch consumed virtual time"
    assert end == pytest.approx(900.0)


def test_many_calls_overlap() -> None:
    async def main():
        k = build(latency_ms={"search_flights": 600, "search_hotels": 600})
        a = await k.kernel.dispatch("search_flights", {"destination": "BOM"}, ["destination"])
        b = await k.kernel.dispatch("search_hotels", {"city": "BOM"}, ["city"])
        await settle(a, b)
        return k.clock.now

    assert run_virtual(main()) == pytest.approx(600.0)


# ============================================================ selective cancellation

def test_a_slot_correction_cancels_only_that_slots_readers() -> None:
    """The central claim of the design.

    Two calls in flight. One read `destination`, one read `city`. The user
    corrects the destination. A framework that flushes the pipeline kills both
    and loses the hotel search for nothing.
    """

    async def main():
        k = build(latency_ms={"search_flights": 900, "search_hotels": 900})
        k.state.set_slot("destination", "Delhi")
        k.state.set_slot("city", "Goa")

        flights = await k.kernel.dispatch("search_flights", {"destination": "Delhi"}, ["destination"])
        hotels = await k.kernel.dispatch("search_hotels", {"city": "Goa"}, ["city"])

        await k.clock.sleep(300)
        delta = k.state.patch_slot("destination", "Mumbai")
        cancelled = await k.kernel.apply_work_policy(
            policy_for(InterruptionKind.SLOT_CORRECTION), delta.invalidating_slots
        )

        await settle(flights, hotels)
        return (
            [(c.call_id, c.tool) for c in cancelled],
            (flights.call_id, flights.outcome),
            (hotels.call_id, hotels.outcome),
        )

    cancelled, (flights_id, flights_outcome), (hotels_id, hotels_outcome) = run_virtual(main())
    assert cancelled == [(flights_id, "search_flights")], "exactly the flight search, and only it"
    assert flights_outcome is CallOutcome.CANCELLED_BEFORE_EFFECT
    assert hotels_outcome is CallOutcome.COMPLETED_STILL_VALID, "the hotel search was never stale"
    assert hotels_id not in [c[0] for c in cancelled]


def test_a_call_reading_several_slots_dies_if_any_changes() -> None:
    async def main():
        k = build(latency_ms={"search_flights": 900})
        k.state.set_slot("destination", "Delhi")
        k.state.set_slot("date", "Tue")
        rec = await k.kernel.dispatch(
            "search_flights", {"destination": "Delhi", "date": "Tue"}, ["destination", "date"]
        )
        await k.clock.sleep(100)
        delta = k.state.patch_slot("date", "Wed")
        await k.kernel.apply_work_policy(policy_for(InterruptionKind.SLOT_CORRECTION), delta.invalidating_slots)
        await settle(rec)
        return rec.outcome, rec.invalidated_by

    outcome, by = run_virtual(main())
    assert outcome is CallOutcome.CANCELLED_BEFORE_EFFECT
    assert by == ["date"]


def test_binding_a_new_slot_cancels_nothing() -> None:
    """Adding information is not correcting it. No in-flight call read a slot
    that did not exist, so nothing can be invalidated."""

    async def main():
        k = build(latency_ms={"search_flights": 500})
        k.state.set_slot("destination", "Delhi")
        rec = await k.kernel.dispatch("search_flights", {"destination": "Delhi"}, ["destination"])
        delta = k.state.set_slot("party_size", 2)
        cancelled = await k.kernel.apply_work_policy(
            policy_for(InterruptionKind.SLOT_CORRECTION), delta.invalidating_slots
        )
        await settle(rec)
        return cancelled, rec.outcome

    cancelled, outcome = run_virtual(main())
    assert cancelled == []
    assert outcome is CallOutcome.COMPLETED_STILL_VALID


def test_barge_in_yields_the_floor_but_keeps_every_call() -> None:
    """The cell every surveyed framework gets wrong.

    The user talking over a filler wants the floor, not a different outcome.
    Cancelling here throws away valid work and guarantees a re-run.
    """

    async def main():
        k = build(latency_ms={"search_flights": 800, "search_hotels": 800})
        a = await k.kernel.dispatch("search_flights", {"destination": "BOM"}, ["destination"])
        b = await k.kernel.dispatch("search_hotels", {"city": "BOM"}, ["city"])
        await k.clock.sleep(120)
        cancelled = await k.kernel.apply_work_policy(policy_for(InterruptionKind.BARGE_IN))
        await settle(a, b)
        return cancelled, a.outcome, b.outcome

    cancelled, a_out, b_out = run_virtual(main())
    assert cancelled == []
    assert a_out is b_out is CallOutcome.COMPLETED_STILL_VALID


def test_repeat_request_keeps_work_too() -> None:
    async def main():
        k = build(latency_ms={"search_flights": 400})
        rec = await k.kernel.dispatch("search_flights", {"destination": "BOM"}, ["destination"])
        cancelled = await k.kernel.apply_work_policy(policy_for(InterruptionKind.REPEAT_REQUEST))
        await settle(rec)
        return cancelled, rec.outcome

    cancelled, outcome = run_virtual(main())
    assert cancelled == [] and outcome is CallOutcome.COMPLETED_STILL_VALID


def test_refinement_keeps_the_call_running() -> None:
    """"Morning flights only" must filter a result, not re-fetch it."""

    async def main():
        k = build(latency_ms={"search_flights": 700})
        rec = await k.kernel.dispatch("search_flights", {"destination": "BOM", "date": "d"}, ["destination"])
        await k.clock.sleep(200)
        cancelled = await k.kernel.apply_work_policy(policy_for(InterruptionKind.REFINEMENT))
        await settle(rec)
        return cancelled, rec.outcome, rec.result["count"]

    cancelled, outcome, count = run_virtual(main())
    assert cancelled == []
    assert outcome is CallOutcome.COMPLETED_STILL_VALID and count > 0


def test_goal_switch_cancels_everything() -> None:
    async def main():
        k = build(latency_ms={"search_flights": 900, "search_hotels": 900})
        a = await k.kernel.dispatch("search_flights", {"destination": "BOM"}, ["destination"])
        b = await k.kernel.dispatch("search_flights", {"destination": "BLR"}, ["destination"])
        await k.clock.sleep(100)
        cancelled = await k.kernel.apply_work_policy(policy_for(InterruptionKind.GOAL_SWITCH))
        await settle(a, b)
        return len(cancelled), a.outcome, b.outcome

    n, a_out, b_out = run_virtual(main())
    assert n == 2
    assert a_out is b_out is CallOutcome.CANCELLED_BEFORE_EFFECT


def test_cancellation_grace_period_is_zero_virtual_ms() -> None:
    """The guide asks for cancellation "within a few ms". A rule-based fast path
    costs no virtual time at all, and issuing N cancels does not cost N times
    more."""

    async def main():
        k = build(latency_ms={"search_flights": 900})
        k.state.set_slot("destination", "Delhi")
        recs = [
            await k.kernel.dispatch("search_flights", {"destination": "Delhi", "i": i}, ["destination"])
            for i in range(8)
        ]
        await k.clock.sleep(250)
        at_interrupt = k.clock.now
        delta = k.state.patch_slot("destination", "Mumbai")
        await k.kernel.apply_work_policy(policy_for(InterruptionKind.SLOT_CORRECTION), delta.invalidating_slots)
        await settle(*recs)
        cancels = [r for r in k.trace.records if r.name == "cancel"]
        return at_interrupt, [c.t for c in cancels], len(cancels)

    at, times, n = run_virtual(main())
    assert n == 8
    assert all(t == at for t in times), "cancellation latency grew with the number of calls"


# ============================================================ duplicate suppression

def test_the_same_state_change_twice_reaches_the_tool_once() -> None:
    async def main():
        k = build(latency_ms={"book_flight": 200})
        k.state.set_intent("book_flight")
        first = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await settle(first)
        second = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await settle(second)
        return first.outcome, second.outcome, k.env.world.duplicates(), len(k.env.world.live_effects())

    first, second, dupes, n = run_virtual(main())
    assert first is CallOutcome.COMPLETED_STILL_VALID
    assert second is CallOutcome.DUPLICATE_SUPPRESSED
    assert dupes == {} and n == 1


def test_a_concurrent_duplicate_joins_rather_than_dispatching() -> None:
    """Two re-plans milliseconds apart must not both book.

    This is the race that claiming *after* dispatch loses.
    """

    async def main():
        k = build(latency_ms={"book_flight": 800})
        k.state.set_intent("book_flight")
        first = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await k.clock.sleep(5)
        second = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await settle(first, second)
        return first.call_id, second.call_id, len(k.env.world.live_effects())

    a, b, n = run_virtual(main())
    assert a == b, "the second dispatch should have joined the first"
    assert n == 1


def test_different_arguments_are_not_suppressed() -> None:
    async def main():
        k = build(latency_ms={"book_flight": 100})
        k.state.set_intent("book_flight")
        a = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await settle(a)
        b = await k.kernel.dispatch("book_flight", {"flight_no": "6E202"}, ["flight_no"])
        await settle(b)
        return a.outcome, b.outcome, len(k.env.world.live_effects())

    a_out, b_out, n = run_virtual(main())
    assert a_out is b_out is CallOutcome.COMPLETED_STILL_VALID
    assert n == 2


def test_retry_after_a_transient_fault_is_allowed() -> None:
    """A failed attempt left no effect, so retrying is not a duplicate.

    Collapsing 'failed' into 'already done' would break retry-after-fault, which
    the public suite names explicitly.
    """

    async def main():
        k = build(
            latency_ms={"book_flight": 100},
            faults=[Fault(tool="book_flight", kind=FaultKind.TRANSIENT, on_call=1)],
        )
        k.state.set_intent("book_flight")
        first = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await settle(first)
        retry = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await settle(retry)
        return first.outcome, retry.outcome, len(k.env.world.live_effects())

    first, retry, n = run_virtual(main())
    assert first is CallOutcome.FAILED
    assert retry is CallOutcome.COMPLETED_STILL_VALID
    assert n == 1


# ============================================================ speculation

def test_confirming_a_speculative_call_joins_it() -> None:
    """The latency win: the observation is ready at max(plan, tool), not plan + tool."""

    async def main():
        k = build(latency_ms={"search_flights": 600})
        spec = await k.kernel.dispatch(
            "search_flights", {"destination": "BOM"}, ["destination"], speculative=True
        )
        await k.clock.sleep(200)  # planner deliberating
        confirmed = await k.kernel.dispatch("search_flights", {"destination": "BOM"}, ["destination"])
        await settle(spec)
        joins = [r for r in k.trace.records if r.name == "speculation_join"]
        return spec.call_id, confirmed.call_id, confirmed.speculative, k.clock.now, joins

    spec_id, conf_id, still_spec, end, joins = run_virtual(main())
    assert spec_id == conf_id, "confirmation issued a second call instead of joining"
    assert still_spec is False, "the joined call should no longer be speculative"
    assert end == pytest.approx(600.0), "joining must not restart the clock"
    assert joins and joins[0].payload["saved_ms"] == pytest.approx(200.0)


def test_a_missed_speculation_is_free() -> None:
    """A wrong guess costs a cancelled read-only call and nothing else."""

    async def main():
        k = build(latency_ms={"search_flights": 600})
        k.state.set_slot("destination", "BOM")
        spec = await k.kernel.dispatch(
            "search_flights", {"destination": "BOM"}, ["destination"], speculative=True
        )
        await k.clock.sleep(100)
        delta = k.state.patch_slot("destination", "BLR")
        await k.kernel.apply_work_policy(policy_for(InterruptionKind.SLOT_CORRECTION), delta.invalidating_slots)
        await settle(spec)
        return spec.outcome, k.env.world.effects

    outcome, effects = run_virtual(main())
    assert outcome is CallOutcome.CANCELLED_BEFORE_EFFECT
    assert effects == [], "a read-only speculation can never leave an effect"


def test_speculating_a_state_change_is_structurally_impossible() -> None:
    async def main():
        k = build()
        with pytest.raises(SpeculationRefused):
            await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, [], speculative=True)
        return [r.name for r in k.trace.records if r.name == "speculation_refused"]

    assert run_virtual(main()) == ["speculation_refused"]


# ============================================================ stale completion

def test_a_call_that_finishes_after_its_slot_changed_is_marked_stale() -> None:
    """Not cancelled — it was never cancelled. It simply is no longer wanted.

    Silently using this result is how an agent answers about Delhi when the user
    said Mumbai.
    """

    async def main():
        k = build(latency_ms={"search_flights": 400})
        k.state.set_slot("destination", "Delhi")
        rec = await k.kernel.dispatch("search_flights", {"destination": "Delhi"}, ["destination"])
        await k.clock.sleep(100)
        k.state.patch_slot("destination", "Mumbai")  # no cancellation issued
        await settle(rec)
        return rec.outcome

    assert run_virtual(main()) is CallOutcome.COMPLETED_NOW_STALE


# ============================================================ uncertain cancellation

def test_cancelling_a_state_change_late_is_uncertain_then_compensated() -> None:
    """The case the whole effect ledger exists for.

    The cancel arrives after the environment committed. The agent cannot know
    that, so it probes with the declared verifier, finds the booking, and undoes
    it. What it must never do is record the cancel as clean.
    """

    async def main():
        k = build(latency_ms={"book_flight": 1000, "get_booking_status": 50, "cancel_booking": 50},
                  commit_fraction=0.7)
        k.state.set_intent("book_flight")
        rec = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])

        await k.clock.sleep(800)  # past the 700 ms commit point
        await k.kernel.cancel(rec, "goal_switch")
        uncertain_at_cancel = rec.outcome

        disclosed = await k.kernel.resolve_effects()
        return uncertain_at_cancel, rec.outcome, rec.resolution, disclosed, k.env.world.live_effects()

    at_cancel, final, resolution, disclosed, live = run_virtual(main())
    assert at_cancel is CallOutcome.CANCELLED_UNCERTAIN
    assert final is CallOutcome.COMPENSATED
    assert resolution == "compensated"
    assert disclosed == []
    assert live == [], "the stale booking must not survive"


def test_cancelling_a_state_change_early_verifies_clean() -> None:
    async def main():
        k = build(latency_ms={"book_flight": 1000, "get_booking_status": 50}, commit_fraction=0.7)
        k.state.set_intent("book_flight")
        rec = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await k.clock.sleep(200)  # well before the commit
        await k.kernel.cancel(rec, "goal_switch")
        disclosed = await k.kernel.resolve_effects()
        return rec.outcome, rec.resolution, disclosed, k.env.world.live_effects()

    outcome, resolution, disclosed, live = run_virtual(main())
    assert outcome is CallOutcome.CANCELLED_BEFORE_EFFECT
    assert resolution == "verified_no_effect"
    assert disclosed == [] and live == []


def test_an_unverifiable_effect_is_disclosed_not_swallowed() -> None:
    """With no verifier in the manifest we genuinely cannot know. Saying so is
    the only honest option, and the trace records that we did."""

    manifest = parse_manifest(
        [{"name": "create_ticket", "mutating": True, "params": [{"name": "subject"}]}]
    )

    async def main():
        clock, trace = Clock(), Trace()
        state = SessionState(session_id="t")
        env = MockEnvironment(clock, trace, World(), EnvConfig(latency_ms={"create_ticket": 1000},
                                                              commit_fraction=0.5))
        kernel = Dispatcher(clock=clock, trace=trace, state=state, manifest=manifest, executor=env.call)

        rec = await kernel.dispatch("create_ticket", {"subject": "leak"}, [])
        await clock.sleep(700)
        await kernel.cancel(rec, "goal_switch")
        disclosed = await kernel.resolve_effects()
        return disclosed, rec.resolution, [r.name for r in trace.records if r.name == "effect_undetermined"]

    disclosed, resolution, traced = run_virtual(main())
    assert len(disclosed) == 1
    assert resolution == "disclosed"
    assert traced == ["effect_undetermined"], "the uncertainty must appear in the trace"


def test_read_only_cancellation_needs_no_resolution() -> None:
    async def main():
        k = build(latency_ms={"search_flights": 800})
        rec = await k.kernel.dispatch("search_flights", {"destination": "BOM"}, ["destination"])
        await k.clock.sleep(400)
        await k.kernel.cancel(rec, "goal_switch")
        return rec.outcome, await k.kernel.resolve_effects()

    outcome, disclosed = run_virtual(main())
    assert outcome is CallOutcome.CANCELLED_BEFORE_EFFECT
    assert disclosed == []


# ============================================================ trace completeness

def test_no_call_exits_without_an_outcome_in_the_trace() -> None:
    """An action that is not logged did not happen.

    Covers the completed, cancelled, suppressed and failed paths in one run.
    """

    async def main():
        k = build(
            latency_ms={"search_flights": 900, "book_flight": 100, "search_hotels": 200},
            faults=[Fault(tool="search_hotels", kind=FaultKind.PERMANENT, on_call=1)],
        )
        k.state.set_intent("book_flight")
        k.state.set_slot("destination", "Delhi")

        cancelled = await k.kernel.dispatch("search_flights", {"destination": "Delhi"}, ["destination"])
        failed = await k.kernel.dispatch("search_hotels", {"city": "Goa"}, ["city"])
        booked = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await settle(failed, booked)

        dupe = await k.kernel.dispatch("book_flight", {"flight_no": "AI101"}, ["flight_no"])
        await k.kernel.cancel(cancelled, "goal_switch")
        await settle(cancelled)

        traced = {r.payload["call_id"] for r in k.trace.records if r.name == "call_settled"}
        traced |= {r.payload["call_id"] for r in k.trace.records if r.name == "duplicate_suppressed"}
        return {c.call_id: c.outcome for c in k.kernel.registry}, traced

    outcomes, traced = run_virtual(main())
    assert all(o is not CallOutcome.PENDING for o in outcomes.values()), "a call was left pending"
    assert set(outcomes) == traced, "a call settled without appearing in the trace"
    assert CallOutcome.CANCELLED_BEFORE_EFFECT in outcomes.values()
    assert CallOutcome.FAILED in outcomes.values()
    assert CallOutcome.DUPLICATE_SUPPRESSED in outcomes.values()


def test_unknown_tool_is_rejected_without_crashing_the_session() -> None:
    async def main():
        k = build()
        rec = await k.kernel.dispatch("summon_a_unicorn", {"colour": "mauve"}, [])
        return rec.outcome, rec.error, [r.name for r in k.trace.records if r.name == "dispatch_rejected"]

    outcome, error, traced = run_virtual(main())
    assert outcome is CallOutcome.FAILED and "unknown tool" in error
    assert traced == ["dispatch_rejected"]


def test_cancel_action_is_emitted_before_the_task_is_touched() -> None:
    """Scoring reads the trace, and 'prompt cancellation' is measured from it."""

    async def main():
        k = build(latency_ms={"search_flights": 900})
        rec = await k.kernel.dispatch("search_flights", {"destination": "BOM"}, ["destination"])
        await k.clock.sleep(100)
        await k.kernel.cancel(rec, "slot_correction:destination", ["destination"])
        await settle(rec)

        order = [r.name for r in k.trace.records if r.name in ("cancel", "call_settled")]
        cancel_rec = next(r for r in k.trace.records if r.name == "cancel")
        return order, cancel_rec.t, cancel_rec.payload

    order, t, payload = run_virtual(main())
    assert order == ["cancel", "call_settled"]
    assert t == pytest.approx(100.0)
    assert payload["reason"] == "slot_correction:destination"
    assert payload["invalidated_by_slots"] == ["destination"]
