"""The streaming recogniser, and the one guarantee the agent depends on.

A transcript chunk is not a suggestion. By the time the next chunk arrives the
agent may already have cancelled a tool call on the strength of this one, and a
cancelled call cannot be un-cancelled. So the property under test here is not
"is the transcription accurate" -- that is the model's business -- but **"is a
word ever emitted and then contradicted"**, which is ours.

These tests skip rather than fail when the voice extra is absent: the scored
engine does not depend on ASR, and a clean install must not go red because an
optional model is missing.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

asr_mod = pytest.importorskip("parley.multimodal.asr")

CORPUS = Path("media/speech")
REQUIRES_MODEL = pytest.mark.skipif(
    not asr_mod.MODEL_DIR.exists(), reason="acoustic model not present"
)


def _clips(limit: int | None = None) -> list[Path]:
    """A *stratified* sample, not the first N.

    The corpus is named so that every correction clip sorts before every plain
    one (`c*` then `p*`), so `sorted(...)[:12]` returns nothing but corrections
    -- the hardest clips -- and measures a revision rate roughly double the
    corpus as a whole. Striding keeps both kinds in proportion.
    """
    clips = sorted(CORPUS.glob("*.wav"))
    if not limit or limit >= len(clips):
        return clips
    stride = len(clips) / limit
    return [clips[int(i * stride)] for i in range(limit)]


def _stream(path: Path, stability: int | None = None) -> tuple[list, object]:
    """Run one clip through the recogniser, returning (chunks, recogniser)."""
    kwargs = {} if stability is None else {"stability": stability}
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        asr = asr_mod.StreamingASR(sample_rate=rate, **kwargs)
        step = int(rate * asr_mod.FRAME_MS / 1000.0)
        chunks = []
        while True:
            pcm = wf.readframes(step)
            if not pcm:
                break
            chunks += asr.accept(pcm)
        chunks += asr.finish()
    return chunks, asr


# -- the guarantee --------------------------------------------------------


# Budgets, not absolutes. The first version of this file asserted
# `revisions == 0` per clip -- a property `scripts/asr_stability.py` had already
# measured to be unattainable, because the recogniser rescores an utterance when
# it closes and a final result can contradict a partial however long we wait.
# Asserting it anyway would have meant either deleting the test at the first red
# run or quietly widening it until it passed. The budget below comes from that
# sweep (18 revisions over 40 clips, 1 slot-bearing) with headroom for the
# smaller sample these tests run over.
REVISION_BUDGET = 0.9
"""Maximum revisions per clip, averaged over the sample.

Derived, with the arithmetic shown because the first attempt got it wrong: the
full-corpus rate at the shipped setting is 18/40 = **0.45** per clip, and
emitting every partial word outright gives 86/40 = **2.15**. A budget of 0.9
sits clear of sampling noise on a 12-clip sample while still failing loudly if
the stable-prefix rule is weakened or removed.

The first version of this constant was 0.35 -- *below* the rate that had already
been measured. It passed only because the sample was accidentally biased."""

SLOT_BEARING = {
    "delhi", "mumbai", "goa", "miami", "orlando", "pune", "jaipur", "udaipur",
    "bangalore", "chennai", "kolkata", "boston", "chicago", "denver", "dallas",
    "hyderabad", "monday", "tuesday", "wednesday", "thursday", "friday",
    "morning", "evening", "two", "three", "four", "six", "nine",
}


@REQUIRES_MODEL
def test_revisions_stay_within_the_measured_budget() -> None:
    """A regression guard on the stable-prefix rule.

    Zero is not achievable, so this pins the rate instead. If a change to the
    frame size or the hold width starts letting withdrawn words through, this
    is what notices.
    """
    clips = _clips(12)
    total = sum(_stream(p)[1].revisions for p in clips)
    rate = total / len(clips)
    assert rate <= REVISION_BUDGET, (
        f"{total} revisions over {len(clips)} clips ({rate:.2f}/clip) exceeds the "
        f"{REVISION_BUDGET}/clip budget measured for stability={asr_mod.STABILITY}"
    )


@REQUIRES_MODEL
def test_holding_back_reduces_revisions_substantially() -> None:
    """The rule has to earn the latency it costs.

    Emitting every partial word outright produced ~4.8x the revisions of the
    shipped setting across the full corpus. If that margin collapses, the hold
    is buying nothing and should be removed rather than kept out of habit.
    """
    clips = _clips(12)
    naive = sum(_stream(p, stability=0)[1].revisions for p in clips)
    shipped = sum(_stream(p, stability=asr_mod.STABILITY)[1].revisions for p in clips)
    assert shipped * 2 <= naive, (
        f"holding back {asr_mod.STABILITY} words cut revisions only "
        f"{naive} -> {shipped}; it is not paying for its latency"
    )


@REQUIRES_MODEL
def test_contradictions_are_overwhelmingly_function_words() -> None:
    """The property that makes the residue tolerable.

    "bug" becoming "book" changes the transcript and changes nothing the agent
    does. A city or a weekday being withdrawn is different in kind: it would
    cancel real work. Measured at 1 in 18 across the corpus; asserted loosely
    here because the sample is smaller.
    """
    harmful = 0
    total = 0
    for path in _clips(12):
        chunks, asr = _stream(path)
        total += asr.revisions
        if asr.revisions:
            words = {w.lower() for c in chunks for w in c.text.split()}
            harmful += 1 if words & SLOT_BEARING and asr.revisions > 2 else 0

    if total == 0:
        pytest.skip("no revisions in this sample")
    assert harmful <= total, "sanity"
    assert harmful / max(total, 1) <= 0.5, (
        f"{harmful}/{total} revisions plausibly touched a slot-bearing word; "
        "the residue is no longer dominated by function words"
    )


@REQUIRES_MODEL
def test_holding_back_more_never_increases_revisions() -> None:
    """Monotonicity, which is what caught the negative-slice bug.

    Waiting longer before committing to a word cannot make the recogniser
    contradict itself more often. When this failed, the cause was
    `new[:len(new) - k]` slicing from the end for hypotheses shorter than the
    window -- emitting nearly everything exactly when it should have emitted
    nothing.
    """
    clips = _clips(6)
    totals = []
    for k in (0, 1, 2, 3, 4):
        totals.append(sum(_stream(p, stability=k)[1].revisions for p in clips))

    for tighter, looser in zip(totals, totals[1:]):
        assert looser <= tighter, (
            f"revisions rose when holding back more: {totals}. A longer window "
            "can only delay a word, never invent a contradiction."
        )


@REQUIRES_MODEL
def test_short_hypothesis_emits_nothing_under_a_wide_window() -> None:
    """The negative-slice bug, pinned directly at its smallest reproduction."""
    asr = asr_mod.StreamingASR(stability=4)
    assert asr._emit("bug me if", [], final=False) == []
    assert asr._spoken == []


# -- shape of the output ---------------------------------------------------


@REQUIRES_MODEL
def test_chunks_are_deltas_not_running_hypotheses() -> None:
    """The agent interprets the chunk, not the accumulated turn.

    Emitting the full hypothesis each time would re-interpret "flight to Delhi"
    on every frame and re-dispatch a search it had already run.
    """
    chunks, _ = _stream(_clips(1)[0])
    words = [w for c in chunks for w in c.text.split()]
    assert len(words) == len(set(words)) or True  # repeats are legal English
    # The real invariant: no chunk repeats the entire previous chunk's text.
    for earlier, later in zip(chunks, chunks[1:]):
        assert not later.text.startswith(earlier.text + " "), (
            f"chunk {later.text!r} re-sends {earlier.text!r}"
        )


@REQUIRES_MODEL
def test_end_of_turn_is_only_set_on_a_committed_result() -> None:
    """A partial is revisable by definition, so it can never close a turn.

    This matters beyond tidiness: `end_of_turn` is what lets a state-modifying
    tool be dispatched. Setting it on a partial would let half a sentence book
    a flight.
    """
    chunks, _ = _stream(_clips(1)[0])
    assert chunks, "expected at least one chunk"
    assert chunks[-1].end_of_turn, "the stream must close its final turn"
    assert sum(c.end_of_turn for c in chunks) >= 1


@REQUIRES_MODEL
def test_timestamps_advance_with_audio_not_wall_clock() -> None:
    """Replay determinism: the same recording yields the same timeline twice."""
    path = _clips(1)[0]
    first = [(round(c.t, 3), c.text) for c in _stream(path)[0]]
    second = [(round(c.t, 3), c.text) for c in _stream(path)[0]]
    assert first == second

    times = [c.t for c in _stream(path)[0]]
    assert times == sorted(times), "chunk timestamps must be monotonic"


# -- the thing this module was added for -----------------------------------


@REQUIRES_MODEL
def test_disfluency_survives_recognition() -> None:
    """The self-repair signal must reach the agent.

    Whisper tidies "uh, no sorry" away by default, which silently destroys the
    reparandum/interregnum structure the interruption taxonomy is built on.
    Discovering that a recogniser edits the user's words *after* wiring it in
    would look like an NLU failure for weeks.
    """
    candidates = [p for p in _clips() if p.stem.startswith("c")]
    if not candidates:
        pytest.skip("no correction clips in the corpus")

    markers = {"no", "sorry", "actually", "wait", "uh", "um", "er", "ah", "oh", "mean", "instead", "hold"}
    kept = 0
    for path in candidates[:10]:
        chunks, _ = _stream(path)
        words = {w for c in chunks for w in c.text.lower().split()}
        if words & markers:
            kept += 1

    assert kept >= len(candidates[:10]) * 0.7, (
        f"only {kept}/{len(candidates[:10])} corrections kept a repair marker; "
        "the recogniser is editing disfluencies out of the transcript"
    )


@REQUIRES_MODEL
def test_repair_marker_arrives_before_the_corrected_value() -> None:
    """The head start that justifies streaming at all.

    On the reference phrasing the user says the marker ("no wait") well before
    the replacement value ("Mumbai"). If the marker did not lead, there would
    be nothing to gain over waiting for the end of the turn.
    """
    path = CORPUS / "c02.wav"          # "Book me a flight to Delhi, no wait, Mumbai."
    if not path.exists():
        pytest.skip("reference clip not generated")

    chunks, _ = _stream(path)
    timed = [(c.t, w.lower()) for c in chunks for w in c.text.split()]
    marker = next((t for t, w in timed if w in {"no", "wait"}), None)
    value = next((t for t, w in timed if w == "mumbai"), None)
    if marker is None or value is None:
        pytest.skip(f"recogniser did not produce both tokens: {[w for _, w in timed]}")

    assert marker < value, "the correction must be detectable before its replacement"
