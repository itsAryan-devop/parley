"""Substitute the team name everywhere, in one command.

    python scripts/set_team_name.py --check
    python scripts/set_team_name.py ThaparPatiala_Parley

The guide's nomenclature is `CollegeName_TeamName`, and the placeholder
`ThaparPatiala_<TEAM>` appears in seven places across six files plus the deck's
filename and its embedded author/title metadata. Doing that by hand under
deadline pressure is how a tagged commit ends up with one stray placeholder in
the README a jury reads first -- and the deck warns twice that not following the
submission guideline "would lead to direct disqualification".

So it is a script. It is also deliberately conservative:

  * `--check` reports without writing, so the first run is never destructive.
  * The name is validated against the required `CollegeName_TeamName` shape
    before anything is touched.
  * Files are read and written as UTF-8 explicitly. On this machine PowerShell's
    default encoding mangles em-dashes into mojibake and `Out-File` adds a BOM
    that breaks `pytest.ini` -- both learned the hard way, see docs/BUILD_LOG.md.
  * The deck is renamed with `git mv` when the tree is a git repo, so history
    follows the file.
  * Nothing is done twice: a tree with no placeholders left reports that and
    exits 0, so re-running after a partial edit is safe.

It does not commit, and it does not tag. Those stay manual on purpose.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import utf8

utf8()

ROOT = Path(__file__).resolve().parents[1]

PLACEHOLDERS = ("ThaparPatiala_<TEAM>", "ThaparPatiala_TEAM")

#: Files carrying the placeholder. Listed explicitly rather than discovered by
#: walking the tree: a glob would also rewrite this script's own docstring and
#: SUBMISSION.md's instructions *about* the placeholder, which must keep saying
#: what they say.
TARGETS = (
    "README.md",
    "DISCLOSURE.md",
    "docs/DESIGN.md",
    "docs/BUILD_LOG.md",
    "deck/build_deck.js",
)

DECK_OLD = "deck/ThaparPatiala_TEAM_Submission_ppt.pptx"

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z][A-Za-z0-9]*$")


def occurrences() -> dict[str, int]:
    """Placeholder count per file, for the whole tree, excluding the noise."""
    found: dict[str, int] = {}
    skip = {".git", ".venv", "venv", "__pycache__", "node_modules", "models", "runs"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix in (".pptx", ".pyc", ".wav", ".png"):
            continue
        if any(part in skip for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n = sum(text.count(p) for p in PLACEHOLDERS)
        if n:
            found[str(path.relative_to(ROOT)).replace("\\", "/")] = n
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name", nargs="?", help="CollegeName_TeamName, e.g. ThaparPatiala_Parley")
    ap.add_argument("--check", action="store_true", help="report and change nothing")
    args = ap.parse_args()

    found = occurrences()

    if args.check or not args.name:
        if not found:
            print("no placeholders left — the team name has already been set")
            return 0
        print("placeholders still present:\n")
        for rel, n in sorted(found.items()):
            note = "  <- instructions, keep as-is" if rel in ("SUBMISSION.md", "scripts/set_team_name.py") else ""
            print(f"  {n:2d}  {rel}{note}")
        deck = ROOT / DECK_OLD
        print(f"\ndeck file: {'present, needs renaming' if deck.exists() else 'already renamed'}")
        if not args.name:
            print("\nre-run with the real name, e.g.:")
            print("    python scripts/set_team_name.py ThaparPatiala_Parley")
        return 0

    name = args.name.strip()
    if not NAME_RE.match(name):
        print(f"'{name}' is not CollegeName_TeamName: one underscore, letters and "
              "digits only, no spaces.", file=sys.stderr)
        print("The guide calls this exact nomenclature a disqualification risk.", file=sys.stderr)
        return 1

    print(f"setting team name to: {name}\n")
    touched = 0
    for rel in TARGETS:
        path = ROOT / rel
        if not path.exists():
            print(f"  !  {rel} missing, skipped")
            continue
        text = path.read_text(encoding="utf-8")
        original = text
        for placeholder in PLACEHOLDERS:
            text = text.replace(placeholder, name)
        if text != original:
            # Explicit UTF-8, no BOM. See the module docstring.
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"  ok {rel}")
            touched += 1
        else:
            print(f"  -  {rel} (nothing to change)")

    # The deck filename carries the team name too, and the jury sees it before
    # they open anything.
    old = ROOT / DECK_OLD
    new = ROOT / "deck" / f"{name}_Submission_ppt.pptx"
    if old.exists():
        try:
            subprocess.run(["git", "mv", str(old), str(new)], cwd=ROOT, check=True,
                           capture_output=True, text=True)
            print(f"  ok git mv -> deck/{new.name}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            old.rename(new)
            print(f"  ok renamed -> deck/{new.name}  (not a git repo, or git mv failed)")
        touched += 1
    elif new.exists():
        print(f"  -  deck/{new.name} already named correctly")

    print(f"\n{touched} change(s).")
    print("\nNow, in order:")
    print("  1. node deck/build_deck.js      # rebuild so the slides carry the new name")
    print("  2. python scripts/set_team_name.py --check")
    print("  3. review the diff, then commit")
    print("\nSUBMISSION.md keeps the placeholder on purpose: it is the instructions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
