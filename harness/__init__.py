"""A spec-faithful replica of the Theme 5 evaluation harness.

The official kit had not been released at the time of writing, so this is built
from the guide's description of it: virtual clock streaming harness, deterministic
mock tools with latency and fault injection, full event/action trace logging, and
a scorer implementing the published rubric.

The agent talks to `harness.runner` through an adapter boundary, so replacing
this with the real kit is a boundary change rather than a rewrite.
"""

from .clock import Clock, VirtualTimeLoop, run_virtual

__all__ = ["Clock", "VirtualTimeLoop", "run_virtual"]
