# Next step — learned turn-taking / endpointing

> **This is a work brief for a fresh session.** It is self-contained. Read it in
> full, then read `HANDOFF.md` and `docs/CODE_MAP.md` before writing code.
> Nothing here has been started — this is the specification, not a progress log.

---

## 0. Before you touch anything

1. Read [`../HANDOFF.md`](../HANDOFF.md) — project state, and especially §4 on
   what the voice path does and does **not** do. We did not train speech models;
   we do not start now.
2. Read [`CODE_MAP.md`](CODE_MAP.md) §"rules that must not be broken" and the
   integration points below.
3. Confirm the baseline is green before changing it:
   ```bash
   python -m pytest          # 348 passing
   python scripts/run_scenarios.py   # 30/30, mean 109.2
   ```
4. The GPU is an **RTX 3050 Laptop, 4 GB**. The user has authorised using it.
   4 GB is the hard ceiling — plan batch sizes and model size around it.

---

## 1. Why this task, in one paragraph

Theme 5 is *full-duplex, interruptible* agents. The genuine full-duplex problem —
the one that is the theme rather than adjacent to it — is **turn-taking**:
deciding, in real time, *when the user is finished* (endpointing) and *what it
means when they speak over us* (barge-in vs backchannel vs continuation).
Everything else we built (ASR, TTS, OCR) is commodity perception, correctly
bought off the shelf. This is the one place where deeper, self-trained ML is
both on-theme and defensible. It is also the honest answer to "your own ML is
shallow" — right now it is three logistic-regression classifiers.

---

## 2. What exists today (the gap you are filling)

**There is no endpointing at all.** The agent *trusts* the `end_of_turn` boolean
that arrives on each `TranscriptChunk` (`parley/agent/agent.py:182` and
throughout `_on_transcript`). In the scored scenarios that marker comes straight
from the scenario JSON; in the live path it comes from Vosk committing an
utterance on silence (`parley/multimodal/asr.py:197`). The agent never *predicts*
it.

**Barge-in is a bare signal.** `_on_interruption` (`agent.py:154`) records that a
VAD event fired and lets the *words* decide later what kind of interruption it
was (`nlu.py`). There is no model of turn-taking dynamics — no notion of "the
user paused but is not done", "that was a backchannel, keep going", or "they
genuinely took the floor".

So the two learnable decisions, neither of which is currently learned:

| Decision | Today | This task |
|---|---|---|
| **Endpointing** — is the user done, or mid-turn? | trusted from the event marker | predict from lexical + timing (+ acoustic) cues |
| **Overlap intent** — barge-in vs backchannel vs continuation | classified after the fact from words | predict at overlap onset from timing + partial words |

---

## 3. The honest reality check — read before scoping

**Where this helps, and where it might not.** Do not overclaim; the last two
sessions were disciplined about honest negative results and this must be too.

- **The scored public suite delivers `end_of_turn` explicitly.** A learned
  endpointer changes *nothing* on those 30 scenarios unless you also build
  scenarios that withhold the marker. **So step 1 of the real work is scenario
  design** (see §6), not modelling. Without it you have a model with nothing to
  do and no way to score it.
- **The guide says chunks arrive "with end-of-turn markers."** So on the *hidden*
  set the marker may also be present, in which case the endpointer is
  belt-and-braces. Frame the contribution as **robustness when the marker is
  absent, late, or wrong** — a flaky recogniser drops EOT markers (the fuzzer
  already perturbs this: `eot_dropped`), and that is the case where a learned
  endpointer earns its place.
- **The live demo is where it visibly pays off.** Vosk's silence-based
  endpointing is crude; a lexical+timing endpointer makes the demo feel
  genuinely full-duplex. That is a real, showable win even if the scored delta is
  small.
- **Data is synthetic and that caps the ceiling.** Be upfront about it, exactly
  as `train_classifier.py` is about the interruption corpus.

If, after building it, the model does not beat the "trust the marker, fall back
to a silence timeout" baseline on withheld-marker scenarios, **say so and ship it
as a confidence signal**, the way the interruption classifier was handled. A
negative result honestly reported is worth more than a fabricated win.

---

## 4. Hard constraints (these are not negotiable)

Inherited from the whole project — violating any of them breaks something that
currently works:

1. **No runtime downloads.** Weights ship in the repo. `onnxruntime` is already a
   dependency (via the `vision` extra / RapidOCR) — an **ONNX-exported model adds
   no new runtime dependency.** Do NOT add torch to the runtime path. Train with
   torch offline in a script; export to ONNX; infer with onnxruntime. Or, if the
   model is small enough, export to plain numpy/JSON like the existing
   classifiers and skip onnxruntime entirely.
2. **Deterministic inference.** The harness is bit-reproducible. No dropout at
   inference, no nondeterministic ops, fixed seeds recorded in metadata.
3. **Cheap on the hot path.** Endpointing runs on *every* transcript chunk, and
   latency is 15% of the score. Target sub-millisecond CPU inference. A tiny MLP
   or a distilled/quantised model — not a full transformer per chunk.
4. **Adapter boundary.** `parley/` imports nothing from `harness/`. The model and
   its inference live under `parley/agent/` (or a new `parley/turntaking/`). The
   trainer lives under `scripts/`.
5. **The scored engine must still run on a clean install with neither the voice
   nor vision extra present.** If the endpointer needs onnxruntime, gate it like
   ASR/OCR: degrade to the current marker-trusting behaviour when it is absent,
   and `pytest.importorskip` in its tests. The 30 scenarios and core suite must
   pass with only `pydantic`, `numpy`, `pillow`.
6. **Every number in the docs is real and reproducible.** Held-out eval, honest
   baseline comparison, seeds recorded.

---

## 5. Where it plugs in

| File | Role in this task |
|---|---|
| `parley/protocol/events.py` | `TranscriptChunk.end_of_turn`, `InterruptionSignal`. You may add fields (e.g. a per-chunk silence-since-last-word) but keep them optional and defaulted |
| `parley/agent/agent.py` | `_on_transcript` consumes `end_of_turn`; `_on_interruption` handles VAD. This is where a predicted endpoint would override/augment the marker |
| `parley/agent/nlu.py` | Feature extraction lives here; the endpoint features (lexical completeness, trailing function word, timing) belong alongside the existing ones |
| `parley/agent/model.py` | The pattern to copy: JSON weights, numpy inference, `from_sklearn`/`load_default`, honest metadata. Mirror this for the endpointer if you go the numpy route |
| new: `parley/agent/endpointer.py` (or `parley/turntaking/`) | The model + inference |
| new: `scripts/train_endpointer.py` | Offline training; mirrors `train_classifier.py`'s honest-ablation structure |
| `harness/fuzz.py` | Already perturbs `eot_dropped` and `spurious_vad` — your withheld-marker scenarios interact with this |
| `harness/scoring.py` | Understand how latency and recovery are scored before claiming an improvement |

---

## 6. Suggested plan (adapt as you learn)

**Phase 0 — make it testable first.** Add scenarios that withhold or delay
`end_of_turn`, so there is a decision to make and a way to score it. Without this
the model is untestable. Extend `scripts/make_scenarios.py`; add a handful where
a turn arrives as several partial chunks with `end_of_turn=False` and the final
marker is missing, so the agent must decide when to act. Assert the agent still
completes the task and does not act prematurely on a half-sentence. This phase
alone has value even before any model exists — it exercises a real gap.

**Phase 1 — features and a linear baseline.** Endpointing features per chunk:
lexical completeness (does the transcript parse as a complete request against the
manifest?), trailing token class (a trailing "to"/"from"/"and" predicts *more
coming*; a trailing city/date predicts *done*), silence-since-last-word, word
count, running word rate. Fit a logistic regression first — it is the honest
baseline the neural model must beat, and it may be enough.

**Phase 2 — the neural model, on the GPU.** A small model over the streaming
token sequence + timing features. Candidates that fit 4 GB: a 1–2 layer GRU/LSTM
over token embeddings, or a tiny transformer (2 layers, d=128). Train in torch,
export to ONNX, quantise to int8. **Benchmark honestly** against Phase 1 and
against "trust the marker + 700 ms silence fallback." Report held-out accuracy,
false-early-endpoint rate (acting on a half-sentence — the expensive error), and
CPU inference latency.

**Phase 3 — overlap intent (only if Phase 2 succeeds).** Extend to classifying
overlap onset: barge-in vs backchannel vs continuation, from timing + the first
1–2 partial words. This connects to the existing `BACKCHANNEL`/`BARGE_IN` kinds
in `policy.py`.

**Phase 4 — wire into the live demo.** Show it: the demo currently relies on
Vosk's silence endpointing. A lexical endpointer makes it visibly snappier and
more natural. This is the showable payoff for the video.

### On data
- **Start synthetic**, extending the corpus generators (`corpus.py`,
  `make_speech.py`) with timing, pauses, backchannels, overlaps. Honest about the
  ceiling.
- **A public turn-taking corpus** (Switchboard/MapTask-style) would raise the
  ceiling but needs download + licensing review, and the weights (not the data)
  must ship. Only pursue if synthetic plateaus below the baseline. Do not add a
  runtime data dependency.

---

## 7. Definition of done

- [ ] Withheld-marker scenarios exist and the agent handles them correctly
      (no premature action, task still completes).
- [ ] An endpointer model, trained by us, weights committed (ONNX or JSON).
- [ ] Honest benchmark in the training script's output: neural vs linear vs
      silence-timeout baseline, with held-out numbers and inference latency.
- [ ] Runtime adds **no new pip dependency** (ONNX via existing onnxruntime, or
      pure numpy). Scored suite still passes with only the three core deps.
- [ ] Deterministic: two runs, identical traces.
- [ ] Fuzzer still 100% (it perturbs `eot_dropped` — your model must survive it).
- [ ] Docs updated: `DESIGN.md` (new section), `BUILD_LOG.md` (what broke),
      `RESEARCH.md` (endpointing literature — there is a lot; ICASSP 2026 HumDial
      and the papers already cited are a start), `STATE_OF_PLAY.md` (the session).
- [ ] If it does **not** beat the baseline, that is written down plainly and it
      ships as a confidence signal, not deleted and not oversold.

---

## 8. Rules that must not be broken (repeated because they are load-bearing)

- **Cancel before mutating state** (`agent.py`). Do not reorder anything in
  `_on_transcript` without understanding why cancellation precedes the state
  patch — see `BUILD_LOG.md`.
- **Intent stays out of the idempotency key** (`ledger.py`). Unrelated to this
  task, but do not "tidy" it.
- **Never write non-ASCII via PowerShell `Set-Content`/`Out-File`** — mojibake +
  BOM. Use the editor tool or a bash heredoc.
- **Do not tune against the public suite.** The fuzzer is the real test.
- **Read a measurement after the process settles, not during.** (A size figure
  was published wrong once by reading it mid-run.)
