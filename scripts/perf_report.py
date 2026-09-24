"""Wall-clock budget: cold start, per-scenario cost, and headroom.

    python scripts/perf_report.py

The guide gives a 300 s setup/warm-up hook and a **120 s wall-clock cap per
scenario**. Virtual time makes tool latency free, so the only real cost is
Python: imports, model loads, and the agent's own work. That has never been
measured here, and "it feels fast" is not a number.

Cold start is measured in a *subprocess*, because measuring it in-process after
everything is already imported measures nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import utf8

utf8()

CAP_MS = 120_000.0
WARMUP_BUDGET_S = 300.0


def cold_start() -> dict[str, float]:
    """Import cost and first-model-load cost, in a fresh interpreter."""
    probe = """
import json, time
t0 = time.perf_counter()
from harness.runner import run_scenario
from harness.scenario import load_all
t_import = time.perf_counter() - t0

t0 = time.perf_counter()
from parley.agent.model import InterruptionModel
from parley.multimodal import vision, audio
assert InterruptionModel.load_default() is not None
assert vision._get_model() is not None and audio._get_model() is not None
t_models = time.perf_counter() - t0

t0 = time.perf_counter()
r = run_scenario(load_all()[0])
t_first = time.perf_counter() - t0
assert r.error is None, r.error

print(json.dumps({"import": t_import, "models": t_models, "first_scenario": t_first}))
"""
    out = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True
    )
    if out.returncode != 0:
        raise SystemExit(f"cold-start probe failed:\n{out.stderr}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    from harness.runner import run_scenario
    from harness.scenario import load_all
    from parley.agent.model import InterruptionModel

    print("measuring cold start in a fresh interpreter…")
    cold = cold_start()
    print(f"  imports                 {cold['import'] * 1000:8.0f} ms")
    print(f"  model loads             {cold['models'] * 1000:8.0f} ms")
    print(f"  first scenario (cold)   {cold['first_scenario'] * 1000:8.0f} ms")
    total_warm = sum(cold.values())
    print(f"  total to first answer   {total_warm * 1000:8.0f} ms "
          f"({total_warm / WARMUP_BUDGET_S:.2%} of the 300 s warm-up hook)")
    print()

    model = InterruptionModel.load_default()
    scenarios = load_all()

    print(f"{'scenario':<36} {'wall ms':>9} {'virtual ms':>11} {'ratio':>8}")
    print("-" * 68)
    rows = []
    for scenario in scenarios:
        start = time.perf_counter()
        result = run_scenario(scenario, model=model)
        wall_ms = (time.perf_counter() - start) * 1000
        virtual_ms = max((r.t for r in result.trace), default=0.0)
        rows.append((scenario.id, wall_ms, virtual_ms))
        print(f"{scenario.id:<36} {wall_ms:>9.1f} {virtual_ms:>11.0f} "
              f"{virtual_ms / max(wall_ms, 1e-9):>7.0f}x")

    print("-" * 68)
    worst_wall = max(r[1] for r in rows)
    total_wall = sum(r[1] for r in rows)
    worst_virtual = max(r[2] for r in rows)

    print(f"worst scenario          {worst_wall:8.1f} ms wall  "
          f"({worst_wall / CAP_MS:.3%} of the 120 s cap)")
    print(f"whole suite             {total_wall:8.1f} ms wall for "
          f"{len(rows)} scenarios")
    print(f"worst virtual timeline  {worst_virtual:8.0f} ms  "
          f"({worst_virtual / CAP_MS:.1%} of the cap)")
    print()
    print(f"headroom: the slowest scenario could get "
          f"{CAP_MS / max(worst_wall, 1e-9):.0f}x slower and still fit.")

    if worst_wall > CAP_MS * 0.5:
        print("\nWARNING: a scenario is using over half the wall-clock cap.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
