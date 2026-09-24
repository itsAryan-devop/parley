# Prior art review — Devaansh's Theme 05 prototype

Reviewed: [DevaanshGupta8/interruptible-realtime-agent](https://github.com/DevaanshGupta8/interruptible-realtime-agent)
@ `88e9d88`, read in full (33 files, ~2,800 lines).

Devaansh is on this team, and this prototype was built independently and in
parallel with PARLEY. **No code was taken from it.** What follows is what we
learned, what changed here as a result, and one defect worth fixing on his side.

---

## 1. What it got right that we had not

### Real perception, before we had any

The prototype ran **faster-whisper `tiny.en`** for speech and **RapidOCR** for
frames. At the time of review PARLEY's audio path classified sounds and its
vision path measured hue mass — neither could transcribe a sentence or read an
error code. That is a capability gap, not a stylistic difference: his agent can
be pointed at a real appliance and read `E4` off the panel.

This review is the direct reason `parley/multimodal/asr.py` and
`parley/multimodal/ocr.py` exist.

### The Whisper disfluency trap — his sharpest find

`iragent/perception.py:44`:

```python
initial_prompt="Um, I want to go to, uh, no wait, actually somewhere else."
```

Whisper **removes disfluencies by default.** "to Denver, uh, no sorry, Dallas"
comes back as "Dallas" — and with it goes the entire reparandum / interregnum /
repair structure that the seven-way interruption taxonomy is built on. An agent
wired to Whisper without this prompt would show a self-repair detector that
mysteriously never fires, and the cause would look like an NLU bug for weeks.

Priming the decoder with a disfluent sentence keeps the markers. It is a
genuinely non-obvious insight and it is his.

**What we did with it.** We verified the failure mode, then went a different
way: a *streaming* recogniser (Vosk) emits the repair marker as it is spoken and
needs no priming at all. The measured payoff on the reference clip:

| | |
|---|---|
| `denver` spoken | 1.77–2.28 s |
| `oh no sorry` | 2.61–3.93 s |
| `dallas` spoken | **4.44 s** |

The correction is actionable at ~2.9 s — **1.5 s before the corrected value
exists**. Whisper cannot do this at any prompt, because it decodes whole clips.
His insight is what made us look at the problem the right way round.

### Ordered transcript application

`iragent/agent.py:158` — `asr_next` / `asr_ready` apply transcripts strictly in
arrival order, so a slow first clip cannot overwrite a correction carried by a
later one. We had no equivalent because our audio path classified rather than
transcribed; the moment ASR landed, we needed it.

### Blur as an abstention trigger

`_sharpness` (variance of the Laplacian) gives a *reason* to ask — "hold the
camera closer" — where our Mahalanobis abstention only gives "I'm not sure".
Adopted in `ocr.py`, with a correction: see §3.

---

## 2. Where PARLEY is ahead

| | Prototype | PARLEY |
|---|---|---|
| Scenarios | 10 | 29 |
| Tests | 14 (NLU only) | 285+ |
| Timing fuzzer | listed as future work (`RESEARCH.md` §5.7) | built; 2,030 runs clean |
| Clock | wall-clock, 4 ms polling | virtual, deterministic, 1 ns resolution |
| Duplicate prevention | backend idempotency (see §4) | claimed pre-dispatch in the ledger |
| Docker / deck / docs | — | present |

His `RESEARCH.md` §5 lists seven risks for the hidden set. Five were already
closed here; §5.7 ("fuzz the harness… assert the invariants rather than exact
traces") describes `harness/fuzz.py` almost exactly.

The wall-clock harness is the most consequential difference. Latency is 15% of
the score and is read from trace timestamps; measuring it with `time.monotonic()`
and a 4 ms poll makes every number carry scheduler noise, and makes an
adversarial-timing failure unreproducible when it does appear.

---

## 3. Where our measurement disagreed with the borrowed idea

We adopted blur detection and then found that **gating on it is wrong.**

Measured on a reference panel: a Gaussian blur of radius 2 drops
variance-of-Laplacian from **141.9 to 1.0** — while OCR still reads `E4`, the
model number and the brand perfectly. Treating "blurry" as a veto would make the
agent ask the user to retake a photograph it had *already understood*, trading a
correct answer for a pointless question.

So in `ocr.py` blur is a **reason, never a gate**. Recognising the glyphs is
itself the proof the frame was sharp enough; the statistic is consulted only
when the read comes back empty, to explain why (`FrameText.needs_retake`).

---

## 4. A defect worth fixing in the prototype

**Duplicate state-changing calls are prevented by the mock backend, not by the
agent.**

`coordinator.py:79-81` refuses a write whose idempotency key is already in
`committed_keys`:

```python
key = idempotency_key(self.session_id, tool.name, args) if tool.writes else None
if key and key in self.committed_keys:
    return None, None          # never double-commit
```

But `committed_keys` is only populated in `on_result` — i.e. **when the tool
result arrives**. Between cancelling a write and its result landing, the set is
empty for that key, so a re-plan in that window can issue a *second* call
carrying the same idempotency key.

It does not show up in the local suite because `mock_tools.py:105-106` replays a
known key instead of committing again, and replays are never appended to
`commits` — which is the list `scorer.py` counts. The backend covers for the
agent, and the scorer then reports zero duplicates.

The exposure is that the brief makes duplicate avoidance **the agent's**
responsibility. If the official harness does not implement idempotent replay —
and there is no reason to assume it does — this is a live double-booking against
the 10% safety block, in exactly the adversarial-timing conditions the hidden set
is built from.

**Suggested fix.** Claim the key at *issue* time rather than at result time, and
release it only on a definitive "did not commit". That is what
`parley/kernel/ledger.py` does; the claim happens before dispatch, so the second
call never leaves the agent regardless of what the backend would have done.

Worth adding a scenario that asserts it directly: barge-in mid-write, re-plan
immediately, and assert `len(commits) == 1` against a backend with idempotent
replay **disabled**. A fuzzer would find this; a demo never will.

---

## 5. Also worth noting

- **No LICENSE file.** Defaults to all-rights-reserved. Fine within one team,
  but it should be added before the repo is public.
- **Runtime model download** (~75 MB Whisper + OCR weights on first run). Argued
  as fitting the 300 s warm-up hook, which is reasonable *if* the evaluation host
  has egress. Nobody has confirmed that it does. The asymmetry is unpleasant: if
  the assumption holds he scores normally; if it fails the agent does not start
  at all. PARLEY commits its weights (`models/`, ~68 MB) so the question never
  arises.
- The `_amend` path (`agent.py:514`) — reconciling a write that committed before
  the user changed their mind — is a genuinely good idea and close to our
  compensator flow. Worth comparing the two directly.
