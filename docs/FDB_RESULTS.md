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
