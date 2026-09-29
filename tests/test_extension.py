"""The camera troubleshooting extension, offline: no LiveKit, no network.

Frames are the committed ones in media/scenarios/frames/. What is locked down:
a clear frame is diagnosed with the manual's steps; an ambiguous one produces a
question and no label (ask, don't guess); an unreadable one asks for a retake;
the same frame is never diagnosed twice; and per-session state holds one frame.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from parley.extension import FrameDiagnoser, LatestFrame, diagnose_latest, rgba_to_png
from parley.extension import camera

FRAMES = Path(__file__).resolve().parents[1] / "media" / "scenarios" / "frames"


def _png(name: str) -> bytes:
    return (FRAMES / name).read_bytes()


def _rgba(name: str) -> tuple[bytes, int, int]:
    img = Image.open(FRAMES / name).convert("RGBA")
    return img.tobytes(), img.width, img.height


@pytest.mark.parametrize("name,label", [
    ("router_power_led_red.png", "router_power_led_red"),
    ("router_wan_led_amber.png", "router_wan_led_amber"),
    ("tv_hdmi_no_signal.png", "tv_hdmi_no_signal"),
    ("washer_error_e4.png", "washer_error_e4"),
])
async def test_clear_frame_is_diagnosed_with_manual_steps(name: str, label: str) -> None:
    result, executed = await FrameDiagnoser().diagnose(_png(name))
    assert executed
    assert result["status"] == "diagnosed"
    assert result["label"] == label
    assert result["manual"]["steps"], "a diagnosis must carry grounded steps"


async def test_ambiguous_frame_asks_and_names_no_label() -> None:
    result, _ = await FrameDiagnoser().diagnose(_png("router_led_ambiguous.png"))
    assert result["status"] == "ask"
    assert "label" not in result
    assert "?" in result["question"]
    assert set(result["candidates"]) == {"router_power_led_red", "router_wan_led_amber"}
    # Steps for each rival are ready for after the user answers.
    assert set(result["manual_if_confirmed"]) == set(result["candidates"])


async def test_unreadable_frame_asks_for_a_retake() -> None:
    result, _ = await FrameDiagnoser().diagnose(_png("washer_unreadable.png"))
    assert result["status"] == "retake"
    assert "label" not in result
    assert result["question"].endswith("?")


async def test_same_frame_is_never_diagnosed_twice(monkeypatch) -> None:
    calls = []
    real = camera._ground
    monkeypatch.setattr(camera, "_ground", lambda png, fid: calls.append(fid) or real(png, fid))

    diagnoser = FrameDiagnoser()
    first, ran1 = await diagnoser.diagnose(_png("tv_hdmi_no_signal.png"))
    second, ran2 = await diagnoser.diagnose(_png("tv_hdmi_no_signal.png"))
    other, ran3 = await diagnoser.diagnose(_png("router_wan_led_amber.png"))

    assert (ran1, ran2, ran3) == (True, False, True)
    assert len(calls) == 2
    assert second["label"] == first["label"] and "repeated" in second
    assert other["label"] == "router_wan_led_amber"


async def test_dedup_is_per_session() -> None:
    png = _png("tv_hdmi_no_signal.png")
    _, ran_a = await FrameDiagnoser().diagnose(png)
    _, ran_b = await FrameDiagnoser().diagnose(png)
    assert ran_a and ran_b, "a new session must not inherit another's results"


async def test_diagnose_latest_uses_the_newest_frame_only() -> None:
    latest, diagnoser = LatestFrame(), FrameDiagnoser()
    assert (await diagnose_latest(latest, diagnoser))["status"] == "no_frame"

    latest.set(*_rgba("router_power_led_red.png"))
    latest.set(*_rgba("tv_hdmi_no_signal.png"))
    result = await diagnose_latest(latest, diagnoser)
    assert result["label"] == "tv_hdmi_no_signal"

    latest.clear()
    assert latest.get() is None
    assert (await diagnose_latest(latest, diagnoser))["status"] == "no_frame"


def test_tts_proxy_pushes_each_wav_whole() -> None:
    """The workaround for livekit-agents 1.3.12 cutting Groq audio mid-file."""
    pytest.importorskip("livekit.agents")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "agent_extension", Path(__file__).resolve().parents[1] / "fdb" / "agent_extension.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Emitter:
        def __init__(self):
            self.events = []

        def initialize(self, **kw):
            self.events.append(("init", kw["mime_type"]))

        def push(self, data):
            self.events.append(("push", data))

        def flush(self):
            self.events.append(("flush",))

    inner = Emitter()
    proxy = mod._WholeWav(inner)
    proxy.initialize(mime_type="audio/wav")
    for chunk in (b"RIFF", b"....WAVE", b"pcm"):
        proxy.push(chunk)
    proxy.flush()
    assert inner.events == [("init", "audio/wav"), ("push", b"RIFF....WAVEpcm"), ("flush",)]


def test_rgba_to_png_round_trips_and_caps_width() -> None:
    rgba = np.zeros((720, 1280, 4), dtype=np.uint8)
    rgba[..., 0] = 200
    rgba[..., 3] = 255
    png = rgba_to_png(rgba.tobytes(), 1280, 720)
    img = Image.open(__import__("io").BytesIO(png))
    assert img.size == (camera.MAX_WIDTH, 540)
    assert img.getpixel((10, 10))[0] == 200
