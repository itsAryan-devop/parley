"""Synthesise a spoken corpus for the ASR stability sweep.

    python scripts/make_speech.py

Writes 16 kHz mono WAV into `media/speech/`, with a JSON sidecar giving the
prompt text and the character offset where the repair begins.

Windows SAPI is the synthesiser because it is already on the box and needs no
download -- the same constraint the rest of the project runs under. The voices
are robotic, which is a *feature* here: if the stable-prefix rule survives
synthetic prosody with unnatural pauses around the interregnum, it will not be
surprised by a human saying the same sentence more smoothly.

This corpus measures recogniser *behaviour*, not accuracy. It is deliberately
not used to tune anything the agent decides.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import utf8

utf8()

OUT = Path("media/speech")

# Every line carries a mid-utterance correction, because that is the only kind
# of turn where a late revision can do damage: a word revised inside the
# reparandum changes which slot the agent believes was corrected.
LINES: list[tuple[str, str]] = [
    ("c01", "I need a flight from Chicago to Denver, uh, no sorry, to Dallas, on Friday."),
    ("c02", "Book me a flight to Delhi, no wait, Mumbai."),
    ("c03", "Find a hotel in Goa for Tuesday, actually make that Wednesday."),
    ("c04", "I want to fly to Miami, um, I mean Orlando."),
    ("c05", "Search flights to Pune on Monday, sorry, Tuesday morning."),
    ("c06", "Get me a room in Jaipur, er, Udaipur instead."),
    ("c07", "Book the six a.m. flight, no, make it the evening one."),
    ("c08", "I need two tickets, uh, three tickets to Bangalore."),
    ("c09", "Flight from Delhi to Chennai, hold on, from Mumbai to Chennai."),
    ("c10", "Reserve a hotel for four nights, sorry, for three nights."),
    ("c11", "Change my booking to Thursday, actually Friday works better."),
    ("c12", "Look up flights to Kolkata, um, cancel that, find me a train."),
    ("c13", "I'd like the window seat, no, the aisle seat please."),
    ("c14", "Send it to the Andheri office, uh, the Bandra office."),
    ("c15", "Book a cab for nine thirty, sorry, nine fifteen."),
    # Turns with no correction at all: the sweep must not reward a stability
    # window so long that it also delays ordinary speech.
    ("p01", "Find me a flight from Delhi to Mumbai on Tuesday morning."),
    ("p02", "Book a hotel in Goa for the weekend."),
    ("p03", "What flights are available to Hyderabad tomorrow?"),
    ("p04", "Reserve two seats on the morning flight to Chennai."),
    ("p05", "Show me hotels near the airport in Bangalore."),
]

PS_TEMPLATE = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
    "16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
    "[System.Speech.AudioFormat.AudioChannel]::Mono); "
    "$s.SetOutputToWaveFile('{path}', $f); "
    "$s.Rate = {rate}; "
    "$s.Speak([System.IO.File]::ReadAllText('{txt}')); "
    "$s.Dispose();"
)


def synth(text: str, path: Path, rate: int = 0) -> bool:
    """Speak `text` into a 16 kHz mono WAV. Returns whether it worked."""
    # The text goes via a file rather than inline: quoting a sentence containing
    # apostrophes and commas through PowerShell is a losing game, and one
    # mis-escaped line would silently synthesise the wrong words.
    txt = path.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    cmd = PS_TEMPLATE.format(
        path=str(path).replace("\\", "/"), txt=str(txt).replace("\\", "/"), rate=rate
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True,
    )
    txt.unlink(missing_ok=True)
    if proc.returncode != 0 or not path.exists():
        print(f"  ! {path.name}: {(proc.stderr or proc.stdout).strip()[:120]}")
        return False
    return True


def main() -> int:
    if sys.platform != "win32":
        print("SAPI is Windows-only; supply WAVs in media/speech/ by hand elsewhere.")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    made = 0
    for ident, text in LINES:
        path = OUT / f"{ident}.wav"
        # Two rates per line: a recogniser's revision behaviour is a function of
        # how fast the words arrive, so a corpus at one speaking rate would
        # measure one operating point and call it the answer.
        for suffix, rate in (("", 0), ("_fast", 3)):
            target = OUT / f"{ident}{suffix}.wav"
            if synth(text, target, rate):
                made += 1
                manifest.append({
                    "id": f"{ident}{suffix}",
                    "text": text,
                    "rate": rate,
                    "has_correction": ident.startswith("c"),
                    "path": str(target).replace("\\", "/"),
                })
        print(f"  {ident}  {text[:60]}")

    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"\nwrote {made} clips to {OUT}/")
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
