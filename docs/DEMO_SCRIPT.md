# Demo video script — 5:00 hard cap

The deck argues; the video must **show**. One rule throughout: never describe
behaviour the screen isn't demonstrating at that moment.

Everything below is reproducible from a clean clone. Record at 1080p; the
timeline viewer and terminal are both dark, so the cut looks continuous.

**Before recording**

```bash
python scripts/run_scenarios.py --json runs/scores.json   # populates runs/
python scripts/view.py --no-browser                       # trace viewer on :8770
python demo/server.py                                     # live demo on :8771
```

Two servers, two ports, both wanted on screen. Start them before the take.

Do a **mic check on the live demo first.** Say the opening line once and confirm
the transcript appears; a recogniser that mishears the city mid-take is the one
failure mode that ruins the most important shot in the video.

---

## 0:00 – 0:25 · The problem, stated once

**Screen:** title slide, then straight to the live demo at `:8771`.

> "A voice assistant takes turns. People don't. They interrupt, they correct
> themselves mid-sentence, and they change their mind while the assistant is
> three tool calls into the plan they just abandoned.
>
> The obvious fix — throw the plan away and start over — is worse. It destroys
> work that was still valid and re-runs calls that already had side effects."

Don't linger. The demo is the argument.

---

## 0:25 – 1:30 · The flagship, spoken out loud and live

**Screen:** the live demo, full screen. Hold the mic button and speak.

> *"Find me a flight to Delhi on Tuesday. And a hotel in Goa."*

Two bars appear and start filling. **Wait for them to be visibly mid-flight** —
about two seconds — then cut in over the agent:

> *"No wait — Mumbai."*

Say nothing for three seconds. Let the screen do it: the flight bar freezes
red and strikes through, the hotel bar keeps filling to the end.

> "That's live. I interrupted it while two searches were running.
>
> The flight search read the destination slot, so it died — you can see where.
> The hotel search never read that slot, so it finished. And a new flight search
> went out on the corrected value.
>
> Every framework we looked at flushes the whole pipeline here. That kills the
> hotel search for nothing, and it has to be re-run later."

**This is the single most important 45 seconds in the video.** It is also the
only part that is genuinely live, so protect it: if the recogniser fumbles a
word, stop and re-take rather than talking over a wrong transcript.

*Fallback if the microphone misbehaves on the day:* type the same three turns
into the box — the agent cannot tell the difference, which is itself worth one
sentence — or drop to `scripts/view.py` at `?trace=S02_slot_correction` and
narrate the recorded trace instead. Do not attempt to fix audio on camera.

---

## 1:30 – 2:10 · The three cases that look like interruptions and aren't

**Screen:** `python scripts/run_scenarios.py -v S05 S17`

> "Not everything that arrives over our voice is an interruption. 'Hold on' is
> a floor grab — yield the microphone, touch nothing. 'Say that again' is
> answered from the transcript, never by re-running a tool. And 'mhm' is the
> user telling us they're listening; a voice-activity detector fires on it and
> stops the turn, which is the detector's mistake, not a request."

Point at the tool lane staying green across all of them.

> "In all three the work keeps running. Cancelling there and re-running
> afterwards is exactly the stale re-run the scoring penalises."

---

## 2:10 – 3:00 · The uncomfortable one: a cancel is a request, not a fact

**Screen:** `python scripts/run_scenarios.py -v S07`, then the viewer.

> "Here the user abandons a flight booking — but the cancel arrives *after* the
> booking already committed in the environment. The agent cannot know that."

Point at the kernel lane: `effect_probe` → `compensated`.

> "So it doesn't assume. It probes with the verifier the manifest declares,
> finds the booking, undoes it through the declared inverse, and says so out
> loud."

**Read the actual transcript line off the screen.** Then S18:

> "And when there's no verifier available, it says *that* instead — 'I'd
> already started creating that ticket and I can't confirm whether it went
> through.' Saying nothing is what makes a state snapshot quietly stop matching
> the world."

---

## 3:00 – 3:40 · Multimodal, including knowing when to shut up

**Screen:** back to the live demo at `:8771`. Click the **washer E4** sample.

> "A photograph of a washing machine panel. It's acknowledged immediately and
> decoded behind the acknowledgment, the panel is read, and the error code
> drives the manual lookup."

Point at the slot chip: `label washer_error_e4 · r1 · vision` — the provenance
says a camera bound that slot, not a sentence. Then read the remediation steps
off the final response.

Now click the **router · two LEDs** sample.

> "This one has two indicators lit. The evidence is genuinely split, so it asks
> which one — by name."

Read the question off the screen: *"I can't tell from the picture — is it router
wan led amber or router power led red?"*

> "Nothing was dispatched and no slot was bound. A classifier will happily
> report ninety percent confidence on an image like this; we measure the
> distance to the training distribution and refuse when it's too far.
>
> A hundred percent of undecidable frames are refused rather than guessed."

**Both samples are the exact files the scored scenarios use** (S10 and S11), so
what the audience sees is what the suite measures — worth saying in one line.

---

## 3:40 – 4:25 · Why we believe it generalises

**Screen:** `python scripts/fuzz.py --trials 60 --jitter 900` — let the dots run.

> "The hidden set is about sixty scenarios of adversarial timing. Passing the
> thirty we wrote proves very little — those are the cases we thought of.
>
> So we perturb everything: timestamps, tool latency, commit points, events
> collapsed onto identical timestamps, injected faults, truncated sessions.
> Then we assert only invariants — no duplicate state change, no call missing
> from the trace, nothing claimed that can't be warranted."

Show the final line.

> "One thousand seven hundred and forty perturbed schedules, every invariant
> held. It found
> two real bugs we'd never have written a test for: a call that vanished from
> the trace when two events landed on the same millisecond, and a genuine
> double-booking caused by putting the intent into the idempotency key."

---

## 4:25 – 5:00 · Close

**Screen:** `python -m pytest` (348 passing), then the scorecard table.

> "Thirty scenarios, every declared check passing. The coordination layer is
> the product — three quarters of the score is decided by what got executed and
> what got cancelled, not by model quality. There's no language model in the
> loop, and that's a design decision, not a shortcut.
>
> Everything you've seen runs from a clean clone with `docker run`, downloads
> nothing, and writes the trace you've been watching."

**Last frame:** the viewer on S02, held for three seconds in silence.

---

## Recording notes

- **Do not speed up the fuzzer.** The dots accumulating is the proof.
- Terminal at ~16pt; the scorecard table must be legible at 1080p.
- If a take runs long, cut §1:30–2:10 down to `BARGE_IN` only. Never cut §2:10 —
  the uncertainty handling is the hardest thing in the project to reproduce and
  the easiest to under-sell.
- Say "we don't know" out loud at least once. It is the point.
