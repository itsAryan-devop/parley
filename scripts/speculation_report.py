"""Does speculation actually pay for itself?

    python scripts/speculation_report.py

Speculation is one of the theme's named focus areas, and the literature says a
miss is free — a discarded read-only call and nothing else. "Free" is not the
same as "useful", though, and the honest question is how often a speculative
call is *joined* by the confirmation that follows it, and how much wall-clock
that actually saves.

This reads the traces rather than the code, so the answer is whatever the agent
really did.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import utf8

utf8()

from harness.runner import run_scenario
from harness.scenario import load_all
from harness.trace import RecordKind
from parley.agent.model import InterruptionModel


def main() -> int:
    model = InterruptionModel.load_default()
    spec_total = joined = superseded = still_spec = 0
    saved: list[float] = []
    per_scenario: list[tuple[str, int, int, float]] = []

    for scenario in load_all():
        result = run_scenario(scenario, model=model)

        speculative = [
            r for r in result.trace.named("tool_call", RecordKind.ACTION)
            if r.payload.get("speculative")
        ]
        joins = result.trace.named("speculation_join", RecordKind.KERNEL)
        supersedes = [
            r for r in result.trace.named("cancel", RecordKind.ACTION)
            if str(r.payload.get("reason", "")).startswith("superseded_by_")
        ]

        spec_total += len(speculative)
        joined += len(joins)
        superseded += len(supersedes)
        still_spec += sum(
            1 for c in result.agent.kernel.registry if c.speculative and c.outcome.had_effect
        )
        scenario_saved = [j.payload.get("saved_ms", 0.0) for j in joins]
        saved += scenario_saved

        if speculative:
            per_scenario.append((
                scenario.id, len(speculative), len(joins),
                sum(scenario_saved),
            ))

    print(f"{'scenario':<36} {'spec':>5} {'joined':>7} {'saved ms':>9}")
    print("-" * 60)
    for sid, n, j, ms in per_scenario:
        print(f"{sid:<36} {n:>5} {j:>7} {ms:>9.0f}")
    print("-" * 60)

    if not spec_total:
        print("no speculative calls were issued at all")
        return 0

    hit = joined / spec_total
    print(f"speculative calls issued : {spec_total}")
    print(f"  joined by confirmation : {joined}  ({hit:.0%} hit rate)")
    print(f"  superseded and cancelled: {superseded}")
    print(f"  completed while still speculative: {still_spec}")
    print(f"  total latency hidden    : {sum(saved):.0f} ms "
          f"(mean {sum(saved) / max(joined, 1):.0f} ms per join)")
    print()

    # The published comparison point: toolspec reports a useful win at a 39.3%
    # top-1 hit rate, because misses cost nothing.
    if hit >= 0.39:
        print(f"At {hit:.0%}, this beats the 39% hit rate published for n-gram-driven")
        print("speculation (toolspec) — expected, since speculating from bound slots")
        print("is a stronger signal than predicting the next tool in a sequence.")
    else:
        print(f"At {hit:.0%}, the hit rate is below the 39% published for n-gram-driven")
        print("speculation. Misses are free — a cancelled read-only call — so this is")
        print("not a regression, but it is not the win the design note claimed either.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
