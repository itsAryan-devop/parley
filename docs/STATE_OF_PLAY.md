# State of play — session log

Companion to [`../HANDOFF.md`](../HANDOFF.md). That file says *where things are*;
this one says *how they got there*, newest first, so a fresh session can tell a
deliberate decision from an accident.

Bugs are not repeated here — they live in [`BUILD_LOG.md`](BUILD_LOG.md),
grouped by what they teach. This is the narrative.

---

## Session 4 — 25 Sep 2026 (overnight, unattended)

Ran with a standing instruction to keep building, research periodically, and not
stop. Started from: 29 scenarios, 275 tests, ASR and OCR just landed.
Ended at: 30 scenarios, 348 tests, a live voice demo, and Docker deleted.

### Built: the live voice demo

The project's honest weakness was that it had **no wow**. A text harness whose
cleverness is entirely invisible is a hard thing to put in a five-minute video,
and the user said so directly — *"feels pretty basic, everyone could go for it"*.
That was correct.

`demo/live.py` + `demo/server.py` + `demo/index.html`. The design constraint was
that the demo must not be a second implementation: it swaps **one object**, the
clock, and imports the agent, kernel, floor manager, planner and mock
environment unmodified. `LiveClock` implements the two methods the agent actually
touches (`now`, `sleep`) on `perf_counter`.

That is the adapter boundary holding for a third time — `test_adapter.py` proved
it across tool schemas, `asr.py` across modality, this across *time itself*.

The timeline is drawn from the same trace records the scorer reads. A
cancellation appears on screen because `kernel(..., "invalidate")` was emitted,
not because the UI was told to draw one. If the trace is wrong, the picture is
wrong — the only honest way to demo a system whose claim is auditability.

Driven live, S02 renders the thesis in one frame: `search_flights` frozen at
29.7% of its bar, struck through, "invalidated by destination"; `search_hotels`
beside it filling to 100% and reaching the final answer untouched.

Later added: drag-and-drop photo upload and sample chips loading the exact frames
S10 and S11 score, so a presenter is not opening a file manager on camera.

### Found: inline media had never worked

Wiring photo upload exposed a live scoring bug. `ground_frame` and `ground_audio`
collapsed the protocol's two delivery fields:

```python
source = getattr(event, "path", None) or getattr(event, "data_b64", None)
```

Both are `str`, and the field name is the only thing saying which encoding it is.
That `or` handed a base64 string to `Image.open` as a *filename*, so every inline
frame reported "could not be decoded" as though the image were corrupt.

Nothing caught it: all 29 scenarios delivered media by path, the fuzzer perturbs
timing rather than encoding, and `test_adapter.py` translates schemas rather than
payloads. **A harness handing us bytes inline would have scored zero on all 13
visual and audio scenarios** — the half carrying the 1.5× multiplier.

Fixed with `perception.payload_of`, which resolves the fields by name and accepts
`data:` URIs. Proven equivalent rather than merely working: all 14 real media
files ground to byte-identical payloads via both routes, OCR's `E4` read included.

### The blind spot was more interesting than the bug

Taught the fuzzer to re-encode media inline (~40% of seeds). Then measured
whether that would have caught the original bug. **It would not** — 0 of 17
seeds on S10, 0 of 14 on S13.

The reason is structural, not a gap in the perturbation. A failed decode degrades
*gracefully*: the agent says it could not make the frame out and asks. No
duplicate effect, no pending call, nothing claimed without a warrant. It is a
**capability** failure, and the fuzzer asserts **safety** invariants by design.

Encoding-invariance is a *metamorphic* property — same bytes, different encoding,
same answer — needing two runs compared. It cannot live in a single-run invariant
check. `test_media_scenarios_are_encoding_invariant` does it, and against the old
bug it fails 12 of 13 scenarios where the fuzzer caught zero.

Both were kept, with the fuzzer's docstring stating plainly what it does and does
not detect, and the measured numbers, so nobody re-derives this from a
comfortable assumption.

### Research pass

Re-read three 2026 papers. The sweep surfaced *"Continue, Adapt, or Yield"*
(arXiv 2609.13117) — our exact floor taxonomy, in someone else's title, dated two
weeks before we committed those verbs.

The first draft of `PRIOR_ART.md` §A.1 duly announced that a novelty claim had to
be withdrawn. **Checking before rewriting showed there was none**: `RESEARCH.md`
§R1.4 had credited the paper from the day the verbs were adopted, and the README
only ever claimed the *pairing* of the two axes. The confession was fiction, and
it is recorded anyway — a rushed "honest correction" that invents a sin is not
honesty, it is inaccuracy with better manners.

What the deeper read genuinely bought:

- Their measured gap, now quotable: on collaborative cues humans adapt **68.2%**
  of the time; the full-duplex model they evaluate manages **34.8%**, otherwise
  ploughing on (42.4%) or shutting up (22.7%). **State of the art under-adapts by
  half** — which is exactly the binary our two-axis policy exists to break.
- Confirmation their scope is the speaking floor only. Nothing about in-flight
  tool calls, because their agents have none. That gap is where PARLEY lives.
- One named hole of ours from the cost-aware speculation literature: we never
  price a speculation, so we cannot decline an expensive one. Unfixable honestly
  against a mock environment where every call is free.

Four limitations now written down in `DESIGN.md` §15. A limitation you have named
is a design decision; one you have not is a bug waiting for a judge.

### Improved: abstention names what it is torn between

`DEMO_SCRIPT.md` promised the two-LED router frame gets "asks which one — *by
name*". It did not; it said "I couldn't make that out". The script was describing
behaviour the screen would not produce, breaking the one rule that file sets
itself.

Cause: `out_of_distribution` short-circuited before the ambiguity check,
discarding the candidate list it had just computed. The fix came from measuring.
As a multiple of each modality's threshold:

| frame | distance | verdict |
|---|---|---|
| `router_led_ambiguous.png` | **2.1×** | between classes — nameable |
| `beeping_ambiguous.wav` | 1.1× | between classes |
| `washer_unreadable.png` | **2100×** | resembles nothing — generic |

Just outside means "between classes" and is answerable with "is it A or B?".
Three orders of magnitude outside means "resembles nothing" and is not. Gated on
rival mass too, because `sound_ambiguous.wav` is mildly OOD with a runner-up at
0.032 — naming that would invent a rival to make a question sound specific.

Under both gates exactly one scenario changes behaviour, and it is the one whose
own description asks for "a specific clarification".

### Docker: built, verified, then deleted

Spent a large part of the night getting Docker Desktop's Linux engine to start —
the cause was orphaned AF_UNIX socket files Windows could neither delete nor
rename, in two directories, which had to be cleared **simultaneously** because
each crashed start orphans a fresh one. Renaming both aside worked, and preserved
the four existing images a factory reset would have destroyed.

The build then succeeded first time and found two real defects (below). And then
the user asked the obvious question: *why are we using Docker at all?*

**Checked instead of defending it. Nothing asks for it.** The submission is repo
URL, demo video, deck, AI disclosure. It was our own addition — and worse, it had
become a self-inflicted risk: trap #2 in our own checklist read "Docker never
actually built — the README's first command is `docker build`". That is
circular. It was only a trap because we made it the headline command.

Removed entirely on 25 Sep. Full accounting in `DESIGN.md` §14.

**The two defects it found were kept, because both bite with no container
involved:**

1. `pytest` from a clean checkout failed at *collection*. The bare console script
   does not put the working directory on `sys.path` the way `python -m pytest`
   does, so `tests/test_live.py` could not import `demo/`. Anyone reproducing our
   numbers that way would see the whole suite refuse to start. Fixed in
   `tests/conftest.py`.
2. The claim "inference needs nothing but numpy" had never been tested — and the
   image was shipping scikit-learn (188 MB, 29% of it) that no scenario and no
   test imports.

The honest accounting is that those were lucky by-products. A day spent on a
deliverable nobody asked for found two bugs; the same day spent on hidden-set
failure modes might have found more.

### Two mistakes of mine worth recording

- **Published a wrong number.** Told the user the image was 153 MB. It was read
  off `docker images` while still unpacking and never re-checked; the settled
  size was 641 MB. Lesson: read a measurement *after* the thing has settled.
- **Polled for a string I had not verified.** Two background waiters spun for
  ~3h45m watching the build log for `writing image sha256`, which BuildKit never
  prints. The build had finished long before.

### Also

- Added **S30** — the same frame delivered as `data_b64`, asserting the wire
  encoding cannot change a decision. Downscaled to 64×64 first, which is lossless
  for this purpose because the extractor resizes to 64×64 anyway (measured: label
  identical, confidence 0.994 → 0.993), keeping the scenario 8.5 kB instead of
  230 kB.
- Added `scripts/set_team_name.py` — one tested command for the seven placeholder
  sites plus the deck filename, because doing it by hand near a deadline is how
  one gets missed.
- Settled the deadline as **30 Sep** in three files so it stops resurfacing.

---

## Sessions 1–3 — 24–25 Sep 2026

Condensed; the detail is in [`BUILD_LOG.md`](BUILD_LOG.md).

| # | Built | Ended at |
|---|---|---|
| 1 | Protocol layer, virtual-clock harness, trace, mock environment | 68 tests |
| 2 | Coordination kernel, NLU, learned classifier with an honest ablation | 130 tests |
| 3 | Floor manager, planner, agent loop, multimodal grounding, scenario suite, rubric scorer, timing fuzzer, timeline viewer, deck, docs | 275 tests · 29/29 |

Three findings from those sessions that shape everything since:

- **The fuzzer earned itself immediately**, finding two bugs no hand-written test
  would reach: a call that vanished from the trace when two events landed on the
  same millisecond, and a genuine double-booking caused by putting `intent` into
  the idempotency key.
- **The learned classifier does not beat rules** (0.891 vs 0.876 under ASR
  noise). It ships only because the arbitrated ensemble edges both (0.922 vs
  0.907), and that is reported as small. The detour's real value was the bugs it
  exposed — stripping disfluency before cue matching lifted rule accuracy under
  noise from 0.809 to 0.891, probably the single most valuable fix in the project.
- **Speculation had never actually worked.** The design note claimed a latency
  win nobody had asked the code to demonstrate: four speculative calls across the
  whole suite, zero joins, zero milliseconds hidden. The mechanism was fine, the
  scenarios were not — nearly every turn was a single chunk with `end_of_turn`
  set. After streaming them properly: 50% join rate, 1100 ms hidden.
