#!/usr/bin/env python3
"""PARLEY extension: camera-based device troubleshooting, as a live LiveKit agent.

Separate from the benchmark agent on purpose: a 13th tool on `agent_parley.py`
could draw extra calls on FDB-v3 and cost precision. Same free Groq backend
(Whisper-large-v3-turbo STT, openai/gpt-oss-120b LLM, Orpheus TTS; one GROQ_API_KEY),
plus the user's camera:

* the agent subscribes to the user's video track and keeps only the latest
  frame in memory (`parley.extension.LatestFrame`), cleared per session;
* one tool, `diagnose_device_frame()`, grounds that frame with
  `parley.multimodal.ground_frame` (washer error codes, router LEDs, TV "No
  Signal") and returns a diagnosis with manual steps, or a question when the
  picture is ambiguous or unreadable -- the agent asks rather than guesses;
* the same frame is never diagnosed twice (`parley.fdb.ToolGuard`).

It registers under an explicit agent name, so it only joins rooms that ask for
it and never picks up a benchmark room in the same LiveKit project.

Run (from the repo root; keys in .env.local, never in git):
    python fdb/agent_extension.py dev          # start the worker
    python fdb/agent_extension.py token        # print a Playground token
Then open https://meet.livekit.io/?tab=custom, paste LIVEKIT_URL and the token,
connect, and join with camera + microphone on.
"""

import logging
import os
import sys
import uuid

from dotenv import load_dotenv

load_dotenv(os.path.join(os.getcwd(), ".env.local"))

AGENT_NAME = "parley-extension"

INSTRUCTIONS = (
    "You are a voice assistant that helps people troubleshoot home devices -- washing "
    "machines, routers and TVs -- by looking through their camera. Keep replies short and "
    "in plain spoken sentences -- no markdown, bullets or numbered lists; they are read aloud "
    "by a speech engine. When the user asks what is wrong or asks you to look, call "
    "diagnose_device_frame. Then follow its status exactly:\n"
    "- diagnosed: in the same reply, say what the device shows AND give the first one or two "
    "manual steps; offer the rest when the user is ready. Use only the steps in the result.\n"
    "- ask: say the question from the result and wait. Do NOT name a diagnosis until the "
    "user answers; then use the matching entry in manual_if_confirmed.\n"
    "- retake or no_frame: say the question or message, and call the tool again only after "
    "the user says they have moved the camera.\n"
    "Never guess a fault the tool did not report, and never describe the picture beyond "
    "what the tool returned."
)


def print_token() -> None:
    """A Playground token that dispatches this agent into a fresh room."""
    from livekit import api

    room = f"extension-{uuid.uuid4().hex[:6]}"
    token = (
        api.AccessToken()
        .with_identity(f"user-{uuid.uuid4().hex[:6]}")
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .with_room_config(api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)]))
        .to_jwt()
    )
    print(f"LIVEKIT_URL={os.environ['LIVEKIT_URL']}\nroom={room}\ntoken={token}")


if __name__ == "__main__" and sys.argv[1:2] == ["token"]:
    print_token()
    sys.exit(0)

import asyncio  # noqa: E402
import functools  # noqa: E402
from pathlib import Path  # noqa: E402

from livekit import agents, rtc  # noqa: E402
from livekit.agents import Agent, AgentServer, AgentSession, llm, tts, utils  # noqa: E402

from parley.extension import FrameDiagnoser, LatestFrame, diagnose_latest  # noqa: E402


class CameraTools:
    def __init__(self, latest: LatestFrame, diagnoser: FrameDiagnoser) -> None:
        self.latest = latest
        self.diagnoser = diagnoser

    # Raw schema because a no-argument tool otherwise serialises as
    # {"required": []} with no "properties", which Groq rejects (HTTP 400).
    @llm.function_tool(raw_schema={
        "name": "diagnose_device_frame",
        "description": (
            "Look at the user's camera and diagnose the device in view (washer error code, "
            "router status LED, TV 'No Signal'). Returns status 'diagnosed' with manual steps, "
            "'ask' with a question to put to the user, or 'retake'/'no_frame'."),
        "parameters": {"type": "object", "properties": {}},
    })
    async def diagnose_device_frame(self, raw_arguments: dict):
        result = await diagnose_latest(self.latest, self.diagnoser)
        logging.info("diagnose_device_frame -> %s", result.get("status"))
        return result


class _WholeWav:
    """Emitter proxy: collect the HTTP chunks and push the WAV in one piece.

    livekit-agents 1.3.12 force-flushes a TTS segment when audio arrives slower
    than real time, which Groq's Orpheus often does. The flush closes the WAV
    decoder mid-file, the rest of the bytes reach a fresh decoder that expects a
    RIFF header ("Invalid WAV file: missing RIFF/WAVE"), and the agent goes
    silent after the first sentence -- i.e. it names the fault and never reads
    the steps. Pushing each sentence's WAV whole means decoding always outruns
    playback, so that flush never fires. Costs one sentence of buffering.
    """

    def __init__(self, emitter) -> None:
        self._emitter = emitter
        self._buf = bytearray()

    def initialize(self, **kwargs) -> None:
        self._emitter.initialize(**kwargs)

    def push(self, data: bytes) -> None:
        self._buf += data

    def flush(self) -> None:
        if self._buf:
            self._emitter.push(bytes(self._buf))
            self._buf.clear()
        self._emitter.flush()


def buffered_groq_tts(**kwargs):
    from livekit.plugins import groq
    from livekit.plugins.groq import tts as groq_tts

    class Stream(groq_tts.ChunkedStream):
        async def _run(self, output_emitter) -> None:
            await super()._run(_WholeWav(output_emitter))

    class BufferedTTS(groq.TTS):
        def synthesize(self, text, *, conn_options=agents.DEFAULT_API_CONNECT_OPTIONS):
            return Stream(tts=self, input_text=text, conn_options=conn_options)

    return BufferedTTS(**kwargs)


PIPER_VOICE = os.getenv("PARLEY_PIPER_VOICE", "en_US-lessac-medium")
PIPER_DIR = Path(os.getenv("PARLEY_PIPER_DIR", Path.home() / ".cache" / "parley" / "piper"))


@functools.lru_cache(maxsize=1)
def _piper_voice():
    """Load (downloading once, ~60 MB) the offline Piper voice, or None."""
    try:
        from piper import PiperVoice
        from piper.download_voices import download_voice

        model = PIPER_DIR / f"{PIPER_VOICE}.onnx"
        if not model.exists():
            PIPER_DIR.mkdir(parents=True, exist_ok=True)
            download_voice(PIPER_VOICE, PIPER_DIR)
        return PiperVoice.load(model)
    except Exception:
        logging.exception("offline Piper voice unavailable; Groq TTS only")
        return None


class PiperTTS(tts.TTS):
    """Offline speech (Piper, ONNX on CPU): no key, no quota. The fallback for
    Groq's free Orpheus tier, which allows only a few thousand characters a day."""

    def __init__(self, voice) -> None:
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                         sample_rate=voice.config.sample_rate, num_channels=1)
        self._voice = voice

    @property
    def model(self) -> str:
        return PIPER_VOICE

    @property
    def provider(self) -> str:
        return "piper"

    def synthesize(self, text, *, conn_options=agents.DEFAULT_API_CONNECT_OPTIONS):
        return _PiperStream(tts=self, input_text=text, conn_options=conn_options)


class _PiperStream(tts.ChunkedStream):
    async def _run(self, output_emitter) -> None:
        voice = self._tts._voice
        output_emitter.initialize(request_id=utils.shortuuid(), sample_rate=self._tts.sample_rate,
                                  num_channels=1, mime_type="audio/pcm")
        pcm = await asyncio.to_thread(
            lambda: b"".join(c.audio_int16_bytes for c in voice.synthesize(self.input_text)))
        output_emitter.push(pcm)
        output_emitter.flush()


def build_tts():
    """Groq Orpheus first; on any failure (typically its daily 429) fall back to
    offline Piper, so a spent quota changes the voice instead of ending the
    session. FallbackAdapter retries Groq in the background and switches back."""
    groq_tts = buffered_groq_tts(model="canopylabs/orpheus-v1-english", voice="autumn")
    voice = _piper_voice()
    if voice is None:
        return groq_tts
    return tts.FallbackAdapter([groq_tts, PiperTTS(voice)])


class ExtensionAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)


server = AgentServer()


async def _pump(track: rtc.Track, latest: LatestFrame) -> None:
    """Keep only the newest frame. ~5 fps is plenty for a still device panel and
    avoids copying every 30 fps frame just to throw it away."""
    stream = rtc.VideoStream(track, format=rtc.VideoBufferType.RGBA)
    loop = asyncio.get_running_loop()
    last = 0.0
    try:
        async for ev in stream:
            now = loop.time()
            if now - last < 0.2:
                continue
            last = now
            f = ev.frame
            latest.set(bytes(f.data), f.width, f.height)
    finally:
        await stream.aclose()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: agents.JobContext):
    from livekit.plugins import groq, silero

    latest = LatestFrame()          # per session: nothing survives into the next one
    diagnoser = FrameDiagnoser()
    pumps: dict[str, asyncio.Task] = {}

    def watch(track: rtc.Track, pub: rtc.RemoteTrackPublication) -> None:
        if track.kind == rtc.TrackKind.KIND_VIDEO and pub.sid not in pumps:
            pumps[pub.sid] = asyncio.create_task(_pump(track, latest))

    @ctx.room.on("track_subscribed")
    def on_subscribed(track, pub, participant):
        watch(track, pub)

    @ctx.room.on("track_unsubscribed")
    def on_unsubscribed(track, pub, participant):
        task = pumps.pop(pub.sid, None)
        if task:
            task.cancel()
            latest.clear()

    async def cleanup():
        for task in pumps.values():
            task.cancel()
        latest.clear()

    ctx.add_shutdown_callback(cleanup)

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=groq.STT(model="whisper-large-v3-turbo", language="en"),
        llm=groq.LLM(model=os.getenv("PARLEY_LLM", "openai/gpt-oss-120b"), temperature=0.0),
        tts=build_tts(),
        tools=llm.find_function_tools(CameraTools(latest, diagnoser)),
    )
    @session.on("conversation_item_added")
    def on_item(ev):
        if getattr(ev.item, "role", None) == "assistant":
            logging.info("assistant said: %s", ev.item.text_content)

    await session.start(room=ctx.room, agent=ExtensionAgent())

    # Tracks subscribed before our handler was attached.
    for p in ctx.room.remote_participants.values():
        for pub in p.track_publications.values():
            if pub.track is not None:
                watch(pub.track, pub)

    await session.generate_reply(
        instructions="Greet the user in one sentence and ask them to point the camera at the device.")


if __name__ == "__main__":
    agents.cli.run_app(server)
