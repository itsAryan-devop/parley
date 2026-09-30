"""Local text-to-speech for the FDB-v3 agent: Piper, on CPU, no API key.

Why it exists: Groq's free tier allows **100 TTS requests per day** (measured
from its `x-ratelimit-*` headers, 29 Sep 2026), and the agent makes one request
per spoken sentence. A 100-clip FDB-v3 run needs several hundred, so on the
free tier the run -- ours or the organisers' re-run -- goes silent part-way,
and a silent clip scores zero. Piper runs in-process at ~20x real time on a
laptop CPU, so the voice is never the thing that runs out.

- Engine: `piper-tts` 1.2.0 (MIT), https://github.com/rhasspy/piper
- Voice: `en_US-ljspeech-medium` from `rhasspy/piper-voices` v1.0.0, trained on
  the LJ Speech dataset (public domain). `reproduce.sh` downloads and
  checksums it into `models/piper/` (gitignored).

LiveKit wraps a non-streaming TTS in a sentence `StreamAdapter`, so each
sentence is synthesised as soon as the LLM finishes it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from livekit.agents import APIConnectionError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

DEFAULT_VOICE = (
    Path(__file__).resolve().parents[2] / "models" / "piper" / "en_US-ljspeech-medium.onnx"
)


class PiperTTS(tts.TTS):
    def __init__(self, voice_path: str | os.PathLike | None = None) -> None:
        from piper.voice import PiperVoice

        path = Path(voice_path or os.getenv("PARLEY_PIPER_VOICE") or DEFAULT_VOICE)
        if not path.exists():
            raise FileNotFoundError(f"Piper voice not found at {path}; run reproduce.sh")
        self._voice = PiperVoice.load(str(path))
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=self._voice.config.sample_rate,
            num_channels=1,
        )

    @property
    def model(self) -> str:
        return "piper-en_US-ljspeech-medium"

    @property
    def provider(self) -> str:
        return "piper (local)"

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return _PiperStream(tts=self, input_text=text, conn_options=conn_options)


class _PiperStream(tts.ChunkedStream):
    def __init__(self, *, tts: PiperTTS, input_text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._piper = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        voice = self._piper._voice
        try:
            pcm = await asyncio.to_thread(
                lambda: b"".join(voice.synthesize_stream_raw(self.input_text))
            )
        except Exception as e:  # piper raises plain exceptions; retry like an API error
            raise APIConnectionError() from e
        output_emitter.initialize(
            request_id=uuid.uuid4().hex,
            sample_rate=voice.config.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )
        output_emitter.push(pcm)
        output_emitter.flush()
