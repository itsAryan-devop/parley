"""Generate the frames and clips the visual/audio scenarios use.

Run offline:

    python scripts/make_media.py

Two jobs. It writes the specific media the public scenarios reference, and it
writes labelled train/test sets for `train_perception.py`.

Everything is synthesised rather than scraped: no copyright question, no
licensing question, and full control over the *hard* cases. That last part
matters more than it sounds — the interesting scenario is not "classify a red
LED", it is "this frame is genuinely between red and amber, so ask". A
synthetic generator can dial the hue to sit exactly on that boundary, which a
folder of downloaded photos cannot.

Real device photos slot straight into the same pipeline if they arrive: the
feature extractor is scale-invariant and takes any PNG. See README.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image, ImageDraw

RATE = 16_000

FRAME_LABELS = ["router_power_led_red", "router_wan_led_amber", "washer_error_e4", "tv_hdmi_no_signal"]
AUDIO_LABELS = ["beeping", "continuous_tone", "grinding", "clicking", "silence"]


# --------------------------------------------------------------------- frames

def _noise(img: Image.Image, rng: random.Random, amount: float) -> Image.Image:
    """Sensor noise, so the classifier cannot rely on perfectly flat regions."""
    arr = np.asarray(img, dtype=float)
    arr += np.random.default_rng(rng.randrange(2**32)).normal(0, amount * 255, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def render_frame(label: str, rng: random.Random, *, size: int = 320, mode: str = "normal") -> Image.Image:
    """One synthetic device frame.

    `mode` produces the genuinely undecidable cases, which are the point of the
    visual scenarios:

        both_lit    two indicators lit at once -> the evidence really is split,
                    so the honest answer is to ask WHICH ONE
        washed_out  overexposed to the point of carrying no colour -> unreadable

    These are deliberately *not* in the training set. Teaching a classifier to
    pick one label for an image with two lit LEDs would train away the exact
    behaviour objective 5 rewards.
    """
    bg = rng.randint(18, 42)
    img = Image.new("RGB", (size, size), (bg, bg, bg + rng.randint(0, 6)))
    d = ImageDraw.Draw(img)

    if label in ("router_power_led_red", "router_wan_led_amber"):
        # Router body: a dark slab with a row of small indicator LEDs.
        body = rng.randint(30, 55)
        d.rounded_rectangle(
            [size * 0.1, size * 0.35, size * 0.9, size * 0.68], radius=12,
            fill=(body, body, body + 4),
        )
        red_hue = rng.uniform(352.0, 368.0) % 360
        amber_hue = rng.uniform(28.0, 44.0)
        if mode == "both_lit":
            lit = {0: red_hue, 2: amber_hue}
        elif label.endswith("red"):
            lit = {0: red_hue}
        else:
            lit = {2: amber_hue}

        for i in range(4):
            cx = size * (0.22 + 0.18 * i)
            cy = size * 0.5
            r = size * rng.uniform(0.020, 0.030)
            if i in lit:
                colour = _hsv(lit[i], rng.uniform(0.85, 1.0), rng.uniform(0.85, 1.0))
                # Glow halo, as a real photographed LED would have.
                d.ellipse([cx - r * 2.2, cy - r * 2.2, cx + r * 2.2, cy + r * 2.2],
                          fill=_blend(colour, (body, body, body), 0.75))
            else:
                colour = (body + 8, body + 8, body + 10)  # unlit
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)

    elif label == "washer_error_e4":
        # Appliance panel: a bright alphanumeric display on a dark facia.
        d.rounded_rectangle([size * 0.12, size * 0.3, size * 0.88, size * 0.7],
                            radius=8, fill=(26, 26, 30))
        d.rectangle([size * 0.3, size * 0.38, size * 0.7, size * 0.62], fill=(8, 10, 8))
        code = rng.choice(["E4", "E 4", "4E"])
        for dx in range(rng.randint(2, 4)):  # fake 7-segment thickness
            d.text((size * 0.38 + dx, size * 0.45), code, fill=(40, 255, 90))
        for i in range(rng.randint(3, 6)):  # control text below
            y = size * (0.74 + 0.04 * i)
            d.line([size * 0.2, y, size * rng.uniform(0.4, 0.8), y], fill=(90, 90, 95), width=2)

    elif label == "tv_hdmi_no_signal":
        # A dark blue screen with a message box: almost no saturated colour mass.
        d.rectangle([0, 0, size, size], fill=(rng.randint(8, 20), rng.randint(10, 24), rng.randint(45, 80)))
        d.rounded_rectangle([size * 0.2, size * 0.4, size * 0.8, size * 0.6],
                            radius=6, fill=(20, 24, 70), outline=(150, 155, 185), width=2)
        for i in range(rng.randint(2, 3)):
            y = size * (0.45 + 0.06 * i)
            d.line([size * 0.26, y, size * rng.uniform(0.45, 0.74), y], fill=(190, 195, 215), width=3)

    if mode == "washed_out":
        # Blown-out exposure: the colour information is simply gone.
        arr = np.asarray(img, dtype=float)
        arr = arr * 0.25 + 200.0
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        return _noise(img, rng, 0.03)

    return _noise(img, rng, rng.uniform(0.004, 0.02))


def _hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
    c = v * s
    x = c * (1 - abs(((h / 60.0) % 2) - 1))
    m = v - c
    table = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)]
    r, g, b = table[int(h // 60) % 6]
    return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))  # type: ignore[return-value]


# ---------------------------------------------------------------------- audio

def render_audio(label: str, rng: random.Random, *, seconds: float = 1.6, mix_with: str | None = None) -> np.ndarray:
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    gen = np.random.default_rng(rng.randrange(2**32))

    if label == "beeping":
        freq = rng.uniform(1800, 2900)
        period = rng.uniform(0.28, 0.45)
        duty = rng.uniform(0.3, 0.5)
        gate = ((t % period) < period * duty).astype(float)
        x = np.sin(2 * math.pi * freq * t) * gate

    elif label == "continuous_tone":
        freq = rng.uniform(380, 1100)
        x = np.sin(2 * math.pi * freq * t)
        x += 0.15 * np.sin(2 * math.pi * 2 * freq * t)  # a little harmonic colour

    elif label == "grinding":
        x = gen.normal(0, 1, n)
        # One-pole low-pass: mechanical noise is weighted low, unlike hiss.
        alpha = rng.uniform(0.55, 0.85)
        for _ in range(2):
            x = np.concatenate([[x[0]], alpha * x[:-1] + (1 - alpha) * x[1:]])
        x *= 1 + 0.3 * np.sin(2 * math.pi * rng.uniform(8, 20) * t)  # rotational wobble

    elif label == "clicking":
        x = np.zeros(n)
        for _ in range(rng.randint(6, 14)):
            start = rng.randrange(0, max(n - 400, 1))
            width = rng.randint(60, 220)
            click = gen.normal(0, 1, width) * np.exp(-np.linspace(0, 6, width))
            x[start:start + width] += click

    else:  # silence -- room tone, not digital zero
        x = gen.normal(0, rng.uniform(0.0006, 0.004), n)

    if mix_with is not None:
        # An even mixture of two classes. Not "a noisy beep" -- genuinely both,
        # so no single label is the right answer and the agent should ask.
        other = render_audio(mix_with, rng, seconds=seconds)
        x = 0.5 * x / (np.abs(x).max() + 1e-9) + 0.5 * other / (np.abs(other).max() + 1e-9)

    x += gen.normal(0, 0.006, n)
    peak = np.abs(x).max()
    if peak > 1e-9:
        x = x / peak * rng.uniform(0.3, 0.85)
    return x


def write_wav(path: Path, x: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


# --------------------------------------------------------------------- driver

def build_dataset(root: Path, n_per_class: int, seed: int, split: str) -> None:
    """Clean, single-label training and test data. No ambiguous cases here."""
    rng = random.Random(f"{seed}:{split}")
    for label in FRAME_LABELS:
        for i in range(n_per_class):
            out = root / "frames" / split / label / f"{i:03d}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            render_frame(label, rng).save(out)
    for label in AUDIO_LABELS:
        for i in range(n_per_class):
            write_wav(root / "audio" / split / label / f"{i:03d}.wav", render_audio(label, rng))


def build_undecidable(root: Path, n: int = 12) -> None:
    """Media with genuinely split or absent evidence.

    Held out of training on purpose. These exist to check that the agent asks a
    specific question instead of picking a label, which is what objective 5
    rewards and what a confidently-wrong perception loses twice over.
    """
    rng = random.Random(90210)
    for i in range(n):
        out = root / "frames" / "both_lit" / f"{i:03d}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        render_frame("router_power_led_red", rng, mode="both_lit").save(out)

        out = root / "frames" / "washed_out" / f"{i:03d}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        render_frame(rng.choice(FRAME_LABELS), rng, mode="washed_out").save(out)

        write_wav(root / "audio" / "beep_click_mix" / f"{i:03d}.wav",
                  render_audio("beeping", rng, mix_with="clicking"))


def build_scenario_media(root: Path) -> None:
    """The exact files the public scenarios reference."""
    rng = random.Random(4242)
    frames = root / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for label in FRAME_LABELS:
        render_frame(label, rng).save(frames / f"{label}.png")
    render_frame("router_power_led_red", rng, mode="both_lit").save(frames / "router_led_ambiguous.png")
    render_frame("washer_error_e4", rng, mode="washed_out").save(frames / "washer_unreadable.png")

    audio = root / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    for label in AUDIO_LABELS:
        write_wav(audio / f"{label}.wav", render_audio(label, rng))
    write_wav(audio / "sound_ambiguous.wav", render_audio("beeping", rng, mix_with="clicking"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("media"))
    ap.add_argument("--n", type=int, default=60, help="examples per class per split")
    args = ap.parse_args()

    build_scenario_media(args.out / "scenarios")
    build_dataset(args.out / "dataset", args.n, seed=20260924, split="train")
    build_dataset(args.out / "dataset", max(args.n // 3, 10), seed=777, split="test")
    build_undecidable(args.out / "undecidable")

    n_png = len(list((args.out).rglob("*.png")))
    n_wav = len(list((args.out).rglob("*.wav")))
    print(f"wrote {n_png} frames and {n_wav} clips under {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
