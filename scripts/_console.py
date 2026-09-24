"""Make script output survive a non-UTF-8 console.

Windows still defaults many consoles to cp1252, where printing `→`, `±`, `✗` or
a box-drawing character raises `UnicodeEncodeError` and takes the whole script
down. Every reporting script here prints at least one of those, so on a judge's
default terminal `python scripts/run_scenarios.py` would traceback before
showing a single result — which reads as a broken submission rather than a
console setting.

Reconfiguring to UTF-8 with `errors="replace"` fixes the common case and
degrades the rest to `?` instead of crashing. Import for the side effect:

    from _console import utf8; utf8()
"""

from __future__ import annotations

import sys


def utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            # Redirected to something that cannot be reconfigured. Nothing to
            # do, and nothing worth failing over.
            pass
