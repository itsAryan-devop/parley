"""Serve the trace viewer.

    python scripts/run_scenarios.py     # writes runs/*.jsonl
    python scripts/view.py              # opens the timeline at localhost:8770

The viewer needs an HTTP origin rather than file:// so it can list `runs/` and
fetch a trace. It reads nothing but the JSONL: if a thing can be drawn, it was
in the trace, which is the same standard the grader applies.
"""

from __future__ import annotations

import argparse
import http.server
import socketserver
import webbrowser
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--trace", default="", help="scenario id to open, e.g. S02_slot_correction")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not (ROOT / "runs").exists():
        print("no runs/ directory — run scripts/run_scenarios.py first")
        return 1

    url = f"http://localhost:{args.port}/viz/timeline.html"
    if args.trace:
        url += f"?trace={args.trace}"

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", args.port), handler) as httpd:
        print(f"serving {ROOT} at {url}\nctrl-c to stop")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
