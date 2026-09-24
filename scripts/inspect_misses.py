"""Print the held-out examples the rule engine gets wrong, with their features.

A rule miss is either something the model should cover, or a mislabelled
example. Telling those apart requires looking at them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import utf8

utf8()

from parley.agent.corpus import generate
from parley.agent.nlu import classify_by_rules, extract_features
from scripts.train_classifier import LEXICON

INTERESTING = ("filled_pause", "editing_term", "n_corrections", "n_additions",
               "n_restatements", "no_content", "overlapping", "intent_differs",
               "refinement_cue", "narrowing_addition")


def main() -> None:
    seen: set[str] = set()
    for ex in generate(800, split="eval"):
        feats, _, corr, add, rest, intent, _ = extract_features(
            ex.turn, ex.state, LEXICON, in_flight=ex.in_flight
        )
        kind, conf, why = classify_by_rules(feats)
        if kind is ex.label:
            continue
        key = f"{ex.label.value}->{kind.value}|{ex.template}"
        if key in seen:
            continue
        seen.add(key)

        print(f"\ntruth={ex.label.value}  rule={kind.value}  ({why})")
        print(f"  text     : {ex.turn.text!r}")
        print(f"  template : {ex.template!r}")
        print(f"  state    : intent={ex.state.intent} slots={ {k: v.value for k, v in ex.state.slots.items()} }")
        print(f"  corr={[(m.slot, m.value) for m in corr]} add={[(m.slot, m.value) for m in add]} "
              f"rest={[(m.slot, m.value) for m in rest]} intent_detected={intent}")
        print("  feats    : " + " ".join(f"{k}={feats[k]:g}" for k in INTERESTING if feats.get(k)))


if __name__ == "__main__":
    main()
