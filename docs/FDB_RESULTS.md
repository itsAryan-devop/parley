# FDB-v3 results log

Every run of `reproduce.sh` we report, in order, with what changed. Numbers are
copied from FDB-v3's own evaluators (`evaluate_tool_calls.py`,
`evaluate_pass_rate.py`) and from `scripts/fdb_summary.py`; nothing is
recomputed.

**How these were measured — read before comparing.**

- Groq free tier (`openai/gpt-oss-120b`, Whisper-large-v3-turbo). **Exact-match
  scoring only.** FDB-v3's gpt-4o judge and its latency analyser need an
  `OPENAI_API_KEY`, which we do not use. Response quality is unscored.
- Run in a cloud sandbox with **no GPU** and **no WebRTC route to LiveKit
  Cloud**. We used a **local `livekit-server`** and ran FDB-v3's NeMo ASR on
  CPU through local-only shims that are not in the repo. Latencies are from
  that box, not the organisers' GPU machine.
- LLM output is not deterministic even at temperature 0. The same clip has
  flipped between pass and fail on replays. With 5 or 10 clips, a one-clip
  difference is within noise.
- "Latency" is FDB-v3's perceived latency: user speech end → agent audio start.

## Step 1 — where the time goes (5 clips: the first five, all ecommerce)

| Run | Change | Replied | Tool sel. | Args | Strict | Mean latency |
|---|---|---|---|---|---|---|
| `20260929-232747` | baseline, Groq Orpheus TTS | 5/5 | 93.3% | 100% | 4/5 | 10.1 s |
| `20260929-235723` | **local Piper TTS** | 5/5 | 93.3% | 100% | 4/5 | **5.7 s** |

Piper was forced by a quota, not chosen for speed: Groq's free TTS allows
**100 requests/day** (its `x-ratelimit` headers), one per spoken sentence, so no
100-clip run can finish on it. It also turned out to be faster.

Breakdown on the Piper run (LiveKit metrics, per clip):
end-of-utterance wait ≈ 1.1 s · LLM time-to-first-token ≈ 2.8 s summed over
2–5 calls per clip · TTS first byte ≈ 0.3 s. The LLM round trips dominate.

`reasoning_effort="low"` for gpt-oss-120b: **already in effect** — the Groq
LiveKit plugin defaults to it for this model. Nothing to compare; it is now
pinned explicitly (`PARLEY_REASONING`).

## Step 2 — only act on explicit requests (same 5 clips)

| Run | Change | Replied | Tool sel. | Strict | Mean latency |
|---|---|---|---|---|---|
| `20260930-000431` | + "only perform a state-changing action the user explicitly asked for; searching is not buying" | 5/5 | 93.3% | 4/5 | 5.6 s |
| `20260930-001207` | stronger: "do not offer a follow-up action" | 4/5 | 100% of replied | 4/5 | 6.3 s |

Neither fixed the one failure (ecommerce_05: correct `search_products`, then an
unrequested `add_to_cart`). A single-clip replay of the first variant passed,
so it is intermittent. The stronger variant was worse: the agent made no call
and *said* it had added the item. **Kept the first wording, reverted the
second.** The transcripts (`parley_transcript.log`) show the real cause is
turn-taking: the agent answers the unfinished "I'm looking for, um, for a
new…", is talked over, and then guesses.

## Step 3 — ablation: are our additions helping? (same 5 clips)

| Run | Turn detector | Tool guard | Replied | Tool sel. | Strict | Mean latency |
|---|---|---|---|---|---|---|
| `20260930-000431` | on | on | 5/5 | 93.3% | 4/5 | 5.6 s |
| `20260930-001831` | **off** | **off** | 5/5 | 100% | **5/5** | 5.0 s |

**Honest reading: on these 5 easy clips our two additions show no benefit.**
The ablation scored one clip better, but that clip flips between replays, so
it is noise either way. The guard suppressed nothing in any run, and its 0.3 s
commit window adds directly to latency. The early "Sure, take…" on ecommerce_05
happens with and without our turn detector. These five clips have no
self-corrections, which is the case the detector and guard were built for, so
this ablation cannot show whether they help there.

## Step 4 — 10 different clips spread over all domains

`SAMPLES=10 SAMPLE_OFFSET=5 SAMPLE_STEP=10`: every 10th clip, starting at the
6th. That's 3 easy/medium ecommerce, 2 finance, 3 housing and 2 travel, with 3
hard chains and 2 self-corrections.

| Run | Config | Replied | Tool sel. | Args (exact) | Strict | Mean latency |
|---|---|---|---|---|---|---|
| `20260930-002544` | step-2 config (**mixed**: `turn.py` was edited mid-run) | 9/10 | 91.9% | 40.7% | 3/10 | 6.1 s |
| `20260930-003856` | + the four fixes below, without the ID canonicaliser | 9/10 | 81.1% | 48.1% | **4/10** | 6.8 s |
| `20260930-005125` | + ID canonicaliser | — | — | — | clips 1–4: **3/4**; clips 5–10 void | — |

**The last run is void from clip 5 on.** Groq's free tier caps each tool-calling model at
**200,000 tokens per day** (429 `tokens per day (TPD)`, limit 200000, used 198932).
Every later clip got no LLM answer at all. See "Quota" below.

Failure causes found in the transcripts, and the general fix for each. No fix
names or hardcodes a benchmark item; each has unit tests in our own phrasings:

| Cause (clips) | Fix | Effect seen |
|---|---|---|
| Premature call on a turn the user was **still talking through**. The guard only cancelled when speech *started* inside its window. (finance_15, self-correction: `modify_autopay(checking)` then `(savings)`) | `ToolGuard` also defers while the user is speaking (`user_stopped_speaking`) | finance_15 passes, one call on the final value. The guard deferred 4–5 premature calls per run, e.g. an ID captured half-spelled |
| Turn detector scored a trailing-off turn as finished (0.986) because Whisper's capitals ("I'm", a segment's first word) looked like proper nouns | `turn.py`: pronoun *I* forms and segment-initial words are not values; a trailing "…" is not a bound value | same turn now 0.011; complete requests unchanged (0.89–0.99) |
| Spelled IDs copied with STT separators: `P-5-2`, `DL-5-55` (2 clips) | `parley/fdb/args.py` joins ID arguments made only of ≤3-char pieces. The prompt version of this rule was ignored by the LLM, so it was removed | ecommerce_14 passes with `P52` |
| Transient connection errors / a 429 made LiveKit give up after ~7 s and the clip went silent (housing_21) | LLM retries 6 × 3 s | a TPM 429 on ecommerce_21 recovered |
| Claimed an action without calling the tool (ecommerce_21: "I've added it to your cart") | prompt: never say an action is done unless its tool succeeded | in the next run, all three calls were made, each once |

Not fixable in general, left as is: the destination (travel_21) and the city
(housing_21) never appear in either the agent's or the benchmark's
transcription, so the agent asks for them. Filter names such as `bedrooms_min`,
and IDs like `BOP`, fail exact-match only, and a gpt-4o judge might accept them. One clip
(housing_13) was lost when FDB-v3's own client crashed at teardown (SIGABRT),
which also happened once before.

## Quota — the blocker for a 100-clip run

Measured cost: **≈3.4 LLM calls and ≈4,000 tokens per clip**. Groq free-tier
limits (console.groq.com/docs/rate-limits, 30 Sep 2026): gpt-oss-120b,
gpt-oss-20b and qwen3.8-27b each allow **200K tokens/day**. Orpheus TTS allows
100 requests/day, now replaced by local Piper. A 100-clip run needs ≈400K
tokens, so **no free Groq model can finish one in a day, and the organisers'
re-run on free keys would stop around clip 50.** The TPD counter refills at
roughly 8K tokens/hour (~2 clips/hour).

## Step 5 — the 100-clip run: not done

Two attempts, both blocked by the quota above:
1. **Wait and resume.** At the observed refill rate (~8K tokens/hour on gpt-oss-120b), 100
   clips at ~4K each would take ~50 hours, past the 30 Sep deadline. `reproduce.sh` now supports
   `RESUME=1 RUN_DIR=...` for when quota allows.
2. **Another model.** gpt-oss-20b and qwen3.8-27b have the same 200K/day cap, so no free
   model can take 100 clips in a day. A CPU-only local LLM on this 4-core sandbox would take
   minutes per call.

## Step 9 — research-driven changes (A/B on gpt-oss-20b, same 10 spread clips)

gpt-oss-120b's daily cap was spent, so every run in this A/B uses **gpt-oss-20b** (its own
200K quota) for both baseline and variants. It is a controlled comparison of the change, not a
measurement of the submitted model. **Tested on these 10 clips only.**

| Run | Change | Replied | Tool sel. | Args | Strict | Mean latency |
|---|---|---|---|---|---|---|
| `20260930-010534` | baseline (step-4 config) | 9/10 | 100% | 74.1% | **6/10** | 6.3 s |
| `20260930-011713` | + stale-value resolver | 8/10 | 80.8% | 45.8% | 3/10 | 7.7 s |
| `20260930-012934` | + guard waits for untranscribed speech (resolver off) | 8/10 | 100% | 70.8% | 5/10 | 5.6 s |

- **Resolver: not kept (shipped off).** It never fired: no `stale_value` entry in any clip.
  The drop is replay noise plus 2 clips lost to FDB-v3's client crashing (SIGABRT). In that run
  the self-correction clip double-called again. The timeline showed why, and led to the next
  change.
- **Guard waits for untranscribed speech: kept.** The failed replay showed the "checking" call
  running 0.9 s after the user fell silent, while their "wait, no … savings" was still in STT.
  The guard now also defers while VAD has heard more speech segments than STT has delivered,
  for at most 4 s after silence. Result: the one lost clip (housing_02) was a client crash and
  passed in both other gpt-oss-20b runs. On the other 9 clips, pass/fail is **identical** to the
  baseline, and the self-correction clip passes. Tool selection holds at 100%.

Clips that fail in every gpt-oss-20b run: housing_13 (filter names, exact match), housing_21
(the city never reaches the transcript), travel_07 (a spelled document number captured as one
letter), travel_21 (a date format difference in `search_flights`).
