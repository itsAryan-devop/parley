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
- [toolspec — speculative tool execution for LLM agents](https://github.com/joelvarun/toolspec)
- [Voice Agent Interruption Handling runbook — Hamming AI](https://hamming.ai/resources/voice-agent-interruption-handling-runbook)
- [LiveKit Agents documentation](https://docs.livekit.io/agents/)
