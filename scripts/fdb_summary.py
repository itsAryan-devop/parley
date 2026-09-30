#!/usr/bin/env python3
"""Per-clip table and latency breakdown for one FDB-v3 run.

    python scripts/fdb_summary.py runs/fdb/<run> third_party/Full-Duplex-Bench/v3/<results dir>

Reads the run's `pass_rate_report.json`, `parley_metrics.log` (LiveKit per-stage
metrics written by fdb/agent_parley.py) and each clip's `result_parley.json`.
Prints markdown; writes nothing. Numbers are copied, never recomputed into scores.
"""

from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def main(run_dir: Path, results_dir: Path, label: str = "parley") -> None:
    passed = {}
    report = run_dir / "pass_rate_report.json"
    if report.exists():
        for s in json.loads(report.read_text())["scenario_results"]:
            passed.setdefault(s["scenario_id"], []).append(s)

    metrics = defaultdict(lambda: defaultdict(list))
    for m in load_jsonl(run_dir / "parley_metrics.log"):
        metrics[m.get("room")][m.get("type")].append(m)

    print("| Clip | Tool calls | Strict | Latency (s) | EOU delay | LLM TTFT (sum) | TTS TTFB |")
    print("|---|---|---|---|---|---|---|")
    lat, eou_all, ttft_all, ttfb_all = [], [], [], []
    for d in sorted(p for p in results_dir.iterdir() if p.is_dir()):
        rf = d / f"result_{label}.json"
        if not rf.exists():
            continue
        r = json.loads(rf.read_text())
        calls = ", ".join(
            f"{c['function']}({', '.join(f'{v}' for v in c['args'].values() if v is not None)})"
            for c in r.get("actual_tool_calls", [])
        ) or "—"
        sid = r.get("example_id", d.name)
        verdicts = passed.get(sid, [])
        ok = "?"
        if verdicts:
            # scenario ids repeat across speakers; the report keeps order per id
            v = verdicts.pop(0)
            ok = "pass" if v["passed"] else f"FAIL: {v['failure_reason']}"
        pl = r.get("perceived_total_latency")
        if pl is not None:
            lat.append(pl)
        m = metrics.get(r.get("room_name"), {})
        eou = [x.get("end_of_utterance_delay", 0) for x in m.get("eou_metrics", [])]
        ttft = [x.get("ttft", 0) for x in m.get("llm_metrics", []) if x.get("ttft", -1) >= 0]
        ttfb = [x.get("ttfb", 0) for x in m.get("tts_metrics", []) if x.get("ttfb", -1) >= 0]
        eou_all += eou[:1]
        ttft_all.append(sum(ttft)) if ttft else None
        ttfb_all += ttfb[:1]
        f = lambda xs: f"{xs[0]:.2f}" if xs else "—"  # noqa: E731
        print(f"| {d.name[:28]} | {calls} | {ok} | {pl if pl is not None else '—'} "
              f"| {f(eou)} | {sum(ttft):.2f} ({len(ttft)} calls) | {f(ttfb)} |")

    def s(xs):
        return f"mean {st.mean(xs):.2f}, median {st.median(xs):.2f} (n={len(xs)})" if xs else "—"

    print()
    print(f"- perceived latency: {s(lat)}")
    print(f"- end-of-utterance delay (first turn): {s(eou_all)}")
    print(f"- LLM time-to-first-token, summed over a clip's LLM calls: {s(ttft_all)}")
    print(f"- TTS time-to-first-byte (first sentence): {s(ttfb_all)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), *(sys.argv[3:4]))
