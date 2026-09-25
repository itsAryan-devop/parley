"""Audio grounding: WAV in, a sound-class label out.

Audio is 30% of the hidden set at a 1.5× multiplier on the multimodal
scenarios. The transcript arrives separately as text chunks, so what the *raw
clip* adds is the part words cannot carry: the appliance is beeping, the drive
is grinding, the line is silent.

Classical DSP over numpy and the stdlib `wave` module — no model download, no
torch, nothing that would eat the 120 s cap. The features are the standard
spectral descriptors, chosen because they separate the classes that matter:

    periodic beeping     strong onset rate, tonal, high spectral flatness dip
    continuous tone      one onset, very low flatness, stable centroid
    broadband grinding   high flatness, high zero-crossing rate, no onsets
    clicking             many very short onsets, broadband, low duty cycle
    silence / hum        near-zero RMS

Ambiguity is handled the same way as vision: if the top two classes are within
the margin, we ask rather than guess.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np

from .perception import Perception, LabelModel, decide, payload_of

MODEL_PATH = Path(__file__).with_name("audio_model.json")

FEATURE_NAMES: list[str] = [
    "rms", "peak", "crest_factor", "zero_crossing_rate",
    "spectral_centroid", "spectral_rolloff", "spectral_flatness", "spectral_spread",
    "onset_rate", "duty_cycle", "envelope_std", "tonality",
    "band_low", "band_mid", "band_high",
]


def _read_wav(source: Any) -> tuple[np.ndarray, int] | None:
    """Decode a WAV to mono float32 in [-1, 1]."""
    try:
        if isinstance(source, (bytes, bytearray)):
            import io

            fh: Any = io.BytesIO(source)
        elif isinstance(source, (str, Path)):
            fh = str(source)
        else:
            return None

        with wave.open(fh, "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(frames)

        dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
        if dtype is None:
            return None
        data = np.frombuffer(raw, dtype=dtype).astype(np.float64)
        if width == 1:  # 8-bit WAV is unsigned, centred on 128
            data = (data - 128.0) / 128.0
        else:
            data /= float(np.iinfo(dtype).max)
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1)
        return data, rate
    except Exception:
        return None


def extract_features(source: Any) -> dict[str, float] | None:
    decoded = _read_wav(source)
    if decoded is None:
        return None
    x, rate = decoded
    if x.size < 64:
        return None

    feats: dict[str, float] = {}
    rms = float(np.sqrt(np.mean(x**2)))
    peak = float(np.max(np.abs(x)))
    feats["rms"] = rms
    feats["peak"] = peak
    feats["crest_factor"] = peak / (rms + 1e-9)
    feats["zero_crossing_rate"] = float(np.mean(np.abs(np.diff(np.signbit(x)))))

    # Magnitude spectrum of the whole clip.
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    freqs = np.fft.rfftfreq(x.size, 1.0 / rate)
    power = spec + 1e-12
    total = power.sum()

    centroid = float((freqs * power).sum() / total)
    feats["spectral_centroid"] = centroid / (rate / 2)
    cumulative = np.cumsum(power)
    rolloff_idx = int(np.searchsorted(cumulative, 0.85 * total))
    feats["spectral_rolloff"] = float(freqs[min(rolloff_idx, freqs.size - 1)]) / (rate / 2)
    # Flatness near 1 == noise-like, near 0 == tonal. The single best separator
    # between grinding and beeping.
    feats["spectral_flatness"] = float(np.exp(np.mean(np.log(power))) / (np.mean(power) + 1e-12))
    feats["spectral_spread"] = float(
        np.sqrt(((freqs - centroid) ** 2 * power).sum() / total)
    ) / (rate / 2)
    # How much of the energy sits in the single loudest bin: a pure tone
    # concentrates, a rattle does not.
    feats["tonality"] = float(power.max() / total)

    edges = [0, 500, 2000, rate / 2]
    for name, (lo, hi) in zip(("band_low", "band_mid", "band_high"), zip(edges[:-1], edges[1:])):
        band = (freqs >= lo) & (freqs < hi)
        feats[name] = float(power[band].sum() / total)

    # Envelope: frame the signal at ~20 ms and look at how the loudness moves.
    frame = max(int(rate * 0.02), 1)
    usable = (x.size // frame) * frame
    env = np.sqrt((x[:usable].reshape(-1, frame) ** 2).mean(axis=1)) if usable else np.array([rms])
    feats["envelope_std"] = float(env.std())

    if env.size > 1 and env.max() > 1e-6:
        active = env > (0.35 * env.max())
        feats["duty_cycle"] = float(active.mean())
        # Onsets are rising edges of the active mask -- a beep pattern has
        # several per second, a continuous tone has one.
        onsets = int(np.sum(active[1:] & ~active[:-1])) + int(active[0])
        duration = x.size / rate
        feats["onset_rate"] = onsets / max(duration, 1e-6)
    else:
        feats["duty_cycle"] = 0.0
        feats["onset_rate"] = 0.0

    return feats


_model: LabelModel | None | bool = False


def _get_model() -> LabelModel | None:
    global _model
    if _model is False:
        _model = LabelModel.load_or_none(MODEL_PATH)
    return _model  # type: ignore[return-value]


async def ground_audio(event: Any, *, clock: Any = None, slot: str = "sound") -> Perception:
    source = payload_of(event)
    ident = getattr(event, "clip_id", "clip")

    if clock is not None:
        await clock.sleep(110.0)

    features = extract_features(source)
    if features is None:
        return Perception(
            slot=slot, label=None, confidence=0.0, source_id=ident,
            modality="audio", error="clip could not be decoded",
        )

    model = _get_model()
    if model is None:
        return Perception(
            slot=slot, label=None, confidence=0.0, source_id=ident,
            modality="audio", features=features, error="no audio model available",
        )

    return decide(
        slot, model.probs(features),
        source_id=ident, modality="audio", features=features,
        phrase="the recording",
        out_of_distribution=model.is_out_of_distribution(features),
        ood_ratio=model.ood_ratio(features),
    )
