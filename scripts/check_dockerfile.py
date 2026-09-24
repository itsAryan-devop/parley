"""Validate the Dockerfile without a daemon.

    python scripts/check_dockerfile.py

A Docker build is the last thing anyone runs before submitting and the first
thing a judge runs after. The failures that bite are dull: a COPY path that was
renamed, an inline `python -c` guard with a typo, a dependency listed in
pyproject but not in the pip install line. All three are checkable statically,
and none of them need the daemon to be up.

This does not replace `docker build`. It catches the mistakes that would make
`docker build` fail five minutes in.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"


def fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def main() -> int:
    if not DOCKERFILE.exists():
        print("no Dockerfile")
        return 1

    text = DOCKERFILE.read_text(encoding="utf-8")
    # Join line continuations so multi-line RUN blocks parse as one instruction.
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    problems = 0

    print("COPY sources exist:")
    for line in joined.splitlines():
        m = re.match(r"\s*COPY\s+(.+)$", line)
        if not m:
            continue
        parts = m.group(1).split()
        for src in parts[:-1]:
            if src.startswith("--"):
                continue
            path = ROOT / src.rstrip("/")
            if path.exists():
                ok(f"COPY {src}")
            else:
                fail(f"COPY {src} — no such path")
                problems += 1

    print("\npython version band matches the guide (3.10-3.12):")
    m = re.search(r"FROM\s+python:(\d+)\.(\d+)", joined)
    if not m:
        fail("no pinned python base image")
        problems += 1
    else:
        major, minor = int(m.group(1)), int(m.group(2))
        if (major, minor) >= (3, 10) and (major, minor) <= (3, 12):
            ok(f"python:{major}.{minor}")
        else:
            fail(f"python:{major}.{minor} is outside 3.10-3.12")
            problems += 1

    print("\nruntime dependencies are all installed:")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r"^dependencies\s*=\s*\[(.*?)\]", pyproject, re.S | re.M)
    names = re.findall(r'"([A-Za-z0-9_.-]+)', declared.group(1)) if declared else []
    installed = joined.lower()
    for name in names:
        if name.lower() in installed:
            ok(name)
        else:
            fail(f"{name} is a runtime dependency but never installed in the image")
            problems += 1

    print("\ncommitted model weights are present:")
    for rel in ("parley/agent/interruption_model.json",
                "parley/multimodal/vision_model.json",
                "parley/multimodal/audio_model.json"):
        if (ROOT / rel).exists():
            ok(rel)
        else:
            fail(f"{rel} missing — the image would degrade silently")
            problems += 1

    print("\ninline RUN python guards actually execute:")
    for m in re.finditer(r'RUN python -c "(.+?)"', joined, re.S):
        snippet = m.group(1).replace('\\\n', '').strip()
        proc = subprocess.run(
            [sys.executable, "-c", snippet], cwd=ROOT, capture_output=True, text=True
        )
        first = snippet.split(";")[0][:58]
        if proc.returncode == 0:
            ok(f"{first}…  → {proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else 'ok'}")
        else:
            fail(f"{first}…  → {(proc.stderr or proc.stdout).strip().splitlines()[-1]}")
            problems += 1

    print()
    if problems:
        print(f"{problems} problem(s). `docker build` would fail.")
        return 1
    print("Dockerfile is statically sound. Run `docker build -t parley .` to confirm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
