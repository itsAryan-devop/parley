"""The live demo server: microphone in, agent out, timeline on screen.

    pip install -e ".[demo]"
    python demo/server.py            # then open http://127.0.0.1:8770

One websocket per browser tab, one `LiveSession` per websocket. Binary frames
are 16 kHz mono PCM straight from the microphone; text frames are JSON control
messages. Nothing is fetched from the network: the acoustic model is vendored,
the page is served from disk, and speech synthesis is the browser's own.

**Why the recogniser runs in a thread.** `AcceptWaveform` is a blocking Kaldi
call costing tens of milliseconds per frame. Awaiting it inline would park the
event loop for that whole time, which is precisely the window in which an
interruption is supposed to be noticed and a tool call cancelled. The one thing
this demo exists to show is the agent reacting *while* work is in flight, so the
decoder is pushed to a worker thread and the loop stays free to do the
cancelling. A demo whose architecture contradicts its own thesis is worse than
no demo.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import utf8

utf8()

from demo.live import DEMO_LATENCY_MS, LiveSession
from parley.agent.model import InterruptionModel

HERE = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8771
"""Not 8770: `scripts/view.py` already serves the trace viewer there, and the
demo script has both running side by side during a recording. Two servers
fighting over one port mid-take is a bad way to find that out."""

SAMPLE_RATE = 16_000


def _load_asr():
    """Import and warm the recogniser, or explain why we cannot."""
    try:
        from parley.multimodal import asr
    except ImportError as exc:  # pragma: no cover
        return None, f"{exc}"
    try:
        ms = asr.warmup()
    except Exception as exc:  # noqa: BLE001 - ASRUnavailable, or no model on disk
        return None, f"{exc}"
    print(f"  acoustic model warm in {ms:.0f} ms")
    return asr, None


class Connection:
    """Bridges one websocket to one agent session."""

    def __init__(self, ws: Any, asr_mod: Any, model: InterruptionModel | None) -> None:
        self.ws = ws
        self.asr_mod = asr_mod
        self.model = model
        self.session: LiveSession | None = None
        self.recogniser: Any = None
        self._outbox: asyncio.Queue = asyncio.Queue()
        self._seq = 0

    # -- outbound ----------------------------------------------------------

    def emit(self, payload: dict[str, Any]) -> None:
        """Called from agent callbacks, which are synchronous. Queue, never await."""
        self._seq += 1
        payload = {**payload, "seq": self._seq}
        self._outbox.put_nowait(payload)

    async def _pump(self) -> None:
        while True:
            payload = await self._outbox.get()
            if payload is None:
                return
            try:
                await self.ws.send(json.dumps(payload))
            except Exception:  # noqa: BLE001 - browser closed the tab
                return

    # -- inbound -----------------------------------------------------------

    async def handle(self) -> None:
        pump = asyncio.create_task(self._pump(), name="pump")
        try:
            async for message in self.ws:
                if isinstance(message, bytes):
                    await self._on_audio(message)
                else:
                    await self._on_control(json.loads(message))
        finally:
            if self.session is not None:
                await self.session.finish("socket closed")
            self._outbox.put_nowait(None)
            await asyncio.gather(pump, return_exceptions=True)

    async def _on_control(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")

        if kind == "start":
            await self._start(msg)

        elif kind == "text":
            # Typed input. The agent cannot distinguish this from a spoken chunk,
            # which is the point -- and it keeps the demo working on a laptop
            # with no working microphone, or a noisy room.
            if self.session is not None:
                self.session.feed({
                    "type": "transcript_chunk",
                    "text": msg.get("text", ""),
                    "end_of_turn": bool(msg.get("end_of_turn", True)),
                })

        elif kind == "interruption":
            # Browser-side VAD heard the user over our speech. Carries no
            # meaning: the agent classifies it once the words arrive.
            if self.session is not None:
                self.session.feed({"type": "interruption", "source": "vad"})

        elif kind in ("frame", "clip"):
            # Inline media, as a data: URI straight from the browser's
            # FileReader. This is the `data_b64` wire path -- the one that was
            # silently broken until `perception.payload_of` was written, because
            # a base64 string is a valid argument to Image.open and an invalid
            # filename. Worth knowing the demo exercises it: the alternative
            # path (media on disk) is the one all 29 scenarios use, so this is
            # the only place the inline encoding gets used in anger.
            if self.session is None:
                return
            blob = msg.get("data") or ""
            ident = msg.get("id") or ("frame" if kind == "frame" else "clip")
            if kind == "frame":
                self.session.feed({
                    "type": "video_frame", "frame_id": ident, "data_b64": blob,
                })
            else:
                self.session.feed({
                    "type": "audio_clip", "clip_id": ident, "data_b64": blob,
                })

        elif kind == "peek":
            # The browser never reconstructs slot state itself -- that would mean
            # a second implementation of the state machine, free to disagree with
            # the one being judged. It asks instead.
            if self.session is not None:
                self.emit({"type": "state", "state": self.session.snapshot()})

        elif kind == "finish":
            await self._finish()

    async def _start(self, msg: dict[str, Any]) -> None:
        if self.session is not None:
            await self._finish()

        latency = dict(DEMO_LATENCY_MS)
        for tool, ms in (msg.get("latency_ms") or {}).items():
            try:
                latency[str(tool)] = float(ms)
            except (TypeError, ValueError):
                pass

        self.session = LiveSession(
            msg.get("session_id") or "live",
            emit=self.emit,
            model=self.model,
            latency_ms=latency,
        )
        self.session.start()
        self.session.feed({
            "type": "session_start",
            "session_id": self.session.session_id,
            "modality_mix": ["audio", "text"],
        })

        if self.asr_mod is not None:
            self.recogniser = self.asr_mod.StreamingASR(sample_rate=SAMPLE_RATE)

        self.emit({
            "type": "ready",
            "asr": self.asr_mod is not None,
            "tools": sorted(self.session.agent.manifest.tools),
            "latency_ms": latency,
        })

    async def _on_audio(self, pcm: bytes) -> None:
        if self.session is None or self.recogniser is None:
            return

        # Blocking Kaldi decode, off the event loop. See the module docstring:
        # the loop has to stay free to cancel calls while this runs.
        try:
            chunks = await asyncio.to_thread(self.recogniser.accept, pcm)
        except Exception as exc:  # noqa: BLE001
            self.emit({"type": "error", "error": f"asr: {exc}"})
            return

        for chunk in chunks:
            if not chunk.text.strip():
                continue
            self.emit({
                "type": "heard",
                "text": chunk.text,
                "end_of_turn": chunk.end_of_turn,
                "confidence": chunk.confidence,
            })
            self.session.feed({
                "type": "transcript_chunk",
                "text": chunk.text,
                "end_of_turn": chunk.end_of_turn,
            })

    async def _finish(self) -> None:
        if self.session is None:
            return
        if self.recogniser is not None:
            try:
                for chunk in self.recogniser.finish():
                    if chunk.text.strip():
                        self.session.feed({
                            "type": "transcript_chunk",
                            "text": chunk.text,
                            "end_of_turn": True,
                        })
            except Exception:  # noqa: BLE001
                pass
            self.recogniser = None

        session, self.session = self.session, None
        session.feed({"type": "session_end", "reason": "user ended the session"})
        await session.finish()
        self.emit({"type": "done", "state": session.snapshot()})


async def main() -> int:
    from websockets.asyncio.server import serve
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    print("PARLEY live demo")
    asr_mod, why = _load_asr()
    if asr_mod is None:
        print(f"  ! speech recognition unavailable ({why})")
        print("    the demo still runs -- type instead of speaking")

    print("  loading interruption model…")
    model = InterruptionModel.load_default()

    index = HERE / "index.html"
    media_root = (ROOT / "media" / "scenarios").resolve()

    def _file_response(body: bytes, content_type: str) -> Any:
        return Response(
            200, "OK",
            Headers([
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ]),
            body,
        )

    def _serve_media(rel: str) -> Any:
        """Serve one sample frame or clip, read-only, from media/scenarios.

        The page offers built-in samples so a presenter does not have to go
        hunting through a file manager on camera. That means this process serves
        files chosen by the client, so the path is resolved and then checked to
        be *inside* media_root -- `..` segments and absolute paths both collapse
        to something outside it and are refused. Without that check this handler
        would read any file the process can reach.
        """
        try:
            target = (media_root / rel).resolve()
        except (OSError, ValueError):
            return Response(400, "Bad Request", Headers([("Content-Length", "0")]), b"")
        if not target.is_file() or media_root not in target.parents:
            return Response(404, "Not Found", Headers([("Content-Length", "0")]), b"")
        kind = {".png": "image/png", ".wav": "audio/wav"}.get(target.suffix.lower())
        if kind is None:
            return Response(415, "Unsupported Media Type",
                            Headers([("Content-Length", "0")]), b"")
        return _file_response(target.read_bytes(), kind)

    def process_request(connection: Any, request: Any) -> Any:
        """Serve the page over the same port, so there is one thing to run."""
        path = request.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return _file_response(index.read_bytes(), "text/html; charset=utf-8")
        if path.startswith("/media/"):
            return _serve_media(path[len("/media/"):])
        if path == "/ws":
            return None  # upgrade to websocket
        return Response(404, "Not Found", Headers([("Content-Length", "0")]), b"")

    async def handler(ws: Any) -> None:
        await Connection(ws, asr_mod, model).handle()

    async with serve(
        handler, HOST, PORT,
        process_request=process_request,
        max_size=2 ** 22,
        ping_interval=None,  # audio frames already keep the socket busy
    ):
        print(f"\n  open  http://{HOST}:{PORT}\n")
        await asyncio.Future()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nbye")
