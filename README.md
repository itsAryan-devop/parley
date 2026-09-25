# PARLEY

**An interruption-native coordination kernel for full-duplex voice agents.**

Samsung PRISM Y2026 GenAI Hackathon (3rd Edition) · **Theme 05 — Interruptible Real-Time Agents**
Team `ThaparPatiala_<TEAM>` · Thapar Institute of Engineering & Technology, Patiala

> *Parley (n.): a conversation between opposing parties, conducted under a truce, in which either
> side may speak at any moment.*

---

## The problem, in our own words

A half-duplex assistant owns the microphone in turns: listen, think, speak, each phase excluding
the others. People don't work like that. They interrupt. They correct themselves mid-noun-phrase.
They change their mind while the machine is three tool calls deep into the plan they just abandoned.

The obvious fix — *on interrupt, throw the plan away and start over* — is worse than the disease.
It discards work that was still valid, re-runs calls that already had side effects, and produces an
assistant that is responsive and wrong instead of slow and right.

**Our claim: interruption handling is a dataflow problem, not a control-flow problem.**

When someone says *"…to Delhi — no, Mumbai"*, exactly one thing changed: the value of one slot. The
right response is not to re-plan. It is to work out **which in-flight computations read that slot**,
kill precisely those, and leave everything else running.

Every framework we surveyed does the opposite. Pipecat "cancels any pending tasks in LLM and TTS".
LiveKit stops the TTS stream and clears the buffer. TASTE2 runs a fixed four-step teardown. All of
them conflate *stop speaking* with *stop working* — see [`docs/RESEARCH.md`](docs/RESEARCH.md).

---

## Quick start

Python 3.10–3.12, three runtime dependencies, nothing else:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
python scripts/run_scenarios.py          # public suite + scorecard
python -m pytest                          # 348 tests
python scripts/fuzz.py --trials 60        # adversarial timing
python scripts/view.py --trace S02_slot_correction   # the timeline viewer
```

Nothing is downloaded at runtime. Every model weight is committed JSON.

Three runtime dependencies: `pydantic`, `numpy`, `pillow`. The scored engine
imports nothing else — no torch, no transformers, no network.

### Talk to it

```bash
pip install -e ".[demo]"
python demo/server.py          # then open http://127.0.0.1:8771
```

Hold the mic button and say *"find me a flight to Delhi on Tuesday, and a hotel
in Goa"* — then, while both searches are still running, cut in with *"no wait,
Mumbai"*. The flight search dies mid-bar; the hotel search finishes untouched.

Or drop a photo on the page — a washing-machine panel gets its error code read
and looked up; a router with two LEDs lit gets *"is it the WAN LED or the power
LED?"* and nothing is dispatched until you say. The sample chips load the exact
frames the scored scenarios use.

The same agent, kernel and mock environment the scenarios score, with exactly
one object swapped: a real clock instead of the virtual one. The timeline is
drawn from the same trace records the scorer reads, so if the trace is wrong the
picture is wrong. Speech in is the vendored Vosk model; speech out is the
browser's own synthesiser. Typing works too, if the room is loud.

---

## Results

Scored against our reconstruction of the published rubric
(`harness/scoring.py`), strictly from trace logs.

| | scenarios | mean score |
|---|---|---|
| text | 16 | 110.1 |
| audio | 7 | 107.1 |
| visual | 7 | 109.3 |
| **all** | **30** | **109.2** |

**30/30 scenarios pass every check they declare.** Scores exceed 100 because the quality multiplier
(0.80×–1.20×) applies on top of the 100-point rubric.

**Every adversarially perturbed run holds every invariant** — 6000 runs at up to ±1400 ms
jitter, with tool latency scaled 0.3×–2.5×, commit points moved, events collapsed onto identical
timestamps, faults injected, events redelivered, end-of-turn markers dropped, spurious VAD signals
fired, and sessions truncated mid-flight.

**Speculation hides 1100 ms at a 50% join rate** — above the 39% published for n-gram-driven
speculation, which is the expected direction: bound slots are a stronger signal than predicting the
next tool in a sequence. Measured from the traces by `scripts/speculation_report.py`, not asserted.

**The runtime budget is not close to being a constraint** (`scripts/perf_report.py`):

| | measured | budget |
|---|---|---|
| cold start — imports, model loads, first scenario | **1.05 s** | 300 s warm-up hook |
| slowest scenario, wall clock | **119 ms** | 120 s per-scenario cap |
| whole suite | **&lt;0.5 s** | — |

The slowest scenario could get **1000× slower** and still fit. That is the payoff from virtual time
(a 10-second tool call costs nothing real) and from a fast path with no inference in it.

Perception abstains on **100%** of undecidable frames and **11/12** undecidable clips rather than
guessing.

---

## What is actually different here

| # | | Targets |
|---|---|---|
| 1 | **Slot-dataflow cancellation.** Every call records which slots fed its arguments. A slot correction invalidates exactly its readers; nothing else is touched. | 35% + 40% |
| 2 | **Two-axis interruption policy.** *Floor* (continue / adapt / yield — the verbs are [Lu et al.'s](https://arxiv.org/abs/2609.13117), not ours) and *work* (keep-all / selective / cancel-all) are independent decisions. The naive system is the diagonal. The literature stops at the floor axis; pairing it with a work policy is the contribution. | 35% |
| 3 | **Four-outcome effect ledger.** A cancel is a request, not a fact. Cancelling a state-modifying call yields `CANCELLED_UNCERTAIN`, resolved by probing a manifest-declared verifier, compensating via a declared inverse, or saying out loud that we cannot tell. | 35% + 10% |
| 4 | **Provable-speech gate.** Every utterance is assembled from facts the kernel can warrant, and each assertion carries its warrant into the trace. "Booked" is unreachable unless a booking exists. | quality ×1.2 |
| 5 | **Speculation with join.** Read-only calls start before end-of-turn; the confirmation *adopts* the call already in flight rather than issuing a second one. State-modifying tools are never speculated. | 15% |

### The interruption matrix

Floor verbs are from the overlapping-speech literature (arXiv 2609.13117); the work axis is ours.
The score lives off the diagonal.

| | **keep all work** | **selective cancel** | **cancel all work** |
|---|---|---|---|
| **continue speaking** | `SELF_REPAIR` — "book the… uh… the Tuesday one" | — | — |
| **adapt utterance** | `REFINEMENT` — "make it morning flights only" | — | — |
| **yield floor** | `BARGE_IN` — user talks over a filler<br>`REPEAT_REQUEST` — "sorry, say that again"<br>`BACKCHANNEL` — "mhm" | `SLOT_CORRECTION` — "…to Delhi — no, Mumbai" | `GOAL_SWITCH` — "forget flights, find a hotel" |

`BARGE_IN`, `REPEAT_REQUEST` and `BACKCHANNEL` are the competitively interesting cells: every
surveyed framework cancels the work there, and re-running an already-executed call is exactly the
"stale re-run" the 35% block penalises. A VAD-triggered system stops speaking on "mhm"; that is the
VAD's mistake, not a user request.

Telling `SELF_REPAIR` from `SLOT_CORRECTION` is the hard part — both look like "X — no wait — Y".
Shriberg's disfluency structure gives the discriminator: they differ in **what the repair does to
the value**. A *different* value for a bound slot is a correction; a *restatement* is a repair.

---

## Architecture

```
          ┌──────────────────────── events in ────────────────────────┐
          │ transcript chunks · WAV · PNG · interrupts · tool results  │
          └────────────────────────────┬──────────────────────────────┘
                                       ▼
   ┌───────────────────────────── FAST PATH ──────────────────────────────┐
   │  Interpreter        rules + learned classifier over one feature vector │
   │  FloorManager       provable-speech gate · filler rationing            │
   │  (pure Python — zero inference, zero virtual time)                     │
   └────────────────────────────────┬─────────────────────────────────────┘
                                    ▼
   ┌────────────────────── COORDINATION KERNEL ───────────────────────────┐
   │  CallRegistry       slot → in-flight readers index                     │
   │  Dispatcher         cancellation-safe dispatch · join · supersede      │
   │  IdempotencyLedger  claimed BEFORE dispatch, never after               │
   │  effect resolution  verify → compensate → or disclose                  │
   └────────────────────────────────┬─────────────────────────────────────┘
                                    ▼
   ┌───────────────────────────── SLOW PATH ──────────────────────────────┐
   │  Planner            manifest-driven · speculative before end-of-turn   │
   │  Perception         PNG/WAV → features → calibrated softmax + abstain  │
   └────────────────────────────────┬─────────────────────────────────────┘
                                    ▼
          ┌───────────────────── actions out ──────────────────────┐
          │ speak · tool_call · cancel · clarify · final + snapshot │
          └────────────────────────────────────────────────────────┘

                 every step writes to one append-only trace
                 on one virtual clock — the scoring surface
```

Cancellation happens **before** state is mutated. Patching the slot first would make an in-flight
call look consistent with the new value, and it would survive when it should die.

### Layout

| path | what it is |
|---|---|
| `parley/protocol/` | typed wire contracts: events, actions, state snapshot, tool manifest |
| `parley/kernel/` | the coordination layer — 75% of the score is decided here |
| `parley/agent/` | interpretation, floor management, planning, the event loop |
| `parley/multimodal/` | frame and clip grounding, with calibrated abstention |
| `harness/` | virtual-clock loop, mock environment, trace, scorer, timing fuzzer |
| `demo/` | the live voice demo — real clock, real microphone, same agent |
| `scenarios/` | the public suite as data — readable without reading any Python |
| `viz/timeline.html` | swimlane trace viewer, self-contained |
| `docs/` | [design note](docs/DESIGN.md) · [code map](docs/CODE_MAP.md) · [build log](docs/BUILD_LOG.md) · [research](docs/RESEARCH.md) · [prior art](docs/PRIOR_ART.md) · [demo script](docs/DEMO_SCRIPT.md) · [state of play](docs/STATE_OF_PLAY.md) |

**New here?** Start with [`HANDOFF.md`](HANDOFF.md) — current state, what is
built, what is left, and what the voice path does and does not do.

---

## The harness

The official evaluation kit had not been released when this was built, so we built a spec-faithful
replica from the guide's description of it: virtual clock, deterministic mock tools with latency
and fault injection, full trace logging, and a scorer implementing the published rubric.

**The agent imports nothing from `harness/`.** It consumes objects with a `.type` and a `.t` and
emits actions through a callback, so adapting to a different kit means writing two translation
functions and moving nothing inside `parley/`. That is a cheap claim to make, so it is
[tested](tests/test_adapter.py) rather than asserted: `harness/adapter.py` maps a deliberately
alien schema — different discriminator key, **timestamps in seconds**, `is_final` instead of
`end_of_turn`, payloads nested in a `data` envelope, heartbeat events we have no concept of, and
out-of-order delivery — and the agent runs a full scenario off it, absorbing a mid-stream
destination correction, cancelling only the affected call, and committing exactly one booking.

**Virtual time is implemented by subclassing the event loop and overriding its clock**, rather than
by spinning `asyncio.sleep(0)` to guess when things have settled. Time advances to the next
scheduled callback only when nothing is runnable, so `asyncio.sleep`, `wait_for` and `timeout` are
all virtualised for free and the agent needs no clock-aware API. A ten-second tool call costs no
wall-clock time, which is what keeps us inside the 120 s cap while still modelling realistic latency.

Two subtleties that took real debugging:

- Windows' ~15.6 ms clock resolution batches every timer inside that window, collapsing events 5 ms
  apart into one tick — exactly the adversarial timing the hidden set is built from. Pinned to 1 ns.
- `0.7` seconds is really `0.69999999999999996`, so an action emitted at exactly its prompt's
  timestamp compared as *earlier* than the prompt and the latency scorer skipped it, inventing
  1900 ms of phantom latency on a scenario where the agent answered instantly.

### Adversarial timing

The hidden set is ~60 scenarios of "edge cases and adversarial timing". Passing thirty scenarios
we wrote proves little — they are the cases we thought of. `harness/fuzz.py` perturbs everything and
asserts only **invariants**, never expectations:

> no duplicate state-changing effect · no call left pending or missing from the trace · no effect
> left unresolved and unmentioned · nothing claimed that cannot be warranted · no speculation of a
> state-modifying tool · no run past the cap

It found two bugs no hand-written test could reach, both in the machinery built to prevent them:

1. **A call vanished from the trace.** Two chunks collapsed onto one timestamp, so a speculative
   call was superseded in the instant it was created. `asyncio.create_task` *schedules* rather than
   runs, so the coroutine body never executed — and the "every exit writes an outcome" guarantee
   lives inside that body. The structural guarantee assumed the body always starts.
2. **A genuine double-booking.** The idempotency key included `intent`. Booking a flight before the
   intent resolved and again afterwards produced two different keys for the same action. The
   identity of a business action is what it does, not what we were calling the goal at the time.

---

## Multimodal

Half the hidden set is audio or visual, at a 1.5× multiplier. Most teams will ship text-only.

Frames and clips are acknowledged immediately and decoded **as background tasks** — "process raw
audio and frames behind conversational acknowledgments" is a concurrency requirement, not a tone.
Awaiting the decode inline parks the event loop, so an interruption arriving mid-decode is handled
late and the cancellation grace period blows out.

Perception is classical and offline: PNG → HSV hue-band masses, layout and edge statistics; WAV →
spectral flatness, centroid, onset rate, envelope. Both feed a small softmax trained offline and
shipped as ~14 kB of JSON.

The part that matters is **abstention**. A logistic regression is badly overconfident away from its
training distribution — ours labelled blown-out photographs and frames with two LEDs lit at ~0.9
confidence, which is exactly the confidently-wrong perception objective 5 penalises twice. Two
fixes, neither costing anything at runtime:

- **Temperature calibration**, constrained to `T ≥ 1`. Unconstrained it fitted to 0.50, the floor of
  the scan, because a perfectly separated validation set makes the NLL optimum run to `T → 0`.
  Calibration may soften; it must never sharpen.
- **Mahalanobis abstention** against class centroids, thresholded by how far genuine in-distribution
  data actually reaches. A false abstention costs one clarification question; a false confident
  answer costs task completion *and* the truthfulness term.

Undecidable media is generated separately and held out of training entirely. Teaching a classifier
to pick one label for a frame with two LEDs lit would train away the behaviour that scores.

> **Using real device photos.** The feature extractor is scale-invariant and takes any PNG, so
> photographs drop straight in. Put them in `media/scenarios/frames/` and reference them from a
> scenario, or add them to `media/dataset/frames/train/<label>/` and re-run
> `scripts/train_perception.py`.

---

## Machine learning, and where it earns its place

Two learned components, both trained offline, both shipped as JSON, both inferring in numpy.
scikit-learn is a **dev** dependency and never runs at scenario time.

The interruption classifier is reported honestly because the result was not the one we expected:

| split | rules | model |
|---|---|---|
| held-out phrasings, clean | **1.000** | 0.945 |
| held-out phrasings + ASR noise | **0.891** | 0.876 |

**The model does not beat the rules.** The feature vector was hand-designed to be discriminative, so
a linear model over it fits a boundary the rules already encode. It ships only because the
*arbitrated ensemble* beats rules alone — **0.922 vs 0.907** on a noisy test split used neither for
fitting nor for choosing the arbitration policy. A +1.5 point margin is small and is reported as
small. The model can never trigger a destructive branch on its own; a false `GOAL_SWITCH` cancels
real work.

Chasing that gap was worth more than the model. It surfaced a real extraction bug — "the Tuesday
**one**" binding `party_size = 1`, a false slot in the scored snapshot — and the finding that an
inserted "uh" or a stutter derailed the rule ordering entirely. Stripping hesitation before matching
semantic cues lifted rule accuracy under noise from **0.809 → 0.891**, which, with audio at 30% of
the hidden set, is probably the most valuable thing the detour produced.

Reproduce: `python scripts/train_classifier.py`, `python scripts/train_perception.py`.

---

## Scope

Per the guide: session-scoped state only, no cross-session memory. Out of scope: wake-word
detection, speech synthesis quality, UI polish.

## Reproducing everything

```bash
python scripts/make_media.py          # synthetic frames and clips (~84 MB, gitignored)
python scripts/train_perception.py    # vision + audio weights
python scripts/train_classifier.py    # interruption weights
python scripts/make_scenarios.py      # regenerate scenarios/*.json
python scripts/run_scenarios.py --json runs/scores.json
python scripts/fuzz.py --trials 300 --jitter 1400
```

AI usage is disclosed per-feature, with the prompts used, in [`DISCLOSURE.md`](DISCLOSURE.md).
