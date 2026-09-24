"""Make the suite runnable from anywhere.

Several tests reach for `scenarios/` and `media/scenarios/` by relative path,
which works when pytest is invoked from the repository root and fails when it
is not. A judge running `docker run --rm parley pytest` gets the right working
directory by accident; `pytest samsungprism/tests` from a parent directory does
not, and five tests fail for reasons that have nothing to do with the agent.

The chdir happens at **import** time rather than in a fixture, because
`load_all()` and the media-path constants run when the test modules are
imported — which is before any fixture, autouse or not, has had a chance to
run. A session fixture here is too late by exactly one step.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
