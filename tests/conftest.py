"""Make the suite runnable from anywhere.

Several tests reach for `scenarios/` and `media/scenarios/` by relative path,
which works when pytest is invoked from the repository root and fails when it
is not. Running `pytest` from the repository root gets the right working
directory by accident; `pytest samsungprism/tests` from a parent directory does
not, and five tests fail for reasons that have nothing to do with the agent.

The chdir happens at **import** time rather than in a fixture, because
`load_all()` and the media-path constants run when the test modules are
imported — which is before any fixture, autouse or not, has had a chance to
run. A session fixture here is too late by exactly one step.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

# The repository root has to be importable, not merely the current directory.
#
# `python -m pytest` puts the working directory on `sys.path`; the bare `pytest`
# console script does not. `parley` and `harness` are unaffected either way
# because `pip install -e .` registers them, but `demo/` is deliberately not a
# distributed package -- and `tests/test_live.py` imports `demo.live`.
#
# So a clean checkout run with bare `pytest` died at *collection* with
# `ModuleNotFoundError: No module named 'demo'`, while the identical suite passed
# under `python -m pytest`. Anyone reproducing our numbers the first way would
# have seen the whole suite refuse to start rather than a single test fail.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
