# Build log

What was built, in what order, and — more usefully — what broke. Kept because
the 15 Oct round is a **Q&A on design decisions and trade-offs**, and the honest
answer to "why is it like that?" is usually "because the first version wasn't,
and here is what went wrong."

Companion to [`DESIGN.md`](DESIGN.md) (what we decided) and
[`RESEARCH.md`](RESEARCH.md) (what the field already knew).

---

## Order of work

| # | Step | Verified by |
|---|---|---|
| 1 | Protocol layer — events, actions, state snapshot, tool manifest | 31 unit tests |
| 2 | Virtual-clock harness, trace, mock environment | 37 more tests |
| 3 | Coordination kernel — dataflow cancellation, idempotency, effect ledger | 96 total |
| 4 | NLU — manifest-driven extraction, seven-way interruption taxonomy | 117 total |
| 5 | Learned classifier + honest ablation | 130 total |
| 6 | Floor manager, planner, agent loop, multimodal grounding | 170 total |
| 7 | Scenario suite, runner, rubric scorer | 204 total · **15/15 clean** |
| 8 | Timing fuzzer, viewer, Docker, README | 233 total · **18/18 clean** |

Each step was run and verified before the next began, and each was followed by a
research pass. Research findings and the actions taken from them are in
[`RESEARCH.md`](RESEARCH.md).

---

## Every bug worth remembering

Grouped by what they teach, not by when they happened. The ones marked **⚑** were
found by tooling that did not exist when the bug was written — which is the
argument for having built the tooling.

### Concurrency and time

**Windows' clock resolution silently erased adversarial timing.** asyncio fires
every timer within `_clock_resolution` of now in one batch. On Windows that is
~15.6 ms, so events 5 ms apart collapsed into a single tick — exactly the timing
the hidden set is built from. Pinned to 1 ns. *Caught by a determinism test that
asserted an exact interleaving rather than a set.*

**Floating point invented 1900 ms of latency.** ⚑ A 700 ms deadline is stored as
`0.7` s, which is really `0.69999999999999996`. Converting back gave
`699.9999999999999`, so an action emitted at exactly its prompt's timestamp
compared as *earlier* than the prompt. The latency scorer skipped it and measured
the gap to the next response instead. `Clock.now` rounds to six decimals.

**A call vanished from the trace.** ⚑ Two transcript chunks collapsed onto one
timestamp, so a speculative call was superseded in the instant it was created.
`asyncio.create_task` *schedules* rather than runs — the coroutine body never
executed, and the "every exit writes an outcome" guarantee lives inside that
body. The structural guarantee had a hole: it assumed the body always starts.
Fixed with a done-callback and a `started` flag, so "cancelled before anything
happened" stays a fact rather than an assumption.

**Perception blocked the event loop.** "Process raw audio and frames *behind
conversational acknowledgments*" is a concurrency requirement, not a description
of tone. Awaiting the decode inline parked everything, so an interruption
arriving mid-decode was handled late. Decoding is now a background task.

### State and identity

**"Slot never existed" was conflated with "slot was cleared."** Both leave no
entry, and they mean opposite things for staleness. Treating absence as staleness
marked healthy calls `COMPLETED_NOW_STALE`, which then triggered compensation for
effects that were perfectly valid. Cleared slots now leave a revision-carrying
tombstone.

**The idempotency key included `intent` — a genuine double-booking.** ⚑ Booking
6E202 before the intent resolved, then again once it had become `book_flight`,
produced two different keys for the same action and the ledger waved the second
through. Two reservations for one seat: precisely the failure the theme names.
The identity of a business action is what it *does*, not what we were calling the
goal at the time. The mock environment's own duplicate check keyed on tool and
arguments alone and was right all along.

**A completed call stayed "valid" forever.** The manual lookup for a washing
machine finished before the user switched to a television. Its outcome said
`COMPLETED_STILL_VALID` and its answer was about the wrong appliance. Staleness
is now re-checked when the final response is composed, not trusted from
settlement time.

### Understanding

**Punctuation hid multi-word cues.** "uh, no, Mumbai" lost its editing term to a
comma; "what was that?" lost its repeat cue to a question mark. Both landed in
branches with *different cancellation behaviour*, so this was a scoring bug, not
a cosmetic one.

**"hold on" is a floor grab as a phrase but not as tokens.** Cue matching now
works by phrase deletion. Relatedly: "wait" over our speech is a barge-in, in
silence it is hesitation — same word, different branch, and ordering had to
reflect that.

**An inserted "uh" derailed everything.** Under ASR-style noise, backchannels,
repeat requests and barge-ins all collapsed into `SELF_REPAIR`. Stripping
hesitation and stutter *before* matching semantic cues — while still recording
disfluency as a feature — lifted rule accuracy under noise from **0.809 → 0.891**.
With audio at 30% of the hidden set this was probably the most valuable single
fix in the project, and it came out of chasing a model that turned out not to be
needed.

**"the Tuesday one" bound `party_size = 1`.** ⚑ The word "one" as a pronoun read
as a count, injecting a false slot into the *scored* snapshot. Number words now
require a counting context.

**"a hotel in Goa" bound `destination`, not `city`.** Both slots share a city
vocabulary and dictionary order decided. The hotel search could then never find
its required parameter and simply never ran. Slot assignment is now
preposition-aware, with the active goal's parameters breaking remaining ties.

**"forget flights, find me a hotel" chose `book_flight`.** Both goals appear
exactly once, so the cue count tied and dictionary order picked the goal being
*abandoned*. Words following a dismissal are now struck out before scoring.

**"not the grinding" bound `grinding`.** A negated value was being treated as a
supplied one — the sort of error that reads as not listening at all, and entirely
avoidable since the preceding word says so.

### Planning

**Intent-tagged tools were invisible when intent was None.** A camera frame bound
a slot perfectly and then no tool ran at all, because `candidates(None)` returned
only tools *declaring* no intent. Added slot-driven intent inference, and made
cross-goal tools plannable when the user has just supplied exactly what they
need — "…and a hotel in Goa" is an addition, not a switch, and the leading
conjunction says so.

**A mutating tool could not be fired from a previous turn's slots.** "Look at
this panel" then "raise a ticket for that" bound nothing in the second turn, so
the ticket was never raised. Requiring a freshly-bound parameter broke the
commonest shape there is. The commit verb is the gate; the idempotency ledger
makes re-firing safe.

**The same search ran three times.** ⚑ The planner re-plans every turn and the
slots were still bound. Re-running a read-only call whose inputs have not changed
*is* a stale re-run. Added result reuse and supersession of obsolete speculations.

**A restated value bound nothing, so retries never happened.** "book UK404"
repeated after a transient fault bound no new slot, so the mutating tool was
never re-planned. Restatements now count as supplied.

### Speaking

**Duplicate suppression was silent.** ⚑ The agent correctly refused to book twice
and then said *nothing* to two further requests, so the user asked three times
into silence. Suppressing the duplicate is right; being invisible about it is
what caused the repetition. Then the fix over-applied and announced "that's
already done" about a reused flight *search* — uninformative and faintly untrue —
so it is now gated to state-changing tools.

**The agent said "PNQ" to someone who said "Pune".** Canonical values are correct
for tool arguments and wrong for speech. Slots carry a `surface` form for saying
and a `value` for doing.

**"Booking flight flight AI101."** The tool name already supplies the noun.

**`str.capitalize()` lower-cased proper nouns** — "to Mumbai" became "To mumbai"
in the first thing the user hears.

**Multimodal latency scored 0.00** because the frame acknowledgment was a
content-free filler, and a filler is not a substantive response. It is now a
grounded acknowledgment.

### Perception

**The classifier was confidently wrong off-distribution.** Logistic regression
labelled blown-out photographs and frames with two LEDs lit at ~0.9 confidence —
exactly the failure objective 5 penalises twice. Added Mahalanobis abstention
with a threshold set from how far genuine in-distribution data actually reaches.

**Temperature calibration fitted to 0.50** — the floor of the scan — because a
perfectly separated validation set makes the NLL optimum run to `T → 0`.
Calibration may soften; it must never sharpen. Constrained to `T ≥ 1`, after
which the abstention distance does the real work and the temperature is an
honest no-op.

**"Hard" examples were not hard.** The first attempt nudged an LED's hue towards
the red/amber boundary and the classifier shrugged and got them right. Genuine
ambiguity needs genuinely *split* evidence — two indicators lit at once — and it
must be held out of training entirely. Teaching a classifier to pick one label
for such a frame would train away the behaviour that scores.

### Measuring what we assumed

**Speculation had never actually worked.** ⚑ The design note claimed a
latency win; nobody had asked the code to demonstrate one. The first run of
`scripts/speculation_report.py` found **four speculative calls across the whole
suite, zero joins, zero milliseconds hidden**. The mechanism was fine — the
scenarios were not. Nearly every turn was a single chunk with `end_of_turn`
set, so speculation, which by design fires only *before* a turn ends, never got
a chance. Real recognisers emit several partials per turn, and the guide's own
input list says "text chunks **with end-of-turn markers**", plural. After
streaming the scenarios properly: **50% join rate, 1100 ms hidden.**

**The agent parroted itself.** ⚑ Rewriting those scenarios exposed it. After
emitting a mid-turn acknowledgment, `_agent_is_speaking` stayed true, so the
user's own sentence *continuing* scored as a barge-in against us. That branch
has low rule confidence, which left the learned model free to override it with
`REPEAT_REQUEST` — and the agent replied *"I said: On Thursday — got it."* to
someone who had not asked it to repeat anything. Overlap now counts only at a
turn boundary, and `REPEAT_REQUEST` joined the branches the model may not
decide alone.

**Intent was never inferred from speech.** "I need to get to Hyderabad on
Thursday" never says the word *flight*, so intent stayed `None` and every
intent-tagged tool remained invisible. Slot-driven inference existed for the
perception path only.

**A newer guess did not retire the older one.** Two speculative searches ran
concurrently and one answer was always discarded.

### Tooling and scoring

**`Trace.emit()` collided on payload keys named `kind`, `name` or `t`.** A tool
fault record naturally wants its own `kind`. Header parameters are now
positional-only. This would have corrupted the one artefact the entire score is
read from.

**The scorer counted a retry-after-failure as a stale re-run.** The public suite
names retries explicitly; the effect never landed, so retrying is correct.

**Two scenarios fired their interruption after the call had already completed**,
testing nothing at all. Found by reading the trace rather than the pass/fail.

**The scorer penalised correct silence.** Answering "mhm" is not fast, it is
rude. Turns whose floor policy is `CONTINUE` are excluded from the latency
measure — narrowly, and only those; barge-ins and corrections are still measured.

### Building the live demo, and what it exposed

The demo was built because the project had no wow: a text harness whose
cleverness is invisible. Swapping the virtual clock for a wall clock was the
whole change — `LiveClock` implements the two methods the agent actually uses
(`now`, `sleep`) and everything else is imported unmodified. Three of the four
findings below came from that exercise rather than from the demo code itself.

**Inline media had never worked, in either modality.** ⚑ The protocol offers
`path` *and* `data_b64`, and both `ground_frame` and `ground_audio` collapsed
them:

```python
source = getattr(event, "path", None) or getattr(event, "data_b64", None)
```

Both are `str`, and the field name is the only thing that says which encoding it
is. That `or` discarded it and handed a base64 string to `Image.open` and
`wave.open` as a *filename*, so every inline frame reported "frame could not be
decoded" as though the image were corrupt. Nothing caught it: all 29 scenarios
delivered media by path, the fuzzer perturbs timing rather than wire encoding,
and `test_adapter.py` translates schemas rather than payloads. The guide never
promises media arrives as a path — a harness handing us bytes would have scored
zero on every visual and audio scenario, the half of the hidden set carrying the
1.5× multiplier. `perception.payload_of` now resolves the fields by name, and
the equivalence is asserted rather than assumed: all 14 real media files ground
to byte-identical payloads via both routes, OCR's `E4` read included. `S30`
covers it at scenario level so a regression lands in the scorecard and not only
in pytest.

**The demo script promised a naming question the agent never asked.** ⚑ It said
the two-LED router frame gets "asks which one — *by name*"; the agent said "I
couldn't make that out". `out_of_distribution` short-circuited before the
ambiguity check and threw away the candidate list it had just computed. The fix
came from measuring rather than guessing: as a multiple of each modality's
threshold, `router_led_ambiguous` sits at **2.1×**, `beeping_ambiguous` at 1.1×,
and `washer_unreadable` at **2100×**. Just outside means "between classes" and is
answerable with "is it A or B?"; three orders of magnitude outside means
"resembles nothing" and is not. Gated on rival mass too, because
`sound_ambiguous` is mildly out of distribution with a runner-up at 0.032 —
naming that would invent a rival to make a question sound specific. Exactly one
scenario changed behaviour, and it is the one whose own description asks for "a
specific clarification".

**Blur was a louder reason than a specific one.** Found immediately by the fix
above: `_fuse` set the retake question unconditionally, so "is it the power LED
or the WAN LED?" was overwritten by "the picture is too blurry to read the
panel". Vaguer *and* false — a photograph of indicator lights has no panel text
to read, so an empty read is not evidence of blur.

**`requestAnimationFrame` is suspended while a tab is not compositing.** Every
progress bar froze at "0 ms" the first time the demo was driven headlessly. A
demo whose bars stop when the presenter alt-tabs to their slides is a demo that
dies on stage; `setInterval` instead.

**Two servers, one port.** `scripts/view.py` has served 8770 since the viewer
landed, and the demo claimed the same port — while the demo script asks for both
running side by side during a take. Moved to 8771 before anyone found out mid-
recording.

### What the first real `docker build` found

Both of these had survived `scripts/check_dockerfile.py`, which checks that COPY
sources *exist* — not that the things importing them are in the image.

**`docker run --rm parley pytest` died at collection.** ⚑ The Dockerfile's own
header advertises that command. `demo/` was never copied in, and
`tests/test_live.py` imports `demo.live`, so the whole suite refused to start —
not one test failed, none ran. Copying `demo/` was not sufficient either: the
bare `pytest` console script does not put the working directory on `sys.path`,
which `python -m pytest` does. `parley` and `harness` resolve regardless because
`pip install -e .` registers them; `demo/` is deliberately not a distributed
package. `tests/conftest.py` now inserts the repository root, so both invocations
work — and anyone reproducing our numbers with bare `pytest` no longer sees an
import error instead of a suite.

**A Linux container printed a Windows path.** Host `__pycache__` was being baked
into the image, so a traceback inside the container pointed at
`D:\samsungprism\tests\test_multimodal.py`. There was no `.dockerignore` at all;
adding one removed the stale bytecode and cut the build context from 8.63 MB to
16.7 kB. Verified afterwards that no `.pyc` in the image references a host path.

**And one test that was wrong rather than the code.** `docker run parley pytest`
failed `test_inline_frame_still_reads_the_error_code` on a perfectly good build:
the image installs neither `vosk` nor `rapidocr`, so there is no panel read to
assert. The frame still classified correctly at 0.99 from colour alone, which is
the argument for keeping OCR a fusion step rather than a dependency. The test now
skips on `ocr.available()` like the rest of the optional-extra suite.

**The client could backdate its own turns.** `LiveSession.feed` overwrites `t`
unconditionally. Without that a browser could stamp every chunk `t=0` and make
response latency — 15% of the score — look arbitrarily good. Now asserted.

### Re-reading the literature

**A correction that turned out not to be needed.** The 25 Sep sweep surfaced
*"Continue, Adapt, or Yield"* (arXiv 2609.13117) — our exact floor taxonomy, in
someone else's title, dated two weeks before `35679c2` committed those verbs.
The first draft of `PRIOR_ART.md` §A.1 duly announced that a novelty claim had to
be withdrawn. Checking before rewriting showed there was none: `RESEARCH.md`
§R1.4 had credited the paper from the day the verbs were adopted, and the README
only ever claimed the *pairing* of the two axes. Recorded anyway, because a
rushed "honest correction" that invents a sin to confess to is not honesty — it
is inaccuracy with better manners.

What the deeper read did buy: their measured gap (humans adapt **68.2%** of the
time on collaborative cues, the model they evaluate **34.8%** — state of the art
under-adapts by half), confirmation that their scope is the speaking floor only,
and one named hole of ours from the cost-aware speculation literature. Four
limitations are now written down in `DESIGN.md` §14, because a limitation you
have named is a design decision and one you have not is a bug waiting for a
judge to find.

---

## Things deliberately not done

- **No LLM in the loop.** 75% of the score is the coordination layer and the
  latency block is 15%; inference time is pure cost. The kernel is the product.
- **No cross-session memory.** Out of scope per the guide, and a tempting way to
  fail a hidden-set scenario.
- **No tool names in the kernel.** Everything comes from the manifest, because
  "unseen tools" is named in the public suite and therefore near-certain in the
  hidden set.
- **No tuning against the public suite.** Passing thirty scenarios we wrote
  proves little. The fuzzer is the real test: 9000 perturbed runs, invariants
  only.

## Standing risks

1. **The real evaluation kit is not in hand.** Every interface assumption needs
   re-checking when it lands. Mitigated but not eliminated: `harness/adapter.py`
   plus `tests/test_adapter.py` demonstrate the agent running a full scenario
   off a deliberately alien schema — seconds instead of milliseconds, different
   type names, nested payload envelopes, heartbeat events, out-of-order
   delivery — with correct cancellation and exactly one booking. What that
   proves is that the *boundary* is real, not that the real kit will fit
   through it without work.
2. ~~**`docker build` is unverified on this machine**~~ — **closed.** The engine
   would not start because two directories held orphaned AF_UNIX socket files
   that Windows could not delete or rename (`Docker\run\sailor-ingest.sock` and
   `docker-secrets-engine\engine.sock`, both zero-byte reparse points reporting
   "the file cannot be accessed by the system"). Docker's own dialog offered only
   "Quit" or "Reset to factory defaults"; renaming the two directories aside was
   enough, and preserved the four existing images a factory reset would have
   destroyed. One caveat learned the hard way: clearing them *one at a time* does
   not work, because each crashed start orphans a fresh socket — both have to go
   before a single clean start. `docker build` then succeeded first time, and all
   three commands the Dockerfile advertises pass. See `SUBMISSION.md` §3.
3. **Deadline discrepancy**: the deck says 25 Sep, the team reports 30 Sep. Plan
   to the 25th.
4. **Team name** is `ThaparPatiala_<TEAM>` throughout and must be substituted
   before the release tag.
