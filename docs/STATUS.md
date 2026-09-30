# Status — morning of 30 Sep 2026

For Aryan. Short version first, details in [`FDB_RESULTS.md`](FDB_RESULTS.md) (every run)
and [`RESEARCH.md`](RESEARCH.md) Round 4. Everything is on branch
`claude/dreamy-volta-fedkme`, draft PR against `master`. Nothing is merged, tagged or submitted.

## ⚠️ Read first: decisions only you can make

1. **Free Groq cannot run all 100 clips in a day, for us or for the organisers.** Every
   tool-calling model on Groq's free tier (gpt-oss-120b, gpt-oss-20b, qwen3.8-27b) is capped at
   **200,000 tokens per day**. A clip costs about 4,000 tokens (≈3.4 LLM calls), so 100 clips
   need about 400K. We hit the cap tonight on clip 5 of a 10-clip run. The organisers
   re-run our `reproduce.sh`, and on free keys that re-run would stop around clip 50. Options,
   your call:
   - **(a) Serve the LLM locally on their 48 GB GPU.** The guide allows it. gpt-oss-20b fits,
     and it scored as well as or better than 120b in our small runs. **The agent side is now
     built:** set `PARLEY_LLM_BASE_URL` (plus optional `PARLEY_LLM_API_KEY` and `PARLEY_LLM`)
     and the agent sends its LLM calls to any OpenAI-compatible server, for example
     `vllm serve openai/gpt-oss-20b` and then
     `PARLEY_LLM_BASE_URL=http://localhost:8000/v1 bash reproduce.sh`. The code path was
     smoke-tested end to end on one clip, against Groq's OpenAI-compatible endpoint. What's
     still missing: the serving step itself (installing vLLM and starting the server on their
     GPU) was not scripted into `reproduce.sh`, because it can't be tested without a GPU.
   - **(b) Declare a paid hosted API.** This conflicts with our no-paid-services rule.
   - **(c) Keep free Groq and document that a full run takes two days of quota,**
     using `RESUME=1 RUN_DIR=... bash reproduce.sh`. This risks a partial score if they don't resume.
2. **Text-to-speech is now local Piper, not Groq's Orpheus.** Orpheus's free tier allows
   100 requests a day, one per spoken sentence, so no 100-clip run could finish. Piper
   (MIT, public-domain voice) runs on CPU and cut mean latency from 10.1 s to 5.7 s.
   `PARLEY_TTS=groq` switches back. Review the voice quality in one clip.
3. **Two decks exist.** The new 8-slide `deck/ThaparPatiala_TEAM_FDBv3_ppt.pptx` covers the
   FDB-v3 work. The old kernel-era deck is still there. Pick one to submit.
4. **Rotate the LiveKit and Groq keys.** They were in this cloud session's environment all
   night. They were never written to git or to any log.

## What was done (steps 1–9)

| Step | Result |
|---|---|
| 1 Latency | 10.1 s → **5.7 s** mean, from local TTS. Breakdown: end-of-utterance wait ~1.1 s, LLM ~2.8 s over 2–5 calls, TTS ~0.3 s. `reasoning_effort=low` was already the Groq plugin's default for gpt-oss, so there was nothing to A/B; it is now pinned. |
| 2 Explicit-request rule | Added. No measurable change. A stronger variant made things worse and was reverted. The remaining failure is turn-taking, not policy. |
| 3 Ablation | **Unflattering:** on the first 5 (easy) clips, turning our turn detector and guard off scored 5/5 vs 4/5, and was 0.6 s faster. It is within noise, and those clips contain no self-corrections. Written up as measured. |
| 4 10 spread clips | Found and fixed 4 general bugs: the guard now waits for silence; Whisper capitals no longer fool the turn detector; spelled IDs are canonicalised (`P-5-2` → `P52`); LLM retries survive a 429. Strict pass went 3/10 → 4/10 on gpt-oss-120b, and **6/10 on gpt-oss-20b** in the clean A/B baseline. |
| 5 100-clip run | **Not done**, blocked by the daily token cap (above). Two attempts, recorded in `FDB_RESULTS.md`: waiting for refill (~8K tokens/hour, so ~50 h) and other models (same cap). |
| 6 README | Rewritten for the new guide. The old one is preserved as `docs/KERNEL.md`. |
| 7 Deck | 8 slides, honest numbers, `deck/build_fdb_deck.js` rebuilds it. |
| 8 This file | — |
| 9 Research | 15 lookups. The FDB-v3 paper's own diagnosis; LiveKit issue #3702; other teams: Keel (48/100 strict, exact-match, Gemini Live), SentinelEdge, Interject; Smart Turn v3. Past PRISM winners aren't published. Two improvements A/B'd: the guard waiting for untranscribed speech was kept; the stale-value resolver was dropped (it never fired). |

## Final numbers (exact-match, small samples — see caveats)

| Clips | Model | Strict pass | Tool sel. | Args | Mean latency |
|---|---|---|---|---|---|
| first 5 | gpt-oss-120b | 4/5 | 93.3% | 100% | 5.7 s |
| 10 spread | gpt-oss-120b | 4/10 | 81.1% | 48.1% | 6.8 s |
| 10 spread | gpt-oss-20b (A/B baseline) | **6/10** | 100% | 74.1% | 6.3 s |
| 10 spread | gpt-oss-20b + stale-value resolver | 3/10 (2 clips lost to the benchmark client crashing) | 80.8% | 45.8% | 7.7 s |
| 10 spread | gpt-oss-20b + guard waits for untranscribed speech (**kept**) | 5/10 (1 clip lost to a client crash; identical to baseline on the other 9) | 100% | 70.8% | **5.6 s** |

Caveats: **exact-match only**, since there's no OpenAI key for FDB-v3's gpt-4o judge. The runs
used a **local LiveKit server and CPU-only ASR** in a sandbox. Replays of the same clip flip
between pass and fail, so ±1–2 clips out of 10 is noise. For scale, the paper's stock
cascaded agent scores 0.45 on all 100 clips *with* the judge, and Keel reports 48/100 with
exact match.

## What failed or is still open

- No 100-clip run (quota).
- FDB-v3's own `livekit_inference.py` client sometimes aborts at teardown (SIGABRT, 1–2 clips
  per 10 here). Its result is then discarded. For a real run, re-run those clips with
  `RESUME=1` after deleting their `result_parley.json`.
- Unfixable in general: the spoken destination or city sometimes never reaches either
  transcript. Filter names and IDs fail exact match where a judge might accept them.
- The stale-value resolver (from SentinelEdge's idea) never triggered in its A/B. It is shipped
  **off** (`PARLEY_RESOLVER=1` to try it).
- Latency is dominated by LLM round trips (2–5 per clip).

## Checklist — only humans can do these

- [ ] Decide the LLM/quota question above (item 1) before anything is submitted.
- [ ] Record the 3–5 min video (a real interruption on a benchmark clip, then the camera extension).
- [ ] More real device frames for the camera extension, especially a router with LEDs lit and a
  washer display. Start `fdb/agent_extension.py` with `PARLEY_EXT_DEBUG_DIR=<dir>` so each frame
  it diagnoses is saved. Real "No Signal" TVs already work (README, Extension section).
- [ ] Fill in the team name: `ThaparPatiala_<TEAM>` appears in the README and both decks.
- [ ] Read and sign `DISCLOSURE.md` (AI usage); `docs/AI_PROMPT_LOG.md` has tonight's prompt.
- [ ] Confirm the deadline and time zone with the organisers.
- [ ] Make fresh LiveKit and Groq keys and rotate the old ones.
- [x] PRs #1–#4 merged (#5: deck/STATUS follow-up).
- [ ] Cut the release tag.
- [ ] Submit the form.

## If a teammate is taking over

- **Branch:** `master` (PRs #1–#4 merged). Everything is pushed,
  including every run's reports, logs and transcripts in `runs/fdb/` (index:
  `runs/fdb/README.md`). No audio, keys or benchmark data are committed.
- **Run it on a normal machine** (with WebRTC access to LiveKit Cloud): set `LIVEKIT_URL`,
  `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and `GROQ_API_KEY`, then `SAMPLES=5 bash reproduce.sh`.
  Use Python 3.10 or 3.11, and have `ffmpeg` installed. A GPU is optional; FDB-v3's ASR scorer
  uses it if present.
- **How the logs here were made:** in a cloud sandbox that has no GPU and cannot reach LiveKit
  Cloud's media. Three local-only workarounds were used and are deliberately not in the repo:
  1. `model.cuda()` made a no-op in FDB-v3's runner, so its ASR runs on CPU;
  2. a local `livekit-server` (`LIVEKIT_URL=ws://127.0.0.1:7880`);
  3. the sandbox's HTTPS proxy bypassed for localhost only, because livekit-agents passes
     `HTTPS_PROXY` explicitly and ignores `NO_PROXY`.

  You do not need any of these on a laptop with a normal network.
- **Quota:** each Groq account gets 200K LLM tokens a day, ~50 clips. The unblocking path is
  `PARLEY_LLM_BASE_URL` (see item 1 above), or `RESUME=1 RUN_DIR=runs/fdb/<run>` across days.
- **Before pushing:** run `python -m pytest` (the 10 `tests/test_asr.py` failures without `vosk`
  are expected) and `bash -n reproduce.sh`. Append your prompts to `docs/AI_PROMPT_LOG.md`.
