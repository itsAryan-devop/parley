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
     and it scored as well as or better than 120b in our small runs. This needs a small
     `PARLEY_BACKEND` change: an OpenAI-compatible `base_url` for vLLM or Ollama, plus a
     serving step in `reproduce.sh`. Not built or tested here: no GPU.
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
| 9 Research | 15 lookups. The FDB-v3 paper's own diagnosis; LiveKit issue #3702; other teams: Keel (48/100 strict, exact-match, Gemini Live), SentinelEdge, Interject; Smart Turn v3. Past PRISM winners aren't published. Two improvements tried (below). |

## Final numbers (exact-match, small samples — see caveats)

| Clips | Model | Strict pass | Tool sel. | Args | Mean latency |
|---|---|---|---|---|---|
| first 5 | gpt-oss-120b | 4/5 | 93.3% | 100% | 5.7 s |
| 10 spread | gpt-oss-120b | 4/10 | 81.1% | 48.1% | 6.8 s |
| 10 spread | gpt-oss-20b (A/B baseline) | **6/10** | 100% | 74.1% | 6.3 s |
| 10 spread | gpt-oss-20b + stale-value resolver | 3/10 (2 clips lost to the benchmark client crashing) | 80.8% | 45.8% | 7.7 s |
| 10 spread | gpt-oss-20b + pending-speech guard | STEP9_PENDING | | | |

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
- [ ] Take the device photos for the camera extension (with the teammate on `extension-camera`).
- [ ] Fill in the team name: `ThaparPatiala_<TEAM>` appears in the README and both decks.
- [ ] Read and sign `DISCLOSURE.md` (AI usage); `docs/AI_PROMPT_LOG.md` has tonight's prompt.
- [ ] Confirm the deadline and time zone with the organisers.
- [ ] Make fresh LiveKit and Groq keys and rotate the old ones.
- [ ] Review and merge the draft PR, then cut the release tag.
- [ ] Submit the form.
