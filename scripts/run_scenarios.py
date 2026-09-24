"""Run the public suite and print a scorecard.

    python scripts/run_scenarios.py                 # all scenarios
    python scripts/run_scenarios.py S02 S07         # by id prefix
    python scripts/run_scenarios.py --verbose S02   # plus the full action trace

Traces are written to `runs/<scenario>.jsonl` and can be opened in the timeline
viewer (`viz/`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import utf8

utf8()

from harness.runner import run_scenario
from harness.scenario import load_all
from harness.scoring import score
from parley.agent.model import InterruptionModel

RUNS = Path("runs")


def show_actions(result) -> None:
    print("    ┌─ actions " + "─" * 58)
    for a in result.actions:
        kind = a.type.value
        if kind == "speak":
            print(f"    │ {a.t:7.0f}  SPEAK[{a.kind.value}]  {a.text}")
        elif kind == "tool_call":
            tag = "SPEC" if a.speculative else "CALL"
            print(f"    │ {a.t:7.0f}  {tag}  {a.call_id} {a.tool}({_short(a.args)})")
        elif kind == "cancel":
            print(f"    │ {a.t:7.0f}  CANCEL {a.call_id}  ({a.reason})")
        elif kind == "clarify":
            print(f"    │ {a.t:7.0f}  ASK    {a.question}")
        elif kind == "final_response":
            print(f"    │ {a.t:7.0f}  FINAL  {a.text}")
    print("    └" + "─" * 68)


def _short(args: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in sorted(args.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ids", nargs="*", help="scenario id prefixes to run")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--no-model", action="store_true", help="rules only, no learned classifier")
    ap.add_argument("--json", type=Path, help="write the scorecards as JSON")
    args = ap.parse_args()

    RUNS.mkdir(exist_ok=True)
    model = None if args.no_model else InterruptionModel.load_default()

    scenarios = load_all()
    if args.ids:
        scenarios = [s for s in scenarios if any(s.id.startswith(p) for p in args.ids)]
    if not scenarios:
        print("no scenarios matched", file=sys.stderr)
        return 1

    cards = []
    print(f"{'scenario':<32} {'mode':<7} {'task':>6} {'recov':>6} {'lat':>6} {'safe':>6} "
          f"{'mult':>6} {'TOTAL':>7}")
    print("─" * 92)

    for scenario in scenarios:
        result = run_scenario(scenario, trace_path=RUNS / f"{scenario.id}.jsonl", model=model)
        card = score(result)
        cards.append(card)

        parts = {c.name: c for c in card.components}
        print(
            f"{scenario.id:<32} {scenario.modality:<7} "
            f"{parts['task'].score:>6.2f} {parts['recovery'].score:>6.2f} "
            f"{parts['latency'].score:>6.2f} {parts['safety'].score:>6.2f} "
            f"{card.multiplier:>6.2f} {card.total:>7.1f}"
        )

        for failure in card.failures:
            print(f"    ✗ {failure}")
        if card.error:
            print(f"    ! {card.error}")
        if args.verbose:
            for c in card.components:
                for note in c.notes:
                    print(f"    · {c.name}: {note}")
            for note in card.multiplier_notes:
                print(f"    · multiplier: {note}")
            show_actions(result)

    print("─" * 92)
    mean = sum(c.total for c in cards) / len(cards)
    clean = sum(1 for c in cards if not c.failures and not c.error)
    print(f"{'mean':<32} {'':<7} {'':>6} {'':>6} {'':>6} {'':>6} {'':>6} {mean:>7.1f}")
    print(f"{clean}/{len(cards)} scenarios with no failed checks")

    by_modality: dict[str, list[float]] = {}
    for scenario, card in zip(scenarios, cards):
        by_modality.setdefault(scenario.modality, []).append(card.total)
    print("\nby modality (hidden set applies 1.5x to multimodal):")
    for modality, totals in sorted(by_modality.items()):
        print(f"  {modality:<8} {len(totals):2d} scenarios   mean {sum(totals) / len(totals):6.1f}")

    if args.json:
        args.json.write_text(
            json.dumps([c.to_payload() for c in cards], indent=2), encoding="utf-8"
        )
        print(f"\nwrote {args.json}")

    return 0 if clean == len(cards) else 1


if __name__ == "__main__":
    raise SystemExit(main())
