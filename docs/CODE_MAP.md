# Code map — where everything lives and why

For a session that needs to change something and does not want to read 6,000
lines first. Companion to [`../HANDOFF.md`](../HANDOFF.md) (state) and
[`DESIGN.md`](DESIGN.md) (rationale).

---

## The shape of it

```
          ┌──────────────────── events in ────────────────────┐
          │ transcript chunks · WAV · PNG · interrupts        │
          └───────────────────────┬──────────────────────────┘
                                  ▼
                    parley/agent/nlu.py          interpret the chunk
                                  │              (incrementally, at repair onset)
                                  ▼
                    parley/kernel/policy.py      floor × work decision
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
   agent/floor.py        kernel/dispatcher.py   protocol/state.py
   what we SAY           what we RUN/CANCEL     what we KNOW
   (provable-speech)     (dataflow invalidation) (revisions, tombstones)
              │                   │                   │
              └───────────────────┼───────────────────┘
                                  ▼
                          harness/trace.py
                    the JSONL that IS the score
```

**The one ordering rule:** in `agent.py::_on_transcript`, cancellation happens
**before** state is mutated. A call is identified as stale by having read a
slot's *old* value; patch the slot first and the call looks consistent with the
new value and survives when it should die. Invisible in a passing demo, fatal on
the hidden set.

---

## `parley/` — the scored engine

Imports nothing from `harness/`. That boundary is tested, not asserted
(`tests/test_adapter.py`).

### `protocol/` — the wire

| File | Notes |
|---|---|
| `events.py` | Inbound. `TranscriptChunk` carries `end_of_turn`; media carries **either** `path` **or** `data_b64` — see the inline-media bug in `BUILD_LOG.md` |
| `actions.py` | Outbound. Every `Speak` carries `claims`, each with a `warrant`. That is what makes truthfulness auditable rather than intended |
| `state.py` | `SessionState` with a monotonic `revision`, per-slot revisions, and **tombstones** so "cleared" is distinguishable from "never existed". `slots` is flat name→value (the guide's shape); provenance rides in `slot_meta` |
| `manifest.py` | Dialect-tolerant parsing. Refuses to guess the mutating flag when a compensator is declared |

### `kernel/` — coordination

| File | Notes |
|---|---|
| `calls.py` | `CallOutcome` has eight values. The interesting one is `CANCELLED_UNCERTAIN` — a cancel is a *request*, not a fact |
| `ledger.py` | `derive_key(spec, intent, args)` **deliberately excludes `intent`**. Including it caused a real double-booking. Do not "fix" this |
| `dispatcher.py` | Cancellation-safe dispatch. `_settle_abandoned` is a done-callback because a task can be cancelled *before its coroutine body runs*, so an in-body handler is not enough. Also speculation join and verify→compensate→disclose |
| `policy.py` | `FloorPolicy` (continue/adapt/yield — verbs from arXiv 2609.13117, cited not claimed) × `WorkPolicy` (keep/selective/cancel-all). Seven `InterruptionKind`s map onto the matrix |

### `agent/` — the loop

| File | Notes |
|---|---|
| `agent.py` | The main loop. Read the module docstring — it is the design in ten lines. `_turn_open` distinguishes a continuing turn from a barge-in |
| `nlu.py` | `DESTRUCTIVE` is the frozenset of kinds the learned model may **not** decide alone. Rules arbitrate |
| `floor.py` | The provable-speech gate. `_unprovable` is why "Booked" is unreachable without a booking |
| `planner.py` | Manifest-driven. No tool name is hardcoded anywhere — "unseen tools" is in the public suite and therefore near-certain in the hidden set |
| `model.py` | JSON weights, numpy inference. Mentions sklearn only in a type hint; never imports it |
| `corpus.py`, `lexicon.py` | Training-corpus generation and manifest-derived vocabulary |

### `multimodal/` — perception

| File | Notes |
|---|---|
| `perception.py` | `Perception` has **three** outcomes: answer / ask / decline. `payload_of` resolves `path` vs `data_b64` — the field name is the only thing that says which encoding it is |
| `vision.py` | HSV hue-band mass + layout features. `_fuse` reconciles the colour classifier with OCR; glyphs outrank hue. Blur is a *reason*, never a gate, and never overwrites a more specific question |
| `audio.py` | Spectral features for **machine sounds** — beeping, grinding, clicking. Not speech |
| `asr.py` | Streaming wrapper over vendored Vosk. `STABILITY = 2` from a measured sweep. Optional extra; the scored engine imports nothing from here |
| `ocr.py` | RapidOCR wrapper. Optional extra |

---

## `harness/` — our reconstruction of the kit

The official evaluation kit was never released. This is a spec-faithful replica.

| File | Notes |
|---|---|
| `clock.py` | `VirtualTimeLoop` subclasses `SelectorEventLoop` and overrides `time()`. `_clock_resolution` is pinned to 1 ns because Windows' ~15.6 ms would batch the adversarial timing the hidden set is built from |
| `mockenv/env.py` | Mutating tools **commit partway through** their latency window (`commit_fraction`). That is what makes `CANCELLED_UNCERTAIN` reachable |
| `trace.py` | Append-only JSONL, flushed per record. Header params are positional-only (`/`) so a payload may legally contain a key named `kind`/`name`/`t` |
| `scoring.py` | Rubric reconstruction: Task 40 / Recovery 35 / Latency 15 / Safety 10, × 0.80–1.20 |
| `fuzz.py` | Perturbs jitter, latency scale, commit point, collapse, faults, redelivery, dropped EOT, spurious VAD, **and media encoding**. Asserts *invariants*, never expectations |
| `adapter.py` | Translates a deliberately alien schema. Proof the boundary is real |
| `runner.py` | Producer/consumer split so event timestamps mean what they say |

---

## `demo/` — the live voice demo

Not scored. Not imported by anything in `parley/` or `harness/`.

| File | Notes |
|---|---|
| `live.py` | `LiveClock` (real `perf_counter`) + `LiveSession`. **`feed()` overwrites the client's `t`** so a browser cannot backdate its turns and flatter our latency |
| `server.py` | WebSocket + static. ASR decode runs in a **thread** so a 30 ms Kaldi call cannot stall the loop the latency claims are measured on |
| `index.html` | Timeline, transcript, slot chips with provenance. `setInterval` not `requestAnimationFrame` — rAF is suspended when the tab is not compositing |

Port **8771**. The trace viewer is 8770.

---

## `scripts/` — everything reproducible

```bash
python scripts/make_media.py          # synthesise frames + clips
python scripts/train_perception.py    # vision + audio weights -> JSON
python scripts/train_classifier.py    # interruption weights -> JSON
python scripts/make_scenarios.py      # scenarios/*.json
python scripts/run_scenarios.py       # the scorecard
python scripts/fuzz.py --trials 60    # adversarial timing
python scripts/view.py                # timeline viewer on :8770
python scripts/set_team_name.py       # the placeholder substitution
python scripts/perf_report.py         # wall-clock budget
python scripts/speculation_report.py  # is speculation paying for itself?
python scripts/asr_stability.py       # the STABILITY sweep
```

`_console.py::utf8()` is imported by every reporting script — a cp1252 console
would otherwise crash them on the first em-dash.

---

## Adding things

**A scenario:** append to `SCENARIOS` in `scripts/make_scenarios.py`, run it,
then `python scripts/run_scenarios.py -v S31`. Scenarios are *data* — the
directory's stated virtue is being readable without reading any Python, so keep
large payloads out of it.

**An interruption kind:** `policy.py` (the enum + matrix), `nlu.py` (cues and
features), then a scenario. If the learned model must not decide it alone, add
it to `DESTRUCTIVE`.

**A tool:** nothing in the kernel. Add it to a scenario's manifest and a handler
in `harness/mockenv/env.py`. The planner derives everything else.

**A perception class:** add media under `media/dataset/`, re-run
`train_perception.py`. Check the undecidable set still abstains — that is the
part that scores.
