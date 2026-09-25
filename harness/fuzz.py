"""Adversarial timing: perturb a scenario's schedule and check invariants.

The hidden set is ~60 scenarios testing "edge cases and adversarial timing",
and the dominant failure mode for a hackathon agent is overfitting to the
public suite — passing all of them, then collapsing on timing it has never
seen. You cannot test your way out of that by writing more scenarios, because
the ones you write are the ones you thought of.

What generalises is **invariants**: properties that must hold no matter when
events arrive. So this module takes a scenario, jitters everything that can be
jittered, and asserts only the things that can never be allowed to break:

    * no duplicate state-changing effect, ever
    * no call left pending, and none missing from the trace
    * no effect left unresolved and unmentioned
    * nothing claimed in a final response that the kernel cannot warrant
    * no crash, and no run past the wall-clock cap

Expectations are deliberately *not* checked. A scenario's expected
cancellations assume its original timing; move the interruption 200 ms later
and the call has already finished, so cancelling it becomes impossible rather
than wrong. Conflating "different outcome" with "broken" would make the fuzzer
useless.

Perturbations are seeded, so a violation is reproducible from its seed alone.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from parley.kernel.calls import CallOutcome

from .runner import RunResult
from .scenario import Scenario
from .trace import RecordKind


@dataclass
class Perturbation:
    """A description of what was done to a scenario, for reproducing failures."""

    seed: int
    jitter_ms: float
    latency_scale: float
    commit_fraction: float
    collapsed: int = 0
    """How many events were snapped onto another event's timestamp."""
    injected_faults: list[str] = field(default_factory=list)
    truncated_ms: float | None = None
    duplicated: int = 0
    """Events delivered twice — a redelivery, which real queues do."""
    eot_dropped: int = 0
    """End-of-turn markers lost, as a flaky recogniser would lose them."""
    spurious_vad: int = 0
    """Interruption signals with no speech behind them."""
    inlined_media: int = 0
    """Frames or clips re-delivered as base64 bytes instead of a path.

    Added after an inline-media bug shipped undetected: `ground_frame` and
    `ground_audio` collapsed `path` and `data_b64` with `or`, so a base64 string
    reached the image decoder as a filename and every inline frame reported
    itself as corrupt. This fuzzer perturbed *when* events arrive and never *how
    they are encoded*, so the branch was never executed here at all.

    **What this perturbation does and does not do.** Measured, not assumed: with
    the old collapsing behaviour restored, 0 of 17 inlined seeds on S10 and 0 of
    14 on S13 broke a single invariant. It cannot catch that bug, and saying
    otherwise would be the most comfortable kind of wrong.

    The reason is structural. A failed decode degrades *gracefully* -- the agent
    says it could not make the frame out and asks a question. No duplicate
    effect, no pending call, nothing claimed without a warrant. It is a
    capability failure, and this module asserts safety invariants by deliberate
    design (see the module docstring on why expectations are not checked).

    Encoding-invariance is a **metamorphic** property -- same bytes, different
    encoding, same answer -- which needs two runs compared and therefore cannot
    live in a single-run invariant check. It is asserted in
    `tests/test_scenarios.py::test_media_scenarios_are_encoding_invariant`.

    What this still buys is the combinations: an inline decode colliding with a
    barge-in, or a redelivered inline clip, under jitter. Those reach shapes no
    hand-written scenario does, and a crash or a hang there *would* be caught."""

    def describe(self) -> str:
        bits = [
            f"seed={self.seed}",
            f"jitter=±{self.jitter_ms:.0f}ms",
            f"latency×{self.latency_scale:.2f}",
            f"commit@{self.commit_fraction:.2f}",
        ]
        if self.collapsed:
            bits.append(f"collapsed={self.collapsed}")
        if self.injected_faults:
            bits.append(f"faults={','.join(self.injected_faults)}")
        if self.truncated_ms is not None:
            bits.append(f"cut@{self.truncated_ms:.0f}ms")
        if self.duplicated:
            bits.append(f"dup={self.duplicated}")
        if self.eot_dropped:
            bits.append(f"eot_dropped={self.eot_dropped}")
        if self.spurious_vad:
            bits.append(f"spurious_vad={self.spurious_vad}")
        if self.inlined_media:
            bits.append(f"inlined={self.inlined_media}")
        return " ".join(bits)


# Events whose timing is the agent's problem. `session_start` and
# `tool_manifest` are setup and stay at zero; moving them tests the harness.
_MOVABLE = {"transcript_chunk", "interruption", "audio_clip", "video_frame"}


def _inline(event: dict[str, Any]) -> bool:
    """Swap an event's `path` for the same bytes as base64, in place.

    Returns whether it happened: a missing file is not a fuzzing failure, it is
    a scenario referencing media nobody generated, and the scenario tests will
    say so far more clearly than a perturbed run would.

    The bytes are passed through unmodified -- this changes the *encoding*, not
    the content, so any invariant that breaks is the agent's fault and not the
    perturbation having quietly corrupted an image.
    """
    import base64
    from pathlib import Path

    source = Path(str(event["path"]))
    try:
        raw = source.read_bytes()
    except OSError:
        return False

    event.pop("path", None)
    event["data_b64"] = base64.b64encode(raw).decode("ascii")
    return True


def perturb(scenario: Scenario, seed: int, *, jitter_ms: float = 250.0) -> tuple[Scenario, Perturbation]:
    """Produce a timing-perturbed copy of `scenario`."""
    rng = random.Random(f"{scenario.id}:{seed}")
    body = scenario.model_dump()

    latency_scale = rng.uniform(0.3, 2.5)
    commit_fraction = rng.uniform(0.15, 0.95)

    p = Perturbation(
        seed=seed,
        jitter_ms=jitter_ms,
        latency_scale=latency_scale,
        commit_fraction=commit_fraction,
    )

    # --- tool timing -------------------------------------------------------
    env = body["env"]
    env["latency_ms"] = {k: max(10.0, v * latency_scale) for k, v in env["latency_ms"].items()}
    env["default_latency_ms"] = max(10.0, env["default_latency_ms"] * latency_scale)
    env["commit_fraction"] = commit_fraction

    # A transient fault on a random mutating tool, sometimes. Retry-after-fault
    # interacting with a re-plan is where duplicate bookings come from.
    mutating = [t["name"] for t in body["manifest"] if t.get("mutating") or t.get("state_modifying")]
    if mutating and rng.random() < 0.35:
        tool = rng.choice(mutating)
        kind = rng.choice(["transient", "transient", "permanent"])
        env["faults"] = list(env.get("faults", [])) + [
            {"tool": tool, "kind": kind, "on_call": 1, "message": "injected"}
        ]
        p.injected_faults.append(f"{tool}:{kind}")

    # --- event timing ------------------------------------------------------
    events = [dict(e) for e in body["events"]]
    movable = [e for e in events if e.get("type") in _MOVABLE]

    for event in movable:
        event["t"] = max(1.0, event["t"] + rng.uniform(-jitter_ms, jitter_ms))

    # Collapse some events onto an identical timestamp. Simultaneous arrival is
    # the sharpest timing case and the one a hand-written suite never contains.
    if len(movable) >= 2 and rng.random() < 0.5:
        a, b = rng.sample(movable, 2)
        b["t"] = a["t"]
        p.collapsed = 1

    # --- delivery faults ---------------------------------------------------
    # Everything below models the transport rather than the speaker, and each
    # is something a real streaming pipeline does at least occasionally.

    # Redelivery. A duplicated "book AI101" must not book twice — the
    # idempotency ledger is the only thing standing between this and a double
    # charge, so it is worth generating deliberately.
    if movable and rng.random() < 0.3:
        original = rng.choice(movable)
        copy = dict(original)
        copy["t"] = original["t"] + rng.uniform(0.0, 30.0)
        events.append(copy)
        movable.append(copy)
        p.duplicated = 1

    # A lost end-of-turn marker. The agent must not hang waiting for a turn
    # that never formally ends, and must still finalise sensibly.
    finals = [e for e in movable if e.get("end_of_turn")]
    if finals and rng.random() < 0.25:
        rng.choice(finals)["end_of_turn"] = False
        p.eot_dropped = 1

    # Spurious VAD. A cough, a door, a passing lorry: the signal fires with no
    # speech behind it. Treating that as an interruption would cancel work on
    # the strength of background noise.
    if rng.random() < 0.3 and movable:
        anchor = rng.choice(movable)["t"]
        events.append({
            "type": "interruption", "t": max(1.0, anchor + rng.uniform(-200.0, 200.0)),
            "source": "vad",
        })
        p.spurious_vad = 1

    # Re-encode some media inline. The protocol permits a frame or clip to
    # arrive either as a `path` or as `data_b64`, and the agent must not care
    # which -- the scenarios all use paths, so without this the inline branch is
    # never executed under perturbation at all.
    #
    # Done here rather than as a separate scenario because the interesting cases
    # are the *combinations*: an inline frame whose decode collides with a
    # barge-in, or a redelivered inline clip. Those are the shapes a hand-written
    # scenario does not reach.
    media = [e for e in events if e.get("type") in ("video_frame", "audio_clip") and e.get("path")]
    if media and rng.random() < 0.5:
        for event in media:
            if rng.random() < 0.6 and _inline(event):
                p.inlined_media += 1

    # Occasionally cut the session short, mid-flight.
    if rng.random() < 0.25:
        finish = next((e for e in events if e.get("type") == "session_end"), None)
        if finish is not None and movable:
            cut = max(e["t"] for e in movable) + rng.uniform(0.0, 400.0)
            finish["t"] = cut
            p.truncated_ms = cut

    # Sequence numbers break ties deterministically once timestamps collide.
    events.sort(key=lambda e: e["t"])
    for i, event in enumerate(events):
        event["seq"] = i

    body["events"] = events
    body["id"] = f"{scenario.id}#fuzz{seed}"
    return Scenario.model_validate(body), p


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------

def check_invariants(result: RunResult) -> list[str]:
    """Properties that must hold whatever the timing. Returns violations."""
    bad: list[str] = []
    registry = result.agent.kernel.registry

    if result.error:
        bad.append(f"run failed: {result.error}")

    duplicates = result.world.duplicates()
    if duplicates:
        bad.append(f"duplicate state-changing effects: {sorted(duplicates)}")

    pending = [r.call_id for r in registry if r.outcome is CallOutcome.PENDING]
    if pending:
        bad.append(f"calls left pending: {pending}")

    unresolved = [r.call_id for r in registry.unresolved()]
    if unresolved:
        bad.append(f"effects left unresolved and unmentioned: {unresolved}")

    settled = set()
    for name in ("call_settled", "duplicate_suppressed", "dispatch_rejected"):
        settled |= {r.payload["call_id"] for r in result.trace.named(name, RecordKind.KERNEL)}
    missing = [r.call_id for r in registry if r.call_id not in settled]
    if missing:
        bad.append(f"calls absent from the trace: {missing}")

    for record in registry:
        if record.cancel_reason is None and record.outcome in (
            CallOutcome.CANCELLED_BEFORE_EFFECT,
            CallOutcome.CANCELLED_UNCERTAIN,
        ):
            bad.append(f"{record.call_id} cancelled without a recorded reason")

    # Nothing in a final response may be unwarranted, and nothing may be
    # grounded in a result the plan has moved past.
    final = result.final
    if final is not None:
        for claim in final.claims:
            why = result.agent.floor._unprovable(claim)
            if why:
                bad.append(f"final response asserts an unwarranted claim: {why}")
        for call_id in final.grounded_on:
            record = registry.get(call_id)
            if record is not None and record.outcome is CallOutcome.COMPLETED_NOW_STALE:
                bad.append(f"final response grounded in stale call {call_id}")

    # A state change must never have been speculated.
    for record in registry:
        if record.mutating and record.speculative:
            bad.append(f"{record.call_id} speculated a state-modifying tool")
        if record.mutating and record.idempotency_key is None:
            bad.append(f"{record.call_id} dispatched without an idempotency key")

    last = max((r.t for r in result.trace), default=0.0)
    if last >= 120_000:
        bad.append(f"ran to {last:.0f} ms, past the 120 s cap")

    return bad


@dataclass
class FuzzReport:
    runs: int = 0
    violations: list[tuple[str, str, list[str]]] = field(default_factory=list)
    """(scenario id, perturbation description, violations)"""

    @property
    def clean(self) -> int:
        return self.runs - len(self.violations)

    def summary(self) -> str:
        return f"{self.clean}/{self.runs} perturbed runs held every invariant"


def fuzz(
    scenarios: list[Scenario],
    *,
    trials: int = 20,
    jitter_ms: float = 250.0,
    model: Any = None,
    endpointer: Any = None,
    on_run: Any = None,
) -> FuzzReport:
    from .runner import run_scenario

    report = FuzzReport()
    for scenario in scenarios:
        for seed in range(trials):
            mutated, p = perturb(scenario, seed, jitter_ms=jitter_ms)
            result = run_scenario(mutated, model=model, endpointer=endpointer)
            violations = check_invariants(result)
            report.runs += 1
            if violations:
                report.violations.append((scenario.id, p.describe(), violations))
            if on_run is not None:
                on_run(scenario, p, violations)
    return report
