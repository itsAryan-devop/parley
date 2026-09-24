# PARLEY — Design Note

**Samsung PRISM Y2026 GenAI Hackathon (3rd Ed.) · Theme 05 — Interruptible Real-Time Agents**

> *Parley (n.): a conversation between opposing parties, conducted under a truce, in which either
> side may speak at any moment.*

Status: living document. Written before code, updated as the build proceeds.
Companion files: [`ARCHITECTURE.md`](ARCHITECTURE.md), [`BUILD_LOG.md`](BUILD_LOG.md),
[`RESEARCH.md`](RESEARCH.md), [`../DISCLOSURE.md`](../DISCLOSURE.md).

---

## 1. Problem, in our own words

A half-duplex assistant owns the microphone in turns: it listens, it thinks, it speaks, and each
phase excludes the others. Humans do not work like that. We interrupt. We correct ourselves
mid-noun-phrase. We change our minds while the machine is already three tool calls deep into the
plan we just abandoned.

The naive fix — "on interrupt, throw away the plan and start over" — is worse than the disease. It
discards work that was still valid, it re-runs calls that already had side effects, and it produces
an assistant that is responsive and wrong instead of slow and right.

**Our claim: interruption handling is a dataflow problem, not a control-flow problem.**

When a user says *"…to Delhi — no, Mumbai"*, exactly one thing changed: the value of one slot.
The correct response is not to re-plan. It is to work out **which in-flight computations read that
slot**, kill precisely those, and leave the rest running. Everything else in this design follows
from taking that sentence seriously.

---

## 2. The scoring surface (why the architecture looks like this)

Per the Theme 5 guide, each scenario is scored 0–100 **strictly from trace logs**:

| Block | Weight | What it actually measures |
|---|---|---|
| Task Completion | 40% | correct tools, valid args, accurate snapshot, grounded final response |
| Interruption Recovery | 35% | prompt cancellation, **absence of stale re-runs**, updated snapshots |
| Response Latency | 15% | time to first *substantive* spoken action after input/interrupt |
| Safety & Protocol | 10% | **zero duplicate state-changing calls**, schema adherence, valid payloads |

× quality multiplier **0.80–1.20** (naturalness, **truthfulness**, relevance).
Hidden set: ~60 scenarios, 50% text / 30% audio / 20% visual, **1.5× on multimodal**.

Three consequences drive every decision below.

**(a) 75% of the score is the coordination layer, not the model.** Task completion and interruption
recovery are both properties of *what got executed and what got cancelled*. A brilliant LLM that
double-books a flight scores worse than a dumb rule engine that doesn't. So the kernel is the
product; the language model is a component the kernel is allowed to distrust.

**(b) An action that is not logged did not happen.** The trace is not telemetry — it is the
scoring surface. Therefore: no code path may exit without writing its outcome, *including the
cancellation path*. We enforce this structurally (§6.4), not by discipline.

**(c) Truthfulness is worth ±20% and is penalised twice.** Objective 1 forbids false completion
claims; the multiplier rewards truthfulness. A filler that says "Booked!" before the slow path
returns loses points in two places. So every spoken line is generated from **facts the kernel can
prove it holds** (§7.2) — never from a model free-running on vibes.

---

## 3. What makes this different from the obvious build

Most teams will ship: an async loop, `task.cancel()` on interrupt, a dict of slots, a prompt.
That gets partial credit and collapses on adversarial timing. Our five departures:

| # | Departure | Targets |
|---|---|---|
| 1 | **Slot-dataflow cancellation** — every call records the slots it read; a slot patch invalidates exactly its readers | 35% + 40% |
| 2 | **Three-outcome effect ledger** — a cancel is a *request*; calls that completed anyway are resolved as `COMPLETED_NOW_STALE` and compensated, never silently dropped | 35% + 10% |
| 3 | **Interruption taxonomy (5 kinds)** — a self-repair is not an interruption; a refinement must *not* cancel | 35% |
| 4 | **Provable-speech gate** — the fast path can only assert what the kernel holds evidence for | quality ×1.2 |
| 5 | **Speculative read-only execution** — pre-warm read-only calls on confident slots; never speculate on state-modifying ones | 15% |

Plus one thing that is not scored but wins the room: a **replayable duplex timeline viewer**
(§10) that renders a trace as swimlanes — user speech, agent speech, tool lifetimes, cancellation
arrows, snapshot diffs — so a human can *see* an interruption being absorbed.

---

## 4. Interruption taxonomy

The guide treats "interruption" as one concept. We hold that it is five events with five different
correct responses, and that collapsing them is the dominant way teams lose the 35% block.

| Kind | Example mid-flight | Correct response | Cancels? |
|---|---|---|---|
| `SLOT_CORRECTION` | "…to Delhi — no, Mumbai" | patch one slot; invalidate only its readers | **selective** |
| `GOAL_SWITCH` | "forget flights, find a hotel" | cancel all in-flight; re-plan; **retain** still-applicable slots (dates, party size) | all |
| `REFINEMENT` | "make it morning flights only" | let the call finish; filter/re-rank the result | **none** |
| `SELF_REPAIR` | "book the… uh… the Tuesday one" | no action — not an interruption; suppress re-planning | none |
| `BARGE_IN` | user talks over our filler | stop speaking, yield the floor | **none** (speech only) |

Grounding: objective 3 names "localized slot corrections" as distinct from re-planning, and the
in-car use case names "dropping *stale* route calculations" — i.e. stale ones, not all of them. The
five-way split is our engineering judgement.

**Over-cancelling scores as badly as under-cancelling:** task completion (40%) falls while nothing
is gained on recovery (35%).

---

## 5. State snapshot

One typed object, mutated only through named operations, serialised on every final response.

```
SessionState
  session_id : str
  intent     : str | None              # current goal
  slots      : dict[str, Slot]         # name -> Slot
  history    : list[TurnRef]
  revision   : int                     # monotonic; bumps on every mutation

Slot
  name       : str
  value      : Any
  confidence : float                   # 0..1, drives speculation
  source     : text | audio | vision | inferred | default
  revision   : int                     # revision at which this slot last changed
  evidence   : str | None              # the span/frame that produced it
```

Mutations are a closed set: `set_slot`, `patch_slot`, `clear_slot`, `set_intent`, `retain_for_goal_switch`.
No other code may touch `slots`. This matters because snapshot accuracy appears in **both** the 40%
block and the 10% block — a single well-guarded type protects half the score.

`revision` is the linchpin of §6: a call dispatched at revision *r* that read slot *s* is stale iff
`slots[s].revision > r`.

---

## 6. Coordination kernel

### 6.1 Call registry
Every dispatch creates a `CallRecord`:
`call_id`, `tool`, `args`, `read_slots` (the slot names whose values fed the args), `dispatched_at`
(virtual clock), `state_revision`, `mutating: bool`, `idempotency_key | None`, `outcome`.

### 6.2 Slot-dataflow invalidation
On `patch_slot(s)`: invalidate `{c : c.in_flight and s ∈ c.read_slots}`. That is the whole rule.
Calls that never read `s` keep running and keep their results. This is what makes a slot correction
cost one cancelled call instead of a full re-plan.

### 6.3 Cancellation is a request, not a fact
`asyncio.Task.cancel()` raises `CancelledError` at the task's *next* await point. A task past its
final await completes regardless. So every call resolves to one of:

- `CANCELLED_BEFORE_EFFECT` — cancel landed; no side effect occurred
- `COMPLETED_STILL_VALID` — finished, and the current plan still wants it
- `COMPLETED_NOW_STALE` — **finished anyway and is now wrong**

The third is the dangerous one. If the tool was state-modifying, the mock environment's state has
moved but ours has not. It must be **compensated** (call the declared inverse) or **explicitly
acknowledged in the transcript**. Never silently dropped — that is precisely what "absence of stale
re-runs" is probing.

### 6.4 Cancellation-safe dispatch (structural guarantee)
```
record_intent_to_call()      # idempotency key written BEFORE dispatch
try:
    result = await tool(...)
    record_outcome(COMPLETED_*)
except asyncio.CancelledError:
    record_outcome(CANCELLED_BEFORE_EFFECT)   # written on the way out
    raise
finally:
    assert record.outcome is not None          # no silent exits
```
A call that vanishes from the trace is unscoreable and plausibly scored as failure.

### 6.5 Idempotency
For every **state-modifying** tool: `key = H(tool_name, intent, sorted(resolved_args))`. Checked
against a session-scoped ledger *before* dispatch; a hit is skipped and logged as
`DUPLICATE_SUPPRESSED`. This is the concrete mechanism behind "strictly avoid duplicate
state-changing calls" and "adjusting parameters mid-booking **without double-booking**".

---

## 7. Fast path / slow path

### 7.1 Split
- **Fast path** — pure Python, zero inference. Target: first token out well inside the "few hundred
  ms" budget. Emits acknowledgments, progress narration, clarification requests, floor yields.
- **Slow path** — tool execution, multimodal decode, LLM reasoning. Never blocks the fast path.
- Both write to the same trace on the same virtual clock.

### 7.2 The provable-speech gate
A fast-path utterance is assembled from a template whose variables are bound **only** to facts the
kernel currently holds:

- a slot may be spoken only if `slots[name].confidence ≥ τ_speak`
- a completion may be claimed only if a `CallRecord` with that effect has outcome
  `COMPLETED_STILL_VALID`
- otherwise the template degrades to a progress form ("still checking…"), never to a claim

So "checking flights to Mumbai for Tuesday" is emitted when *and only when* destination and date
are actually bound. This buys the 15% latency block at zero inference cost and defends the ×1.2
multiplier at the same time.

### 7.3 Speculation policy
Read-only tools may be dispatched speculatively as soon as their required slots reach
`confidence ≥ τ_spec`, because they are free to cancel. State-modifying tools are **never**
speculated — the manifest's read-only flag makes this a mechanical decision, not a judgement call.

---

## 8. Schema-driven tools

Tool names are never hardcoded. The manifest is parsed at session start into typed descriptors
(name, params, `mutating` flag, optional `inverse_of` for compensation, latency/fault profile).
An unseen tool with a well-formed schema must work with zero code changes — "unseen tools" is named
in the public suite, so it is near-certain in the hidden set.

---

## 9. Multimodal grounding

WAV and PNG arrive on the same queue as text. Rule: **acknowledge first, decode behind the
acknowledgment, clarify rather than guess.**

1. Frame/clip arrives → fast path immediately emits a grounded ack ("let me look at that").
2. Slow path decodes; produces `Perception{label, confidence, evidence}`.
3. If `confidence < τ_ground` **or** two candidate labels are within `δ` → emit a *specific*
   clarification ("is that the router's power LED or the WAN LED?"), not a generic failure.
4. On resolution, the perception becomes a slot with `source=vision|audio` and its evidence
   retained for the final response's grounding.

Half the hidden set is audio/visual at 1.5×. Most teams will ship text-only and forfeit the
highest-weighted scenarios. This is where the gap opens.

---

## 10. Observability & the timeline viewer

Every event and action is appended to a JSONL trace keyed by virtual time. A self-contained HTML
viewer replays it as swimlanes: user speech · agent speech · tool call lifetimes · cancellation
arrows · snapshot revisions. Not scored directly, but it is (a) how we debug adversarial timing,
(b) the architecture slide, and (c) the demo video.

---

## 11. Harness strategy

The official evaluation kit is not in hand. We therefore build a **spec-faithful replica** from the
guide's description — virtual clock, two async queues, deterministic latency and fault injection,
mock flight search / booking / ticket creation / frame-grounded manual lookup, full trace logging —
plus a scorer implementing the §2 rubric.

This is not wasted work even when the real kit lands:
- the agent talks to an **adapter**, so swapping harnesses is a boundary change, not a rewrite;
- we get a scoring signal today instead of on submission day;
- the replica lets us generate adversarial timing the public suite will not contain.

---

## 12. Non-goals

Wake-word detection, speech synthesis quality, UI polish, cross-session memory. All explicitly out
of scope per §6 of the guide. Session-scoped state only.

---

## 13. Open items

1. Real evaluation kit — re-check every interface assumption when it lands.
2. Deadline: deck says 25 Sep, team reports 30 Sep. Plan to the 25th.
3. Team name for `CollegeName_TeamName` — placeholder `ThaparPatiala_<TEAM>` throughout.
