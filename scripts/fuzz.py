"""Hammer the agent with perturbed timing and report invariant violations.

    python scripts/fuzz.py                  # 20 perturbations per scenario
    python scripts/fuzz.py --trials 200     # before submitting
    python scripts/fuzz.py --jitter 600 S02

The hidden set is ~60 scenarios of adversarial timing. Passing the fifteen we
wrote proves very little; holding every invariant across a few thousand
schedules we did not write proves rather more.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.fuzz import fuzz
from harness.scenario import load_all
from parley.agent.model import InterruptionModel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ids", nargs="*", help="scenario id prefixes")
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--jitter", type=float, default=250.0, help="± milliseconds")
    ap.add_argument("--no-model", action="store_true")
    args = ap.parse_args()

    scenarios = load_all()
    if args.ids:
        scenarios = [s for s in scenarios if any(s.id.startswith(p) for p in args.ids)]
    if not scenarios:
        print("no scenarios matched", file=sys.stderr)
        return 1

    model = None if args.no_model else InterruptionModel.load_default()
    total = len(scenarios) * args.trials
    print(f"fuzzing {len(scenarios)} scenarios × {args.trials} perturbations "
          f"(±{args.jitter:.0f} ms jitter) = {total} runs\n")

    done = 0

    def progress(scenario, p, violations):
        nonlocal done
        done += 1
        mark = "!" if violations else "."
        sys.stdout.write(mark)
        if done % 80 == 0:
            sys.stdout.write(f"  {done}/{total}\n")
        sys.stdout.flush()

    report = fuzz(
        scenarios, trials=args.trials, jitter_ms=args.jitter, model=model, on_run=progress
    )
    print(f"\n\n{report.summary()}")

    if not report.violations:
        print("\nno invariant broke under any schedule.")
        return 0

    kinds: Counter = Counter()
    for _, _, violations in report.violations:
        for v in violations:
            kinds[v.split(":")[0]] += 1

    print(f"\n{len(report.violations)} run(s) violated an invariant:\n")
    for kind, n in kinds.most_common():
        print(f"  {n:4d}  {kind}")

    print("\nfirst failures, reproducible from the seed:")
    for scenario_id, description, violations in report.violations[:10]:
        print(f"\n  {scenario_id}   {description}")
        for v in violations:
            print(f"      ✗ {v}")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
