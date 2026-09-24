"""Streaming speech recognition: audio in, `TranscriptChunk` events out.

This module is an **event source**, not a part of the agent. It converts a WAV
stream into exactly the chunks the agent already consumes, which is why adding
real speech changed no line of `agent.py`. The adapter boundary that
`tests/test_adapter.py` proves for tool schemas holds for modality too: the
agent cannot tell whether a chunk was typed, scripted, or spoken.

**Why a streaming recogniser and not Whisper.** Whisper decodes a whole clip and
returns one blob, which would force the agent to wait for the end of the
utterance -- forfeiting the entire latency block on precisely the turns that
matter. It also *tidies disfluencies away by default*: "to Denver, uh, no sorry,
Dallas" comes back as "Dallas", silently destroying the reparandum/interregnum
structure the whole interruption taxonomy is built on. Whisper can be bullied
out of that with a disfluent `initial_prompt` (an insight from Devaansh's
prototype -- see docs/RESEARCH.md), but it cannot be made incremental.

Measured on the same clip, Vosk needs no such trick and emits the repair marker
*as it is spoken*:

    i need a flight from chicago to denver     <- search dispatched
    ... denver oh no                           <- 2.88 s: correction detected
    ... oh no sorry to dallas                  <- 4.44 s: new value arrives

The correction is actionable 1.5 s before the corrected value exists. A
turn-based agent learns about it at 4.95 s. That gap is the product.

Vosk is an **optional extra**: `pip install parley[voice]`. The scored engine
imports nothing from here, so the 29 scenarios and 275 tests run on a clean
install with no model on disk.
"""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "vosk-model-small-en-us-0.15"
"""Committed into the repo. Nothing is fetched at runtime -- a cold model pull
would eat both the 120 s scenario cap and the latency block, and would fail
outright on an evaluation host without egress."""

FRAME_MS = 125.0
"""Audio handed to the recogniser per step. 125 ms is the knee: below it the
partial hypothesis rarely changes and we pay the decode cost for nothing; above
it, repair-onset detection is quantised coarsely enough to lose the head start
this module exists to buy."""

SAMPLE_RATE = 16_000

STABILITY = 2
"""Words held back from a partial hypothesis before it is emitted.

Kaldi revises its own tail: on the reference clip "denver ah no" became "denver
oh no sorry" one frame later -- a revision **two words back**, which a one-word
holdback cannot catch. A chunk, once emitted, may already have cancelled a tool
call, so it cannot be withdrawn; the only honest lever is how long to wait
before committing to a word.

Measured over 40 synthesised clips (`scripts/asr_stability.py`):

    hold   revisions   slot-bearing   lag/word
       0          86             12          0 ms
       1          35              2        213 ms
       2          18              1        473 ms     <- shipped
       3          15              1        775 ms
       4          13              1       1081 ms

No window reaches zero and none can: the recogniser rescores the whole utterance
when it closes, so a final result may contradict a partial however long we wait.
What the sweep settles is which revisions matter. At hold=2, **all but one**
contradicted a function word -- "bug" becoming "book" -- which binds no slot and
cancels no work. Widening to 4 removes six more harmless revisions and costs
600 ms on *every* word, against a latency block worth 15%.

The surviving case needs no new machinery, which is the point worth keeping. A
recogniser that withdraws a slot value is dataflow-identical to a user
correcting themselves: the slot's revision bumps and exactly the calls that read
it are cancelled. A streaming hypothesis is a request, not a fact -- the same
shape as an in-flight effect, resolved by the same ledger."""


class ASRUnavailable(RuntimeError):
    """Raised with instructions rather than an ImportError traceback."""


@dataclass
class Chunk:
    """One incremental transcript delta, shaped like a `TranscriptChunk`.

    `text` is the **delta** -- only words that appeared since the last chunk --
    because the agent interprets the chunk rather than the accumulated turn.
    Emitting the full running hypothesis each time would re-interpret "flight to
    Denver" on every frame and re-dispatch the search it already ran.
    """

    t: float
    """Milliseconds from stream start, on the audio's own timeline."""
    text: str
    end_of_turn: bool = False
    confidence: float = 1.0
    words: list[dict[str, Any]] = field(default_factory=list)

    def as_event(self) -> dict[str, Any]:
        return {
            "type": "transcript_chunk",
            "t": round(self.t, 1),
            "text": self.text,
            "end_of_turn": self.end_of_turn,
        }


def _load_model(model_dir: Path | str | None = None) -> Any:
    """Load the vendored model, with a diagnosable failure instead of a stack trace."""
    try:
        from vosk import Model, SetLogLevel
    except ImportError as exc:  # pragma: no cover - depends on the install extra
        raise ASRUnavailable(
            "streaming ASR needs the voice extra: pip install -e '.[voice]'"
        ) from exc

    SetLogLevel(-1)  # Kaldi chatters to stderr and would corrupt a piped trace.
    path = Path(model_dir) if model_dir else MODEL_DIR
    if not path.exists():
        raise ASRUnavailable(
            f"no acoustic model at {path}. It ships in the repo under models/; "
            "run scripts/fetch_models.py if this is a shallow or LFS-less clone."
        )
    return Model(str(path))


_MODEL: Any = None


def get_model(model_dir: Path | str | None = None) -> Any:
    """Process-wide singleton: the model is ~68 MB and reloading it per session
    would spend the warm-up hook many times over."""
    global _MODEL
    if _MODEL is None:
        _MODEL = _load_model(model_dir)
    return _MODEL


def warmup(model_dir: Path | str | None = None) -> float:
    """Load the model up front. Maps onto the guide's 300 s setup hook."""
    import time

    t0 = time.perf_counter()
    get_model(model_dir)
    return (time.perf_counter() - t0) * 1000.0


class StreamingASR:
    """Feed PCM in, get transcript deltas out.

    Stateful across calls, because a recogniser that is reset per frame cannot
    use acoustic context and would never produce the partial hypotheses this
    design depends on.
    """

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        model_dir: Path | str | None = None,
        t0: float = 0.0,
        stability: int = STABILITY,
    ) -> None:
        from vosk import KaldiRecognizer

        self.sample_rate = sample_rate
        self.stability = max(0, stability)
        self._rec = KaldiRecognizer(get_model(model_dir), float(sample_rate))
        self._rec.SetWords(True)
        self._spoken: list[str] = []
        """Words already emitted. A chunk, once handed to the agent, cannot be
        unsaid -- it may already have cancelled a call -- so this is also the
        commitment record that `_emit` refuses to contradict."""
        self.revisions = 0
        """Times the recogniser contradicted a word we had already emitted.
        Should stay at zero thanks to the stable-prefix rule below; counted
        rather than ignored, because a non-zero value here means the agent was
        fed a word the recogniser no longer believes."""
        self.t = t0

    # -- feeding ----------------------------------------------------------

    def accept(self, pcm: bytes) -> list[Chunk]:
        """Push one frame of 16-bit mono PCM. Returns any chunks it produced."""
        self.t += len(pcm) / 2 / self.sample_rate * 1000.0

        if self._rec.AcceptWaveform(pcm):
            # Utterance boundary: the recogniser has committed. This is the only
            # place end_of_turn can be set truthfully -- a partial is by
            # definition still revisable, and telling the agent a turn ended
            # when it had not would let a half-sentence dispatch a booking.
            result = json.loads(self._rec.Result())
            return self._emit(result.get("text", ""), result.get("result", []), final=True)

        partial = json.loads(self._rec.PartialResult()).get("partial", "")
        return self._emit(partial, [], final=False)

    def finish(self) -> list[Chunk]:
        """Close the stream and flush whatever the recogniser is still holding."""
        result = json.loads(self._rec.FinalResult())
        return self._emit(result.get("text", ""), result.get("result", []), final=True)

    # -- delta computation -------------------------------------------------

    def _emit(self, hypothesis: str, words: list[dict], *, final: bool) -> list[Chunk]:
        new = hypothesis.split()

        # The **stable prefix** rule. Kaldi revises the tail of a partial freely
        # until the utterance closes: this clip's "denver a" became "denver oh
        # no" one frame later. Emitting every new word would hand the agent an
        # "a" it then has to un-hear, and a chunk cannot be un-heard -- by the
        # time the revision lands it may already have cancelled a tool call.
        #
        # So a word is only emitted once another word follows it, which is the
        # recogniser's own evidence that it has moved on. Costs one word of
        # latency; buys a transcript the agent can act on irreversibly. On a
        # final result nothing more is coming, so everything is stable.
        # max(0, ...) is load-bearing: `new[:len(new) - k]` with a hypothesis
        # shorter than the window slices from the *end* instead of yielding
        # nothing, so a 3-word hypothesis under a 4-word hold emitted two words
        # rather than none. The sweep caught it as revisions *rising* with the
        # window, which is the opposite of what holding back can possibly do.
        stable = new if final else new[: max(0, len(new) - self.stability)]

        shared = 0
        for spoken_word, stable_word in zip(self._spoken, stable):
            if spoken_word != stable_word:
                break
            shared += 1
        if shared < len(self._spoken):
            # Belt and braces: the stable prefix should make this unreachable.
            # If it ever fires, the agent has acted on a word the recogniser
            # withdrew, and that is worth seeing rather than smoothing over.
            self.revisions += 1

        delta = stable[shared:]
        if not delta and not final:
            return []

        self._spoken = list(stable)
        text = " ".join(delta)
        if final:
            self._spoken = []  # the next utterance starts from nothing
            if not text:
                return []

        confidence = (
            min((w.get("conf", 1.0) for w in words), default=1.0) if words else 1.0
        )
        return [Chunk(t=self.t, text=text, end_of_turn=final,
                      confidence=confidence, words=words)]


def transcribe_stream(
    path: str | Path,
    *,
    model_dir: Path | str | None = None,
    frame_ms: float = FRAME_MS,
) -> Iterator[Chunk]:
    """Stream a WAV file as transcript chunks on the audio's own timeline.

    Timestamps come from the *audio position*, not the wall clock, so a scenario
    built from a recording replays identically on a fast laptop and a loaded CI
    box -- the same property the virtual clock gives the rest of the harness.
    """
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError(
                f"{path}: need 16-bit mono PCM, got {wf.getnchannels()}ch "
                f"{wf.getsampwidth() * 8}-bit"
            )
        rate = wf.getframerate()
        asr = StreamingASR(sample_rate=rate, model_dir=model_dir)
        step = int(rate * frame_ms / 1000.0)

        while True:
            pcm = wf.readframes(step)
            if not pcm:
                break
            yield from asr.accept(pcm)
        yield from asr.finish()


def transcribe(path: str | Path, *, model_dir: Path | str | None = None) -> str:
    """Whole-clip convenience wrapper. Used by tests, never on the hot path."""
    parts = [c.text for c in transcribe_stream(path, model_dir=model_dir) if c.end_of_turn or c.text]
    return " ".join(parts).strip()
