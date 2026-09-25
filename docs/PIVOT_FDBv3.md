# Pivot to Full-Duplex-Bench v3 — the real scoring path

> **Why this file exists.** The organisers released an **updated** Theme 05 guide
> (`Theme05_Participant_Guide_UPDATED_FBD.docx`, seen 25 Sep 2026) that replaces
> the assumptions PARLEY was built on. The old guide said the evaluation kit was
> never published, so we reconstructed one (`harness/`) and built a pure-Python,
> no-LLM engine (`parley/`) scored against our own rubric. **That is no longer
> what gets scored.** This document is the grounded plan to pivot, written after
> reading the actual benchmark repo.

Source of truth for everything below: the FDB-v3 repository, cloned and read at
`github.com/DanielLin94144/Full-Duplex-Bench` (`v3/` directory; paper arXiv
2604.04847). Quotes and mechanics are from `v3/README.md`, `v3/cascaded_agent.py`,
`v3/lk_agent_tool.py`, and `v3/benchmark_data_v2.json`.

---

## 1. What actually gets scored now

**Round 1 = 0.6 × normalized FDB-v3 benchmark + 0.2 × extension + 0.2 × docs.**
The organisers **re-run FDB-v3 themselves** on our submission via our one-command
reproduction script, on a single 48 GB NVIDIA GPU (CUDA 12/13) or our declared
hosted APIs. Only their re-run counts; ties break on strict pass-rate.

FDB-v3 in one paragraph: **100 human-recorded audio clips** (79 scenarios, 12
speakers, 48 kHz), each streamed through a **LiveKit voice agent**; the agent's
tool calls and spoken response are captured and scored by:

| Metric | What it measures |
|---|---|
| Tool-selection **F1** | recall (expected tools called) × precision (**no extra calls**) |
| **Argument accuracy** | semantic correctness of args, gpt-4o judge (`--use-llm`) |
| **Response accuracy** | does the spoken answer complete the task (gpt-4o judge) |
| **Strict pass rate** | ALL expected tools, correct args, **no missing, no extra** |
| **Latency** | user-speech-end → first word / first tool / task-complete |

Data mix (`statistics` in `benchmark_data_v2.json`): 4 domains × 25 each
(travel_identity, finance_billing, housing_location, ecommerce_support); easy
32 / medium 36 / hard 32 (1/2/3 chained calls, avg 1.54); disfluency features
FILLER 26, PAUSE 19, HESITATION 10, FALSE_START 11, **SELF_CORRECTION 21**;
**21 state-rollback scenarios**.

The 12 tools (from `cascaded_agent.py`, exact signatures):
`search_flights(destination,date)`, `book_flight(passenger_name)`,
`update_identity_doc(doc_type,doc_number)`, `get_card_benefits(card_type)`,
`get_exchange_rate(amount,from_currency,to_currency)`,
`modify_autopay(bill_type,source_account)`,
`search_apartments(city,bedrooms,max_price)`,
`calculate_commute(origin_address,destination_address,mode)`,
`update_search_filter(filter_name,value)`, `track_order(order_id)`,
`search_products(query,max_price?)`, `add_to_cart(product_id,quantity?)`.

---

## 2. Where PARLEY's design is exactly right — and where it's now wrong

**Wrong (do not submit as the scored agent):**
- `parley/` is pure Python with no LLM and does not run inside LiveKit. It cannot
  produce tool-selection F1 / argument accuracy / LLM-judged response quality on
  real human audio. The reconstructed `harness/` rubric is not the scorer.

**Exactly right (this is the whole reason we can win the hard cases):**
The guide says *"self-correction handling and multi-step tool chains are where
scores are lost. That is where you win."* Look at the canonical self-correction
scenario `travel_09` (medium, SELF_CORRECTION):

> "…look at flights to **Paris** — actually, no, scratch that. My meeting got
> moved to **Berlin**, so make that Berlin instead, on September 10th."
> **Expected: exactly one call** `search_flights(destination="Berlin",
> date="September 10")`.

The failure mode is calling `search_flights(Paris)` **and** `search_flights(Berlin)`
— an extra call that tanks precision and fails the strict pass. That is
**precisely PARLEY's thesis**: an interruption changes one slot; kill only the
stale reader; never double-fire. The reusable ideas:

| PARLEY module | Reused as, in the LiveKit agent |
|---|---|
| `parley/agent/endpointer.py` (learned turn-taking) | **Custom turn detector** — hold the turn through *"Paris — no, Berlin"* so the LLM only ever sees the corrected value and fires one call. This is the single highest-leverage reuse: premature endpointing on the abandoned value is the extra-call bug. |
| `kernel/ledger.py` idempotency, `dispatcher.py` cancel-safe dispatch | A **tool-call guard** wrapping the 12 tools: suppress a duplicate `(tool,args)`, and drop a call whose argument was superseded within the same turn. Directly lifts precision / strict-pass on the 21 rollback scenarios. |
| `kernel/policy.py` + `nlu.py` taxonomy | The design language for the README's architecture diagram and the "how we handle disfluency" story. |
| `multimodal/` (camera frame → device fault) | The **extension use case (20%)** — the guide literally names *"device troubleshooting with a camera frame."* Already built and tested. |
| the endpoint benchmark discipline (honest baselines) | The README's evaluation honesty; reuse the held-out / false-early framing. |

**Net:** the coordination brain transfers; the runtime shell changes from
our harness to LiveKit + an LLM.

---

## 3. The build (what a fresh session does next)

Ordered; each step is independently checkpointed.

1. **Custom LiveKit agent** — start from `v3/cascaded_agent.py` (Silero VAD +
   Whisper STT + gpt-4o + OpenAI TTS; it exposes the 12 tools and the eval
   pipeline expects its logging). Fork it to `agent_parley.py` and add two
   things the stock template lacks:
   - **Turn detection that survives self-correction.** The stock agent uses
     `AgentSession(min_endpointing_delay=0.5, max_endpointing_delay=5.0)` — a
     fixed silence timeout, the exact baseline our endpointer beat (72 % / 25 %
     false-early vs 89 % / 3 %). Wire our lexical+timing endpointer in as the
     turn detector so the agent does not commit while the user is still
     correcting themselves. LiveKit exposes `turn_detection` on `AgentSession`;
     confirm the hook against the installed `livekit-agents ~=1.3` API.
   - **A tool-call guard** around `registry.call(...)`: dedupe identical
     `(tool,args)` within a conversation and drop a superseded-argument call.
     Port the ledger/dispatcher logic; keep it pure so it needs no LLM.
2. **One-command reproduction script** — `reproduce.sh` (or `.py`) that installs
   deps, writes `.env.local` from documented env vars, starts the agent, runs
   `run_tool_benchmark_all_released.py --provider <ours>`, then the three
   `evaluate_*` scripts with `--use-llm`. Must run on a clean machine (the
   organisers test this; if it fails twice, the 60 % scores zero).
3. **Extension use case** — wrap `parley/multimodal` behind one more tool
   (e.g. `diagnose_device_frame(image)`), shown end-to-end in the video. Keep it
   honest: scored on working end-to-end, not ambition.
4. **Docs + slides + video** — README with one architecture diagram, the
   provider/keys declaration, our own best-run logs (scores, seeds, config); ≤8
   slides; 3–5 min video (a real benchmark interruption, then the extension).

**Do-not (disqualifiers, from the guide):** don't hardcode/fine-tune on
benchmark items (they're public), don't call our own servers at eval time, don't
cache across scenarios.

---

## 4. Human-only blockers (cannot be done from this session)

1. **LiveKit Cloud account** (free tier) → `LIVEKIT_URL/API_KEY/API_SECRET`.
2. **Benchmark data** — download `fdb_v3_data_released/` from the Google Drive
   link in `v3/README.md`, extract into `v3/`.
3. **API keys** — at minimum `OPENAI_API_KEY` (cascaded pipeline **and** the
   gpt-4o judge). A realtime provider key (Gemini/Grok/Ultravox) is optional.
4. **A CUDA GPU box** for a clean-machine reproduction test — the RTX 3050 (4 GB)
   is fine for the cascaded agent (Silero VAD + hosted OpenAI calls do the heavy
   lifting), but not for hosting a large open checkpoint locally.
5. **`ffmpeg`** and `nemo_toolkit[asr]` installed for the ASR step.

Until (1)–(3) exist, the agent can be *written* but not *run* against the
benchmark. Everything in §3 step 1's logic (endpointer, tool guard) can be
unit-tested offline against our own fixtures first.

---

## 5. What to keep from PARLEY as-is

- `parley/agent/endpointer.py`, `endpoint_corpus.py`, `scripts/train_endpointer.py`,
  `endpoint_model.json` — the turn detector, already trained and benchmarked
  (see `BUILD_LOG.md`), lift straight into step 1.
- `parley/multimodal/` — the extension.
- `parley/kernel/ledger.py`, `dispatcher.py` — port the dedup/cancel logic.
- The docs' honesty discipline (real numbers, baselines, negative results).

The reconstructed `harness/` and the 32 scenarios stay as an **internal**
regression net for the coordination logic (like FDB v1/v1.5 is "optional
practice"), not as anything submitted.
