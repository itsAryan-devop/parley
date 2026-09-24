"""Adversarial timing: invariants must hold under schedules nobody wrote.

The hidden set is ~60 scenarios of "edge cases and adversarial timing". Passing
the fifteen scenarios we wrote proves very little, because they are the cases we
thought of. These tests jitter everything that can be jittered and assert only
what must never break.

Two real bugs came out of this, neither reachable from the public suite:

  * A speculative call cancelled in the same instant it was created never
    entered its own coroutine body, so the `except CancelledError` handler
    could not fire and the call vanished from the trace entirely.
  * The idempotency key included `intent`, so booking a flight before the
    intent resolved and again afterwards produced two different keys for the
    same action — a genuine double-booking.

The suite here is small and fast; `scripts/fuzz.py` runs the large campaigns.
"""

from __future__ import annotations

import pytest

from harness.fuzz import check_invariants, fuzz, perturb
from harness.runner import run_scenario
from harness.scenario import load_all
from parley.agent.model import InterruptionModel

SCENARIOS = load_all()
MODEL = InterruptionModel.load_default()
TRIALS = 6


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_invariants_hold_under_perturbed_timing(scenario) -> None:
    for seed in range(TRIALS):
        mutated, p = perturb(scenario, seed, jitter_ms=400.0)
        result = run_scenario(mutated, model=MODEL)
        violations = check_invariants(result)
        assert not violations, f"{scenario.id} [{p.describe()}]\n  " + "\n  ".join(violations)


def test_simultaneous_events_do_not_lose_a_call() -> None:
    """Regression: a task cancelled before its first step never runs its body.

    `create_task` schedules; it does not run. Two transcript chunks collapsed
    onto one timestamp meant a speculative call was superseded in the instant it
    was created, so nothing inside `_run` ever executed and no handler there
    could settle it. A call missing from the trace is unscoreable.
    """
    from harness.clock import Clock, run_virtual
    from harness.mockenv import EnvConfig, MockEnvironment, World
    from harness.trace import Trace
    from parley.kernel import CallOutcome, Dispatcher
    from parley.protocol import SessionState, parse_manifest

    manifest = parse_manifest(
        [{"name": "search_flights", "read_only": True,
          "params": [{"name": "destination"}, {"name": "date"}]}]
    )

    async def main():
        clock, trace = Clock(), Trace()
        state = SessionState(session_id="t")
        env = MockEnvironment(clock, trace, World(), EnvConfig(default_latency_ms=500))
        kernel = Dispatcher(clock=clock, trace=trace, state=state,
                            manifest=manifest, executor=env.call)

        guess = await kernel.dispatch("search_flights", {"destination": "BOM"},
                                      ["destination"], speculative=True)
        # No await in between: the task has been scheduled but has not run.
        confirmed = await kernel.dispatch(
            "search_flights", {"destination": "BOM", "date": "Tue"}, ["destination", "date"]
        )
        await clock.sleep(800)
        return guess, confirmed, trace

    guess, confirmed, trace = run_virtual(main())

    assert guess.started is False, "the body should never have run"
    assert guess.outcome is CallOutcome.CANCELLED_BEFORE_EFFECT
    assert confirmed.outcome is CallOutcome.COMPLETED_STILL_VALID
    settled = {r.payload["call_id"] for r in trace.named("call_settled")}
    assert guess.call_id in settled, "the call disappeared from the trace"


def test_the_idempotency_key_does_not_depend_on_intent() -> None:
    """Regression: the same action must have the same key however we label it.

    Booking a flight while the intent was still unlabelled, then again once it
    had resolved, produced two different keys and two reservations. The identity
    of a business action is what it does, not what we were calling the goal.
    """
    from parley.kernel.ledger import derive_key
    from parley.protocol import parse_manifest

    spec = parse_manifest(
        [{"name": "book_flight", "mutating": True, "params": [{"name": "flight_no"}]}]
    )["book_flight"]

    args = {"flight_no": "6E202"}
    assert derive_key(spec, None, args) == derive_key(spec, "book_flight", args)
    assert derive_key(spec, "book_hotel", args) == derive_key(spec, "rent_kayak", args)
    assert derive_key(spec, None, args) != derive_key(spec, None, {"flight_no": "AI101"})


def test_a_broad_campaign_finds_nothing() -> None:
    """A wider sweep than the per-scenario parametrisation, still CI-fast."""
    report = fuzz(SCENARIOS, trials=4, jitter_ms=900.0, model=MODEL)
    assert not report.violations, "\n".join(
        f"{sid} [{desc}]: {'; '.join(v)}" for sid, desc, v in report.violations
    )
    assert report.runs == len(SCENARIOS) * 4


def test_perturbation_actually_perturbs() -> None:
    """Guard: if `perturb` silently stopped changing anything, every fuzz run
    above would be testing the unmodified scenario and passing for free."""
    scenario = SCENARIOS[0]
    changed = 0
    for seed in range(10):
        mutated, _ = perturb(scenario, seed, jitter_ms=400.0)
        original = [e["t"] for e in scenario.events]
        moved = [e["t"] for e in mutated.events]
        if original != moved:
            changed += 1
        assert mutated.env.latency_ms != scenario.env.latency_ms
    assert changed >= 8, f"only {changed}/10 perturbations moved any event"


def test_perturbations_are_reproducible_from_their_seed() -> None:
    """A violation is only actionable if the schedule that caused it can be
    recreated exactly."""
    a, pa = perturb(SCENARIOS[0], 42, jitter_ms=500.0)
    b, pb = perturb(SCENARIOS[0], 42, jitter_ms=500.0)
    assert a.model_dump() == b.model_dump()
    assert pa.describe() == pb.describe()
