# HANDOFF — read this first

**Purpose:** let a fresh session (or a new teammate) pick this up with zero prior
context and continue without breaking anything.

**Project:** PARLEY — Samsung PRISM Y2026 GenAI Hackathon (3rd Edition),
**Theme 05 — Interruptible Real-Time Agents**
**Team:** `ThaparPatiala_<TEAM>` (placeholder — see §6), Thapar Institute, Patiala
**Deadline:** **30 September 2026**, confirmed by the team. *(The deck PDF we
were given reads 25 Sep. The team has confirmed the 30th twice. Do not re-raise
this.)*
**Last updated:** 25 Sep 2026

---

## 1. The one-paragraph version

A voice assistant takes turns; people interrupt. When someone says *"…to Delhi —
no, Mumbai"*, **exactly one thing changed**: the value of one slot. Every
framework we surveyed responds by flushing the whole pipeline. PARLEY works out
which in-flight tool calls *read that slot*, kills precisely those, and leaves
everything else running.

**The thesis in one line: interruption handling is a dataflow problem, not a
control-flow problem.** Everything else follows from taking that seriously.

---

## 2. Current state — all green

| | |
|---|---|
| Scenarios | **30/30** pass every declared check, mean **109.2** |
| Tests | **348** passing |
| Fuzzer | **6000** perturbed runs, every invariant held |
| Commits | 33 on `master` |
| Working tree | clean |

Reproduce:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
python scripts/run_scenarios.py     # 30/30, mean 109.2
python -m pytest                    # 348 tests
python scripts/fuzz.py --trials 60  # adversarial timing
```

Scores exceed 100 because the rubric's quality multiplier (0.80–1.20×) sits on
top of a 100-point scale. That is our reconstruction of the published rubric
(`harness/scoring.py`), not an official score.

---

## 3. What is actually built

### The scored engine — `parley/`

Pure Python. Three runtime dependencies: `pydantic`, `numpy`, `pillow`. No LLM
in the loop, by design — 75% of the rubric is coordination, and inference time is
pure latency cost.

| Module | What it does |
|---|---|
| `protocol/` | Typed events in, actions out, state snapshot, tool manifest parsing |
| `kernel/calls.py` | Call registry; the eight-outcome enum incl. `CANCELLED_UNCERTAIN` |
| `kernel/ledger.py` | Idempotency, keyed on `(tool, args)` — **deliberately not intent** |
| `kernel/dispatcher.py` | Cancellation-safe dispatch, speculation join, verify→compensate→disclose |
| `kernel/policy.py` | The floor × work matrix; seven interruption kinds |
| `agent/nlu.py` | Feature extraction, rule classifier, arbitration with the learned model |
| `agent/agent.py` | The main loop. **Cancel before mutating state** — ordering is load-bearing |
| `agent/floor.py` | Floor manager + the provable-speech gate |
| `agent/planner.py` | Manifest-driven planning; nothing is hardcoded per tool |
| `multimodal/` | Vision, audio, ASR, OCR, and the abstention logic |

### The harness — `harness/`

Our reconstruction of the evaluation kit, because the official one was never
released. Virtual-clock event loop (subclassed `SelectorEventLoop` with an
overridden `time()`), deterministic mock environment with a **commit point**
partway through each mutating call, JSONL trace, rubric scorer, invariant fuzzer.

### What is NOT here

- No LLM / no API calls. Deliberate.
- No Docker. Removed 25 Sep — nothing in the rules asked for it; see
  `docs/DESIGN.md` §14 for the full accounting.
- No official evaluation kit. Never published. `harness/adapter.py` +
  `tests/test_adapter.py` prove the agent survives a deliberately alien schema.

---

## 4. About the voice — read this carefully

This is the most commonly misunderstood part, so it is stated precisely.

### We did NOT train any speech model

| Capability | Where it comes from | Trained by us? |
|---|---|---|
| **Speech recognition (ASR)** | Vosk `vosk-model-small-en-us-0.15`, vendored in `models/` (~68 MB) | ❌ **No** — pre-trained, off the shelf |
| **Text-to-speech** | The browser's own `SpeechSynthesis` API | ❌ No |
| **OCR** (reading panels) | RapidOCR / ONNX, pip-installed | ❌ No |
| Interruption classifier | Logistic regression on a generated text corpus | ✅ Yes — `scripts/train_classifier.py` |
| Audio *event* classifier | Logistic regression on spectral features | ✅ Yes — `scripts/train_perception.py` |
| Vision classifier | Logistic regression on HSV colour features | ✅ Yes — `scripts/train_perception.py` |

**"Did you do voice training?" — No, and we should not claim to.** We do not
train speech recognition. We vendored a pre-trained Vosk model and built a
*streaming wrapper* around it (`parley/multimodal/asr.py`).

### What IS ours about the voice path, and why it matters

The wrapper is the contribution, not the acoustics:

1. **Streaming, not batch.** Whisper decodes a whole clip and returns one blob,
   forfeiting the latency block. Vosk emits partial hypotheses *as words are
   spoken*. Measured on a reference clip: the correction "oh no sorry" is
   actionable at **~2.9 s**, while the corrected value ("Dallas") does not arrive
   until **4.44 s**. A turn-based agent learns about it at 4.95 s. **That 1.5 s
   gap is the product.**
2. **Whisper destroys the evidence.** It removes disfluencies by default, so
   "to Denver, uh, no sorry, Dallas" comes back as "Dallas" — and the
   reparandum/interregnum/repair structure the whole taxonomy rests on is gone.
   (Credit: Devaansh found this. See `docs/PRIOR_ART.md` Part B.)
3. **The stable-prefix rule.** Kaldi revises its own tail. A chunk, once handed
   to the agent, may already have cancelled a tool call and cannot be unsaid. So
   a word is held back until another word follows it. `STABILITY = 2` was chosen
   from a measured sweep over 40 clips, documented in `asr.py`.

### The audio classifier is not speech

`parley/multimodal/audio.py` classifies **machine sounds** — beeping, grinding,
clicking, continuous tone, silence — from spectral features. It cannot transcribe
a human. It exists for the device-support scenarios ("listen to this racket").
Do not conflate the two.

---

## 5. The live demo

```bash
pip install -e ".[demo]"
python demo/server.py        # http://127.0.0.1:8771
```

Hold the mic button, say *"find me a flight to Delhi on Tuesday, and a hotel in
Goa"*, then — while both searches are still running — cut in with *"no wait,
Mumbai"*. The flight bar freezes red mid-fill; the hotel bar finishes untouched.

Also: drag a photo onto the page, or click a sample chip. The washer panel gets
its error code read and looked up; the two-LED router gets *"is it the WAN LED or
the power LED?"* and dispatches nothing.

**Architecturally, only one object changes.** `demo/live.py` swaps the virtual
clock for `LiveClock` (real `perf_counter`). The agent, kernel, floor manager,
planner and mock environment are imported unmodified. The timeline is drawn from
**the same trace records the scorer reads** — if the trace is wrong, the picture
is wrong.

Port 8771. The trace viewer (`scripts/view.py`) uses 8770; the demo script wants
both running during a take.

---

## 6. What remains — humans only

Nothing technical is blocking. These five need a person:

| # | Task | How |
|---|---|---|
| 1 | **Team name** | `python scripts/set_team_name.py YourCollege_YourTeam` — one command, tested, handles all 7 places + the deck filename |
| 2 | **Record the demo video** | ≤5:00 hard cap. Full script with timings: `docs/DEMO_SCRIPT.md` |
| 3 | **Sign the AI disclosure** | `DISCLOSURE.md` — needs representative name, role, signature, date |
| 4 | **Real Samsung device photos** | *Optional but the highest-value item left* — see §7 |
| 5 | **Tag, last** | `PRISM_GENAI_HACKATHON_Y2026`. **Push nothing after.** Full checklist: `SUBMISSION.md` §8 |

---

## 7. The Galaxy photos — what we need and why

**This is the weakest honest claim in the project.** Every frame and clip the
perception models were fitted on is **synthetic**, generated by
`scripts/make_media.py`. The feature extractor is deliberately scale-invariant
(everything is a fraction or a normalised coordinate) so a phone photo *should*
produce a comparable vector — but "should" is not "was measured".

**What to send:** any PNG/JPG of

- a washing machine / appliance panel showing an **error code** (`E4`, `4E`, …)
- a router with a **status LED** lit — amber, red, green
- a TV showing **"No Signal"** or similar
- bonus: a deliberately **bad** photo — glare, off-axis, blurry. Those test the
  abstention path, which is the part that scores.

**Where they go:** drop the files in `media/scenarios/frames/`. Any resolution.

**Then run:**

```bash
python -c "
import asyncio, sys; sys.path.insert(0,'.')
from pathlib import Path
from parley.multimodal import ground_frame
from parley.protocol.events import VideoFrame
for p in Path('media/scenarios/frames').glob('*.png'):
    r = asyncio.run(ground_frame(VideoFrame(frame_id=p.stem, path=str(p))))
    print(f'{p.name:<34} {r.label} conf={r.confidence:.2f} ambiguous={r.ambiguous}')
"
```

**What success looks like:** a real photo either classifies correctly, or the
agent *abstains and asks*. Both are wins. A confident wrong answer is the only
failure, and it would be worth knowing about before a jury finds it.

A real Samsung device in the demo video, in front of a Samsung jury, is worth
more than another scenario.

---

## 8. Where everything is written down

| File | What it holds |
|---|---|
| `README.md` | The pitch. Results, architecture, what is different |
| `SUBMISSION.md` | The checklist. Traps ranked by how easily they would catch us |
| `HANDOFF.md` | **This file** — start here |
| `docs/DESIGN.md` | The design note. §14 scope creep, §15 known limitations |
| `docs/BUILD_LOG.md` | **Every bug worth remembering**, grouped by what it teaches |
| `docs/RESEARCH.md` | Literature survey, each entry ending in "→ Action" |
| `docs/PRIOR_ART.md` | Part A published papers; Part B teammate's prototype |
| `docs/DEMO_SCRIPT.md` | Shot-by-shot video script with timings and fallbacks |
| `docs/STATE_OF_PLAY.md` | Session-by-session log of what changed and why |
| `DISCLOSURE.md` | AI-use disclosure, per feature, with prompts |

**If you read only two:** this file, then `docs/BUILD_LOG.md`. The build log is
where the hard-won knowledge lives — the bugs are more instructive than the
features.

---

## 9. Rules that must not be broken

Learned the hard way, all documented in `docs/BUILD_LOG.md`:

1. **Cancel before mutating state.** A call is identified as stale by having read
   a slot's *old* value. Patch the slot first and the call looks consistent with
   the new value and survives when it should die. Invisible in a passing demo,
   fatal on the hidden set.
2. **The idempotency key must not include intent.** It caused a genuine
   double-booking. The identity of a business action is what it *does*.
3. **Never write non-ASCII with PowerShell `Set-Content`/`Out-File`.** It mangles
   em-dashes into mojibake and adds a BOM. Use the Edit tool or a bash heredoc.
4. **Do not tune against the public suite.** Thirty scenarios we wrote prove very
   little. The fuzzer is the real test.
5. **Every number in the docs must be real.** If a figure moves, find out why
   before tagging. We shipped a wrong image size once by reading it mid-unpack.
6. **Tag last, push nothing after.** The tagged commit is what gets judged.
