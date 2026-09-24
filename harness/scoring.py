"""Score a run against the published rubric, strictly from the trace.

    Task Completion       40%   correct tools, valid args, snapshot accuracy, grounded final
    Interruption Recovery 35%   prompt cancellation, absence of stale re-runs, updated snapshot
    Response Latency      15%   time to first substantive spoken action after input/interrupt
    Safety & Protocol     10%   zero duplicate state-changing calls, schema adherence
                          x     quality multiplier 0.80-1.20 (naturalness, truthfulness, relevance)

This is our reconstruction of the guide's scheme, not the official grader, and
it is used the way a thermometer is used: to notice regressions and to argue
about trade-offs with numbers instead of opinions. Where the guide is
ambiguous the choice is documented inline.

One deliberate asymmetry: **over-cancellation is penalised as hard as
under-cancellation**. The guide only names "prompt cancellation of invalidated
calls", but cancelling a call that was still valid destroys work and shows up in
the 40% block, so a scorer that ignored it would reward exactly the
flush-everything behaviour this project exists to beat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from parley.kernel.calls import CallOutcome

from .runner import RunResult
from .trace import RecordKind

WEIGHTS = {"task": 0.40, "recovery": 0.35, "latency": 0.15, "safety": 0.10}

LATENCY_TARGET_MS = 300.0
"""Full marks at or under this. The guide says "a few hundred milliseconds"."""
LATENCY_ZERO_MS = 2000.0
"""No marks beyond this."""


@dataclass
class Component:
    name: str
    score: float
    """0..1 before weighting."""
    weight: float
    notes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def weighted(self) -> float:
        return self.score * self.weight * 100.0


@dataclass
class ScoreCard:
    scenario_id: str
    components: list[Component]
    multiplier: float
    multiplier_notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def raw(self) -> float:
        return sum(c.weighted for c in self.components)

    @property
    def total(self) -> float:
        return max(0.0, min(120.0, self.raw * self.multiplier))

    @property
    def failures(self) -> list[str]:
        return [f for c in self.components for f in c.failures]

    def to_payload(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario_id,
            "raw": round(self.raw, 1),
            "multiplier": round(self.multiplier, 3),
            "total": round(self.total, 1),
            "error": self.error,
            "components": {
                c.name: {"score": round(c.score, 3), "weighted": round(c.weighted, 1),
                         "failures": c.failures, "notes": c.notes}
                for c in self.components
            },
            "multiplier_notes": self.multiplier_notes,
        }


# ---------------------------------------------------------------- components

def _task_completion(result: RunResult) -> Component:
    expect = result.scenario.expect
    checks: list[bool] = []
    failures: list[str] = []
    notes: list[str] = []

    snapshot = result.snapshot
    called = {r.payload["tool"] for r in result.trace.named("tool_call", RecordKind.ACTION)}

    if expect.intent is not None:
        ok = snapshot.intent == expect.intent
        checks.append(ok)
        if not ok:
            failures.append(f"intent {snapshot.intent!r}, expected {expect.intent!r}")

    if expect.slots is not None:
        for name, want in expect.slots.items():
            ok = snapshot.slots.get(name) == want
            checks.append(ok)
            if not ok:
                failures.append(f"slot {name}={snapshot.slots.get(name)!r}, expected {want!r}")

    for name in expect.absent_slots:
        ok = name not in snapshot.slots
        checks.append(ok)
        if not ok:
            failures.append(f"slot {name!r} should have been dropped")

    for tool in expect.tools_called:
        ok = tool in called
        checks.append(ok)
        if not ok:
            failures.append(f"{tool} was never called")

    for tool in expect.tools_not_called:
        ok = tool not in called
        checks.append(ok)
        if not ok:
            failures.append(f"{tool} should not have been called")

    if expect.live_effects is not None:
        actual = [{"tool": e.tool, "args": e.args} for e in result.world.live_effects()]
        ok = _effects_match(actual, expect.live_effects)
        checks.append(ok)
        if not ok:
            failures.append(f"world effects {actual}, expected {expect.live_effects}")

    if expect.must_clarify:
        ok = bool(result.trace.named("clarify", RecordKind.ACTION))
        checks.append(ok)
        if not ok:
            failures.append("expected a clarification and none was asked")

    if expect.must_not_claim_completion:
        # Checked across every utterance, not just the final one. The gate makes
        # an unwarranted completion claim structurally impossible; this confirms
        # the gate is actually in the path.
        claimed = [
            (r.name, c)
            for r in result.trace.of_kind(RecordKind.ACTION)
            for c in r.payload.get("claims", [])
            if c.get("kind") == "completed"
        ]
        checks.append(not claimed)
        if claimed:
            failures.append(f"claimed completion when nothing completed: {claimed}")

    # A final response must exist and be grounded in a real result.
    if result.final is None:
        checks.append(False)
        failures.append("no final response was emitted")
    else:
        checks.append(True)
        if not result.final.grounded_on and not expect.must_clarify:
            notes.append("final response is not grounded in any call result")

    score = sum(checks) / len(checks) if checks else 0.0
    return Component("task", score, WEIGHTS["task"], notes, failures)


def _effects_match(actual: list[dict], expected: list[dict]) -> bool:
    if len(actual) != len(expected):
        return False
    remaining = list(actual)
    for want in expected:
        for i, got in enumerate(remaining):
            if got["tool"] == want["tool"] and all(
                got["args"].get(k) == v for k, v in want.get("args", {}).items()
            ):
                remaining.pop(i)
                break
        else:
            return False
    return True


def _interruption_recovery(result: RunResult) -> Component:
    expect = result.scenario.expect
    registry = result.agent.kernel.registry
    checks: list[bool] = []
    failures: list[str] = []
    notes: list[str] = []

    by_tool: dict[str, list] = {}
    for record in registry:
        by_tool.setdefault(record.tool, []).append(record)

    cancelled_outcomes = {
        CallOutcome.CANCELLED_BEFORE_EFFECT,
        CallOutcome.CANCELLED_UNCERTAIN,
        CallOutcome.COMPENSATED,
    }

    for tool in expect.cancelled_tools:
        ok = any(r.outcome in cancelled_outcomes for r in by_tool.get(tool, []))
        checks.append(ok)
        if not ok:
            got = [r.outcome.value for r in by_tool.get(tool, [])]
            failures.append(f"{tool} should have been cancelled, outcomes were {got}")

    # Over-cancellation. Not named in the guide, but it destroys work and the
    # naive flush-everything agent fails exactly here.
    for tool in expect.survived_tools:
        records = by_tool.get(tool, [])
        ok = any(r.outcome is CallOutcome.COMPLETED_STILL_VALID for r in records)
        checks.append(ok)
        if not ok:
            got = [r.outcome.value for r in records]
            failures.append(f"{tool} was still valid and should have survived, outcomes were {got}")

    # "Absence of stale re-runs": nothing the agent answered with may be stale,
    # and no call may have been issued twice with identical arguments.
    stale_used = [
        r.call_id for r in registry
        if r.outcome is CallOutcome.COMPLETED_NOW_STALE
        and result.final is not None and r.call_id in result.final.grounded_on
    ]
    checks.append(not stale_used)
    if stale_used:
        failures.append(f"final response grounded in stale results: {stale_used}")

    # Identical calls issued more than once. A retry after a FAILED attempt is
    # not a re-run -- the effect never landed and the public suite names retries
    # explicitly -- so only calls that actually ran to an outcome count.
    # Likewise a cancelled call: re-issuing after the plan changed back is a
    # fresh decision, not a repeat.
    ran = (
        CallOutcome.COMPLETED_STILL_VALID,
        CallOutcome.COMPLETED_NOW_STALE,
        CallOutcome.PENDING,
    )
    signatures: dict[tuple, int] = {}
    for record in registry:
        if record.outcome not in ran:
            continue
        signatures[record.signature] = signatures.get(record.signature, 0) + 1
    repeats = [sig[0] for sig, n in signatures.items() if n > 1]
    checks.append(not repeats)
    if repeats:
        failures.append(f"identical calls re-issued: {sorted(set(repeats))}")

    # Every cancellation must name why, and the snapshot must have moved on.
    for record in registry:
        if record.outcome in cancelled_outcomes and not record.cancel_reason:
            failures.append(f"{record.call_id} cancelled without a recorded reason")
            checks.append(False)

    unresolved = registry.unresolved()
    checks.append(not unresolved)
    if unresolved:
        failures.append(f"effects left unresolved: {[r.call_id for r in unresolved]}")

    if not checks:
        notes.append("no interruption behaviour expected in this scenario")
        return Component("recovery", 1.0, WEIGHTS["recovery"], notes, failures)

    return Component("recovery", sum(checks) / len(checks), WEIGHTS["recovery"], notes, failures)


def _latency(result: RunResult) -> Component:
    """Time from each user turn (or interruption) to the next substantive action.

    "Substantive" excludes content-free fillers: a system that answers every
    prompt with "one moment" would otherwise score full marks on latency while
    failing objective 1's ban on excessive fillers.
    """
    # Turns where staying silent is the *correct* behaviour are excluded.
    # Answering "mhm" is not fast, it is rude — a backchannel is the user
    # signalling attention, and a self-repair is them still mid-sentence. Both
    # carry floor policy CONTINUE, meaning "keep doing what you were doing", so
    # measuring time-to-response on them would reward interrupting the user.
    #
    # This is a judgement call about an ambiguous rubric line, so it is narrow:
    # only CONTINUE turns are exempt. Barge-ins and corrections still demand a
    # response and are still measured.
    interpretations = sorted(
        (r.t, r.payload.get("policy", {}).get("floor"))
        for r in result.trace.named("interpretation", RecordKind.KERNEL)
    )

    def silence_is_correct(t: float) -> bool:
        """Did the turn beginning at or just after `t` warrant no reply?

        A bare VAD signal has no interpretation of its own — the words that
        follow it do. So a `interruption` event fired by someone saying "mhm"
        resolves to the backchannel interpretation a few milliseconds later,
        and excluding the chunk while still counting the signal would penalise
        exactly the behaviour we want.
        """
        return any(
            floor == "continue" and t - 1e-6 <= at <= t + 250.0
            for at, floor in interpretations
        )

    prompts: list[float] = [
        r.t for r in result.trace.of_kind(RecordKind.EVENT)
        if r.name in ("transcript_chunk", "interruption", "video_frame", "audio_clip")
        and not silence_is_correct(r.t)
    ]
    substantive: list[float] = sorted(
        r.t for r in result.trace.of_kind(RecordKind.ACTION)
        if r.name in ("speak", "clarify", "final_response")
        and r.payload.get("kind") != "filler"
    )

    if not prompts:
        return Component("latency", 1.0, WEIGHTS["latency"], ["no user input"], [])

    # The tolerance guards against an action emitted at the same instant as its
    # prompt being ordered before it by floating-point noise.
    gaps: list[float] = []
    for t in prompts:
        nxt = next((s for s in substantive if s >= t - 1e-6), None)
        gaps.append(LATENCY_ZERO_MS if nxt is None else max(0.0, nxt - t))

    scores = [
        1.0 if g <= LATENCY_TARGET_MS
        else max(0.0, 1.0 - (g - LATENCY_TARGET_MS) / (LATENCY_ZERO_MS - LATENCY_TARGET_MS))
        for g in gaps
    ]
    score = sum(scores) / len(scores)

    worst = max(gaps)
    notes = [
        f"median {sorted(gaps)[len(gaps) // 2]:.0f} ms, worst {worst:.0f} ms",
        "gaps " + ", ".join(f"{t:.0f}->{g:.0f}ms" for t, g in zip(prompts, gaps)),
    ]
    failures = []
    cap = result.scenario.expect.max_first_response_ms
    if cap is not None and gaps[0] > cap:
        failures.append(f"first response {gaps[0]:.0f} ms, expected within {cap:.0f} ms")

    return Component("latency", score, WEIGHTS["latency"], notes, failures)


def _safety(result: RunResult) -> Component:
    checks: list[bool] = []
    failures: list[str] = []

    duplicates = result.world.duplicates()
    checks.append(not duplicates)
    if duplicates:
        failures.append(f"duplicate state-changing effects: {sorted(duplicates)}")

    # Every state-modifying call must have carried an idempotency key.
    for record in result.agent.kernel.registry:
        if record.mutating and record.idempotency_key is None:
            checks.append(False)
            failures.append(f"{record.call_id} ({record.tool}) dispatched without an idempotency key")
    checks.append(True)

    # Every call must have settled, and settled in the trace.
    pending = [r.call_id for r in result.agent.kernel.registry if r.outcome is CallOutcome.PENDING]
    checks.append(not pending)
    if pending:
        failures.append(f"calls left pending: {pending}")

    settled = {r.payload["call_id"] for r in result.trace.named("call_settled", RecordKind.KERNEL)}
    settled |= {r.payload["call_id"] for r in result.trace.named("duplicate_suppressed", RecordKind.KERNEL)}
    settled |= {r.payload["call_id"] for r in result.trace.named("dispatch_rejected", RecordKind.KERNEL)}
    missing = [r.call_id for r in result.agent.kernel.registry if r.call_id not in settled]
    checks.append(not missing)
    if missing:
        failures.append(f"calls that never appear settled in the trace: {missing}")

    # Protocol compliance: the final response must carry a valid snapshot.
    if result.final is not None:
        from parley.protocol.state import StateSnapshot

        try:
            StateSnapshot.model_validate_json(result.final.state.model_dump_json())
            checks.append(True)
        except Exception as exc:  # noqa: BLE001
            checks.append(False)
            failures.append(f"final snapshot failed validation: {exc}")

    return Component("safety", sum(checks) / len(checks) if checks else 0.0,
                     WEIGHTS["safety"], [], failures)


def _multiplier(result: RunResult) -> tuple[float, list[str]]:
    """Naturalness, truthfulness, relevance — the 0.80x to 1.20x swing.

    Truthfulness dominates by design. Objective 1 forbids false completion
    claims and the multiplier rewards truthfulness, so an overstated filler is
    penalised in two places; this mirrors that.
    """
    notes: list[str] = []
    m = 1.0

    speaks = result.trace.named("speak", RecordKind.ACTION)
    fillers = [s for s in speaks if s.payload.get("kind") == "filler"]
    grounded = [s for s in speaks if s.payload.get("claims")]

    # Blocked speech means the gate caught an unwarranted claim before it was
    # uttered. Good that it was caught; still a sign of a sloppy call site.
    blocked = result.trace.named("speech_blocked", RecordKind.KERNEL)
    if blocked:
        m -= 0.02 * len(blocked)
        notes.append(f"-{0.02 * len(blocked):.2f} {len(blocked)} unwarranted utterance(s) blocked")

    if speaks:
        filler_ratio = len(fillers) / len(speaks)
        if filler_ratio > 0.34:
            m -= 0.10
            notes.append(f"-0.10 fillers are {filler_ratio:.0%} of speech")
        grounded_ratio = len(grounded) / len(speaks)
        if grounded_ratio >= 0.6:
            m += 0.10
            notes.append(f"+0.10 {grounded_ratio:.0%} of utterances carry warranted claims")
    else:
        m -= 0.15
        notes.append("-0.15 the agent never spoke")

    # Saying so when an effect may have happened and we cannot confirm it.
    if result.trace.named("disclosed_uncertain_effect", RecordKind.KERNEL) or \
       result.trace.named("disclosed_compensation", RecordKind.KERNEL):
        m += 0.05
        notes.append("+0.05 disclosed an effect the user could not otherwise know about")

    # An unresolved effect that was never mentioned is the silent lie.
    silent = [
        r for r in result.agent.kernel.registry
        if r.outcome.needs_resolution and r.resolution is None
    ]
    if silent:
        m -= 0.20
        notes.append(f"-0.20 {len(silent)} effect(s) left unresolved and unmentioned")

    if result.error:
        m -= 0.15
        notes.append(f"-0.15 run error: {result.error}")

    return max(0.80, min(1.20, m)), notes


def score(result: RunResult) -> ScoreCard:
    components = [
        _task_completion(result),
        _interruption_recovery(result),
        _latency(result),
        _safety(result),
    ]
    multiplier, notes = _multiplier(result)
    return ScoreCard(
        scenario_id=result.scenario.id,
        components=components,
        multiplier=multiplier,
        multiplier_notes=notes,
        error=result.error,
    )
