# Research log — prior art, and what we took from it

Bounded, build-directed research. Each entry ends with **→ Action**: what actually changed in the
code or design as a result. Entries with no action are recorded and dropped.

---

## Round 1 — 24 Sep 2026: full-duplex interruption handling

### R1.1 What production frameworks actually do (Pipecat, LiveKit Agents)

Both leading open-source voice-agent frameworks treat interruption as a **pipeline-wide flush**:

- **Pipecat** — "if STT emits a `UserStartedSpeakingFrame`, the pipeline automatically cancels any
  pending tasks in LLM and TTS." Barge-in cancels the whole downstream pipeline.
- **LiveKit Agents** — on VAD detection the SFU signals the agent to "stop its TTS stream and clear
  its output buffer"; agent state resets.
- **TASTE2** — on barge-in the orchestrator runs a fixed four-step cleanup: close the speech-generator
  socket, clear the audio buffer, reset the interrupt flag, reconnect.

**This is the baseline we must beat, and its weakness is precise.** All three conflate *stop
speaking* with *stop working*. That is correct for TTS (the user no longer wants to hear the
sentence) and wrong for tool execution (the user may still want the flight search that is 80% done).
None of them models the dependency between a changed slot and an in-flight call.

**→ Action.** This is the central competitive claim of the project and belongs on the architecture
slide: *production frameworks cancel the pipeline; PARLEY cancels by dataflow dependency.* Confirms
design note §6.2. No code change — validation.

### R1.2 IHBench — post-interruption recovery in voice agents (arXiv 2606.19595)

Benchmarks whether agents recover after interruption during a structured workflow. Metrics: task
completion rate, **state preservation**, recovery latency, information retention.

The failure modes it observes in current agents are, verbatim in substance:
1. **State loss** — forgetting previously collected information
2. **Duplicate tool calls** — re-executing the same actions after interruption
3. **Context confusion** — mixing interrupted and resumed task information
4. **Incomplete slot filling**

This is independent confirmation that the two things Theme 5 scores hardest — "absence of stale
re-runs" (35%) and "zero duplicate state-changing calls" (10%) — are exactly where deployed systems
fail. The hidden set is almost certainly probing these.

**→ Action.** Raised the priority of the idempotency ledger from "cheap insurance" to a
first-class kernel component with its own test suite. Added `context confusion` as an explicit
adversarial scenario class: interleave two intents and assert no slot crosses between them.

### R1.3 EchoChain — state-update reasoning under interruptions (arXiv 2604.16456)

A full-duplex benchmark whose state modifications fall into three categories:
**Corrections** (revise a previous statement) · **Overrides** (new information supersedes earlier
context) · **Cancellations** (abandon a previously stated plan).

Maps onto our taxonomy cleanly — corrections → `SLOT_CORRECTION`, overrides → `REFINEMENT`,
cancellations → `GOAL_SWITCH` — and reports that current models fail substantially on all three.

**→ Action.** Kept the taxonomy; gained external grounding for three of its five branches, which
matters for the 15 Oct "where did that come from?" question. The two branches with no analogue in
EchoChain (`SELF_REPAIR`, `BARGE_IN`) are the two that are *speech* events rather than *state*
events — which led directly to R1.5.

### R1.4 Continue / Adapt / Yield (arXiv 2609.13117)

In-turn adaptation to overlapping speech. When overlap is detected mid-utterance the agent picks one
of three policies: **Continue** (keep speaking), **Adapt** (modify the ongoing utterance), **Yield**
(stop and let the user finish). Decisions are made *during* generation, not at turn boundaries.

**→ Action.** Adopted the three verbs directly as our floor-management policy names. Previously we
had only "stop speaking"; `ADAPT` is a genuinely useful third option we would not have modelled
(e.g. user says "morning only" while we are mid-sentence: we do not stop, we splice).

**Re-read 25 Sep.** The framework is named *Duplex Cue* (Lu et al.), and its case study sizes the
problem `ADAPT` exists to solve: on collaborative cues recorded humans adapt **68.2%** of the time,
against **34.8%** for the full-duplex model evaluated — which otherwise continues unchanged (42.4%)
or yields (22.7%). State of the art under-adapts by half: it ploughs on or it shuts up. Confirmed
also that the paper's scope is the **speaking floor only** — it says nothing about in-flight tool
calls, which is what leaves the work axis open to us. Positioning analysis in
`docs/PRIOR_ART.md` §A.1.

### R1.5 The synthesis: interruption is two orthogonal decisions

The literature's taxonomy (R1.4) is about **our voice**. Ours (design note §4) is about **our
work**. Treating them as one decision is the conflation that R1.1 shows every framework making.
Separating them yields a 3 × 3 policy matrix, and the interesting cells are the off-diagonal ones:

| | **Keep all work** | **Selective cancel** | **Cancel all work** |
|---|---|---|---|
| **Continue speaking** | `SELF_REPAIR` — "the… uh… Tuesday one" | — | — |
| **Adapt utterance** | `REFINEMENT` — "morning flights only" | — | — |
| **Yield floor** | `BARGE_IN` — user talks over a filler | `SLOT_CORRECTION` — "Delhi — no, Mumbai" | `GOAL_SWITCH` — "forget flights" |

Read the diagonal and you get the naive system: yield ⇒ cancel everything. Read the off-diagonals
and you get the five behaviours the score actually rewards. **`BARGE_IN` (yield, keep all work) and
`REFINEMENT` (adapt, keep all work) are the two cells every surveyed framework gets wrong.**

**→ Action.** This matrix replaces the flat five-row table as the primary architecture slide. The
`InterruptionPolicy` type in the kernel now carries two independent fields — `floor: CONTINUE |
ADAPT | YIELD` and `work: KEEP_ALL | SELECTIVE | CANCEL_ALL` — instead of one enum. Design note §4
updated.

### R1.6 Speculative tool execution (PASTE arXiv 2603.18897; toolspec; SPORK arXiv 2607.03333)

The guide names "speculative execution" as a focus area without defining a policy. The literature
has one, and it is better than what we had drafted:

- Speculate on the *next* tool call while the planner is still deciding, then **join**: "when an
  authoritative request matches an in-flight speculative execution, PASTE joins the existing task
  and returns its result." On a match the observation is ready at `max(decode, tool)` instead of
  `decode + tool`; on a miss the speculative work is discarded and it is *never slower than
  baseline*.
- `toolspec` reports 11.5% mean trajectory latency reduction at a measured **39.3%** top-1
  prediction hit rate — i.e. the technique pays off even when it is wrong 60% of the time, because
  misses are free.
- **Ghost Tool Calls** (arXiv 2606.02483) raises issue-time side-effect and privacy risk for
  speculative tools — the reason to gate speculation on the manifest's read-only flag.

Our hit rate should be well above 39%, because we speculate from **bound slots** rather than an
n-gram over tool sequences: when destination and date are both confident, `search_flights` is not a
guess.

**→ Action.** Two concrete changes. (1) Implement **speculation join** — a confirmed dispatch whose
`(tool, args)` matches a live speculative call adopts that call instead of issuing a second one.
Without join, speculation would *create* the duplicate calls the 10% block penalises. (2) Keep the
hard rule that `mutating=True` is never speculated, now with a citation.

**Added 25 Sep.** *Cost-Aware Speculative Execution for LLM-Agent Workflows* (arXiv 2606.07846)
prices each speculation in real currency and gates it on an expected-value rule with a
failure-weighted cost term. It names a limitation of ours plainly: **we never price a speculation,
so we cannot decline an expensive one.** Not fixable honestly before the deadline — in a mock
environment where every call is free, a cost model would be untested decoration — so it is recorded
as future work rather than built. See `docs/PRIOR_ART.md` §A.3.

### R1.7 Frontend-backend split for tool calls in full-duplex S2S (arXiv 2609.19334)

Duplex speech frontend emits a *delegation token* and streams ASR to a text backend LLM that does
the tool calls; results are injected back via "prefill-and-repeat" and streamed out by TTS. Stated
goal: "largely preserves regular duplex turn-taking, interruption handling, and low-latency
interaction."

Independent confirmation of the fast/slow split: the responsive loop must not be the loop that
waits on tools. Our fast path is rule-based rather than a duplex speech model — for Theme 5 that is
an advantage, since voice synthesis is explicitly out of scope and inference time is pure cost
against the 15% latency block.

**→ Action.** None structurally; validates design note §7.1. Borrowed the *delegation token* idea as
a trace marker so the viewer can show the exact moment the fast path hands off to the slow path.

### R1.8 ICASSP 2026 HumDial Challenge (arXiv 2601.05564)

Track II evaluates real-time decision-making during concurrent listening and speaking, with
scenarios for follow-up questions, **negation/dissatisfaction**, repetition requests, and topic
switching.

**→ Action.** "Repetition request" is a case we had not modelled: the user asks us to repeat
something we already said. It must **not** re-run tools — it is served from the transcript. Added as
a sixth interruption kind, `REPEAT_REQUEST` (floor: `YIELD`, work: `KEEP_ALL`), and as a public
scenario. This is exactly the sort of case that produces a stale re-run in a naive agent.

---

## Round 2 — 24 Sep 2026: telling a self-repair from a correction

The taxonomy is only worth having if the agent can actually *classify* an
utterance into it. `SELF_REPAIR` vs `SLOT_CORRECTION` is the hard pair: both
look like "X — no wait — Y", and they demand opposite behaviour (do nothing
vs. cancel that slot's readers).

### R2.1 Shriberg's disfluency structure (1994), and incremental detection

The standard annotation for a spoken repair has four parts:

```
    "fly me to Delhi    —    uh, no, I mean    —    Mumbai    on Tuesday"
                 └reparandum┘  └─interregnum──┘   └─repair─┘
                            ↑ interruption point
```

- **reparandum** — the part being replaced
- **interruption point** — where the speaker breaks off
- **interregnum** — the editing phrase bridging the gap ("uh", "sorry", "I mean")
- **repair** — what replaces the reparandum

Detection "needs to be strongly incremental: word by word, enabling downstream
processing to begin as early as possible", and a repair "becomes apparent only
when the interregnum is detected or when the repair onset is encountered"
(arXiv 1408.6788; arXiv 2011.06754). RNN word-by-word taggers are the standard
computational treatment.

**→ Action.** This gives the decision rule we were missing, and it is sharp:

> Both kinds have a reparandum, an interregnum and a repair. They differ in **what
> the repair does to the value**. If the repair supplies a *different* value of the
> same slot type, it is a `SLOT_CORRECTION`. If it *restates the same* value — or
> supplies no value at all, merely resuming — it is a `SELF_REPAIR`.

"to Delhi — no, Mumbai": repair value ≠ reparandum value → correction, patch the
slot. "book the… uh… the Tuesday one": the repair restates the referent already
bound → self-repair, do nothing. The interregnum type is corroborating evidence,
not the decision: *filled pauses* ("uh", "um") skew to self-repair, *editing
terms* ("no", "sorry", "actually", "I mean") skew to correction — but "uh, no,
Mumbai" has both, and the value comparison still decides it correctly.

This also tells us **when** to decide: at repair onset, not at end-of-turn.
Waiting for the end-of-turn marker would forfeit the 15% latency block on
exactly the turns that matter most.

### R2.2 Where ML earns its place here

Six features fall straight out of R2.1 and the taxonomy, and they are cheap:

| # | Feature | Separates |
|---|---|---|
| 1 | interregnum class (none / filled pause / editing term) | repair vs not |
| 2 | repair supplies a value for an already-bound slot | correction vs self-repair |
| 3 | new value == previously bound value | self-repair vs correction |
| 4 | utterance introduces a verb/object for a different intent | goal switch |
| 5 | utterance is a pure narrowing modifier ("only", "just", "make it") | refinement |
| 6 | overlap with our own speech; whether anything is in flight | barge-in vs new request |

**→ Action — the ML decision, and its constraints.** Rules over these features
get most cases; the residue is genuinely fuzzy and is what a learned model is
for. But the runtime constraints are hard: 120 s per scenario, a 300 s warm-up,
and **no runtime downloads** (a cold model pull would eat both the cap and the
latency block). So:

- **Train offline** with scikit-learn on a generated corpus built from the
  taxonomy (templates × slot values × interregnum variants), sklearn a *dev*
  dependency only.
- **Ship weights as JSON** in the repo — a multinomial logistic regression over
  ~20 features is a few kilobytes.
- **Infer in pure numpy**, ~20 lines. Microseconds, zero virtual time, fully
  deterministic, nothing to download.
- **Keep the rule engine as a parallel path** and record both verdicts in the
  trace. When they disagree, the rule wins on the state-modifying branches
  (a wrong `GOAL_SWITCH` cancels real work) and the model wins on the rest.

This is ML where it pays and rules where a wrong answer is expensive — and the
disagreement record is itself good evidence for the jury Q&A.

### R2.3 What actually happened when we built it

Recorded because the result was not the one we expected, and the honest version
is more useful in a Q&A than a flattering one.

| Split | Rules | Model |
|---|---|---|
| training phrasings | 0.935 | 0.978 |
| held-out phrasings (clean) | **1.000** | 0.945 |
| held-out phrasings + ASR noise | **0.891** | 0.876 |

**The model does not beat the rules.** That is not a surprise in hindsight: the
feature vector was hand-designed to be discriminative, so a linear model over it
is fitting a boundary the rules already encode. On clean held-out text both
saturate; under noise the rules win.

Three things came out of chasing that, and all three were worth more than the
model itself:

1. **A real extraction bug.** Held-out evaluation surfaced `'that uh uh one'`
   binding `party_size = 1` — the word "one" as a pronoun being read as a count.
   "Book the Tuesday one" would have injected a false slot into the **scored**
   snapshot. Number words now require a counting context.
2. **A mislabelled class.** "Get me a flight into Mumbai" while `destination=BLR`
   is bound is a `SLOT_CORRECTION`, not a `NEW_REQUEST`. The rule engine was
   right and the corpus was wrong; the corpus was fixed.
3. **Noise robustness in the rules.** Training on clean text and evaluating on
   noisy text exposed that an inserted "uh" or a stutter derailed the rule
   ordering — backchannels, repeat requests and barge-ins all collapsing into
   `SELF_REPAIR`. Stripping hesitation and stutter *before* matching semantic
   cues (while still recording disfluency as a feature) lifted rule accuracy
   under noise from **0.809 → 0.891**. Since 30% of the hidden set is audio,
   this is probably the single most valuable thing the ML detour produced.

**The decision.** The model ships, but only because the *arbitrated ensemble*
beats rules alone — **0.922 vs 0.907** on a noisy test split whose seed was used
neither for fitting nor for selecting the arbitration policy. A +1.5 point
margin is small and is reported as small. The model is a second opinion with a
calibrated confidence, and it is structurally forbidden from triggering a
destructive branch on its own.

**→ Action.** Kept the model under arbitration; added the noise-augmented
training mixture; wrote the ensemble comparison into `scripts/train_classifier.py`
so the claim is re-measurable rather than asserted.

---

## Round 3 — 24 Sep 2026: what the other Theme 5 teams are building

Public GitHub repositories tagged for this exact theme, read for two reasons:
to find the official evaluation kit, and to know what we have to be better than.

### R3.1 The evaluation kit is not public, anywhere

Every Theme 5 repository we found ships a **self-authored** harness. None
references a Samsung-provided kit, schema, or scenario format.

**→ Action.** Confirms the decision to build a spec-faithful replica rather than
wait. It also means the interface risk is shared by every team, so the thing
that matters is how cheaply we can swap in the real kit when it lands — which is
why the agent talks to an executor callable and never imports the harness.
Re-verify at `harness/runner.py` when the kit arrives.

### R3.2 The competitive picture is tighter than expected

Several teams are visibly working the same core intuition. One describes
"sub-step detection of user corrections and instant cancellation of dependent
in-flight work" with a "CommitGate & Write-Ahead EffectLedger". Another
classifies interruptions as "slot revision, retraction or intent switch, with
each type having its own cancel-and-replan policy".

So **dependency-aware cancellation and an effect ledger are not unique to us.**
Claiming them as novel in the deck would be both wrong and easy to puncture.

**→ Action.** Sharpened the positioning. What still appears to be ours:

| | Why it is likely to stay differentiating |
|---|---|
| **Floor × work as two axes** | Others classify interruptions into 3–4 kinds and attach one policy each. Separating *what happens to our voice* from *what happens to our work* is what makes `BACKCHANNEL` and `REPEAT_REQUEST` expressible at all — and those are the cells where a VAD-driven system is actively wrong. |
| **`CANCELLED_UNCERTAIN`** | A write-ahead ledger records intent before dispatch. It does not answer "did the effect land after we cancelled?" — that needs a verifier probe, a compensator, or an admission. Most designs collapse this to a boolean. |
| **Provable-speech gate** | Every claim carries its warrant *into the trace*. Truthfulness is a ±20% multiplier and nobody else appears to be making it mechanically checkable. |
| **Working multimodal with calibrated abstention** | 50% of the hidden set at 1.5×. Expect most submissions to be text-only or to stub perception; abstaining correctly on undecidable input is a further step again. |
| **Invariant fuzzing** | 6000 perturbed schedules, invariants only. This is the only defence against a hidden set that nobody can see. |

**→ Action.** Deck leads with the *matrix* and the *uncertainty outcome*, not
with "we cancel selectively" — which is now table stakes.

---

## Round 4 — 30 Sep 2026: after the pivot to FDB-v3

Bounded to ~20 searches and fetches. Three questions: what the benchmark's
authors say fails, what other Theme 05 teams do on the same benchmark, and
what past PRISM finalists did well.

### R4.1 The FDB-v3 paper's own diagnosis (arXiv 2604.04847)

The paper's results table (Pass@1 with the gpt-4o judge, 100 clips): GPT-Realtime
0.600, Gemini Live 3.1 0.540, Gemini Live 2.5 0.490, **stock cascaded (Whisper →
GPT-4o → TTS) 0.450** with 100% turn-take and the highest latency (10.12 s), Grok
0.430, Ultravox 0.410. Self-correction is the weakest category for every
system: even GPT-Realtime "fail[s] on over 40%". The stated cause is that
models "commit intermediate parameters before the correction arrives", and the
authors recommend deferring commitment and supporting state rollback.

**What it does better than us:** a judged, 100-clip measurement. Ours is
exact-match on 5–10 clips. **What it doesn't solve:** it names the failure but
ships no mitigation; the stock cascaded agent has no turn detector and no gate.

**→ Action.** Our cascaded agent reproduces their latency finding: 10.1 s with
hosted TTS, the same as their 10.12 s. We fixed the TTS half of it (local
Piper, 5.7 s). The paper's diagnosis is our ToolGuard's premise, so it is
cited on the problem slide and in the README. Our exact-match numbers are **not**
comparable to their judged Pass@1, and the README says so.

### R4.2 LiveKit's own duplicate-call bug (livekit/agents issue #3702) and async tools (1.6.0)

LiveKit Agents issue #3702: when a user interrupts during or right after a tool
call, the call and its result are not saved to the chat history. The LLM
re-issues it, which in production means duplicate orders. LiveKit 1.6.0
(June 2026) added async tools with an `on_duplicate` policy, but it detects
duplicates **by tool name only**.

**What it fixes for us:** nothing new. It confirms our ToolGuard dedupe is
aimed at a real, framework-level failure. **What it doesn't solve that we do:**
name-only duplicate detection cannot tell "track order A" from "track order B";
ours keys on the tool *and* its normalised arguments, and returns the first
result instead of refusing.

**→ Action.** No code change; we stay on 1.3.12, which FDB-v3 targets. Cited in the
README's rationale for the guard.

### R4.3 Other Theme 05 teams on the same benchmark

- **Keel** (github.com/PROSTLE/samsung): a gate between a LiveKit agent's LLM
  and its tools. It holds a call until end of turn **plus 600 ms of quiet**,
  drops it if the user keeps talking, and runs identical calls once. It reports
  **48/100 strict** (exact-match, Gemini Live) and 0.529 on self-corrections.
  *Better than us:* a complete 100-clip run, and a realtime model with lower
  latency. *Our weakness it exposes:* our gate only checked for speech that
  *started* inside the window. Keel's "user not speaking" condition is the check
  we added in step 4 (ToolGuard defers while the user is speaking). *What it
  doesn't do:* no learned turn detector and no spoken-ID canonicalisation.
- **SentinelEdge** (github.com/Harya018/samsung-prism-Hackathon): Gemini Live
  plus a debounce "commit gate" and a **"block-only resolver"** that checks a
  proposed call's arguments against the turn's transcript and blocks values
  the user corrected away. It documents cross-turn corrections as unsolved. No
  licence file.
- **Interject** (github.com/joannamariyajames/interject, MIT): not on FDB-v3;
  goal stack, per-turn tool budgets, and a "filler channel" that speaks while
  tools run.

**→ Action.** Reimplemented SentinelEdge's resolver idea from its
description, not its code (no licence): `parley/fdb/resolver.py` works on Shriberg's
reparandum structure (R2.1), which we already model. A call that uses a value from the
words just before a strong repair marker, while none of its values comes from after
the correction, is refused once with the correction quoted. A repeat goes through,
so a false positive costs one LLM round trip. Kept or dropped by the A/B in R4.6.
Not adopted: Keel's 600 ms quiet period (a threshold; CLAUDE.md forbids tuning
thresholds against the benchmark, and it would add latency) and a filler
channel (it would lower measured latency without doing any work sooner).

### R4.4 Audio-based turn detection: Pipecat Smart Turn v3

Open weights, data and training code (BSD-2); Whisper-tiny encoder plus a
classifier, ~8 MB int8 ONNX, ~12 ms on CPU, 23 languages. It decides
end-of-turn from the **waveform**, not the transcript.

**Better than us:** our endpointer only sees words, so a trailing-off *tone*
that STT renders as a clean sentence is invisible to it. **What it doesn't
solve:** it has no notion of a repair marker or of an unbound slot, which is
where our lexical features earn their place.

**→ Action.** Not adopted tonight. LiveKit 1.3's turn-detector interface passes
chat text, not audio, so combining the two means routing audio into the
detector. That is more than a small change. Listed under "What's next" in
the deck.

### R4.5 Past PRISM finalists

Winners are not published anywhere we could find. The one placed project we
found, **TriFusion** (finalist, PRISM GenAI Hackathon 2025), leads with a
one-command evaluation for judges, a two-tier architecture diagram, a
demo video and headline performance numbers.

**→ Action.** It matches the updated guide's own weighting (one-command reproduction, a
diagram, a video). Our README now leads with `reproduce.sh` and one diagram.
The video is on the human checklist in `docs/STATUS.md`.

---

## Standing conclusions

1. **The dataflow claim is defensible and differentiating.** No surveyed system models slot →
   in-flight-call dependency. Every one of them flushes.
2. **Duplicate tool calls after interruption are the documented failure mode of the field**
   (R1.2), and they are worth 10% directly plus a large share of the 35% block.
3. **Speculation is worth doing but only with join semantics** (R1.6) — naive speculation
   manufactures the duplicates we are trying to eliminate.
4. **Misses are free, so speculate aggressively on read-only tools** and never on mutating ones.

## Sources

- [A frontend-backend architecture for tool calls in full-duplex speech models](https://arxiv.org/abs/2609.19334)
- [EchoChain: A Full-Duplex Benchmark for State-Update Reasoning Under Interruptions](https://arxiv.org/pdf/2604.16456)
- [IHBench: Evaluating Post-Interruption Recovery in Voice Agents with Structured Workflows](https://arxiv.org/pdf/2606.19595)
- [Continue, Adapt, or Yield: In-Turn Adaptation to Overlapping Speech in Full-Duplex Agents](https://arxiv.org/pdf/2609.13117)
- [TASTE2: Text-Aligned Speech Modeling and Deployment toward Full-Duplex Voice Interaction](https://arxiv.org/pdf/2609.08956)
- [The ICASSP 2026 HumDial Challenge](https://arxiv.org/pdf/2601.05564)
- [Act While Thinking: Pattern-Aware Speculative Tool Execution (PASTE)](https://arxiv.org/html/2603.18897v1)
- [SPORK: Self-Speculative Forking to Accelerate Agentic LLM Inference](https://arxiv.org/pdf/2607.03333)
- [Ghost Tool Calls: Issue-Time Privacy for Speculative Agent Tools](https://arxiv.org/pdf/2606.02483)
- [Cost-Aware Speculative Execution for LLM-Agent Workflows](https://arxiv.org/abs/2606.07846)
- [toolspec — speculative tool execution for LLM agents](https://github.com/joelvarun/toolspec)
- [Voice Agent Interruption Handling runbook — Hamming AI](https://hamming.ai/resources/voice-agent-interruption-handling-runbook)
- [LiveKit Agents documentation](https://docs.livekit.io/agents/)
- [Full-Duplex-Bench-v3 (arXiv 2604.04847)](https://arxiv.org/abs/2604.04847)
- [livekit/agents issue #3702 — tool call results lost during interruption](https://github.com/livekit/agents/issues/3702)
- [LiveKit — async tools for voice agents](https://livekit.com/blog/async-tools-voice-agents)
- [Keel (PROSTLE/samsung)](https://github.com/PROSTLE/samsung)
- [SentinelEdge (Harya018/samsung-prism-Hackathon)](https://github.com/Harya018/samsung-prism-Hackathon)
- [Interject](https://github.com/joannamariyajames/interject)
- [Pipecat Smart Turn v3](https://github.com/pipecat-ai/smart-turn) · [announcement](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)
- [NemotronLabs VoiceChat (arXiv 2609.21967)](https://arxiv.org/abs/2609.21967) — reports 82.5% tool-selection F1 on FDB-v3
- [TriFusion — PRISM GenAI Hackathon 2025 finalist](https://github.com/Samrudhp/anomaly-detection-TriFusion)
- [Groq rate limits](https://console.groq.com/docs/rate-limits)
