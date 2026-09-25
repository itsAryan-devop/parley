# Teammate brief — build the FDB-v3 LiveKit agent

> **For the teammate picking up the benchmark build (with or without Claude Code).**
> This is self-contained. Read it, then read `docs/PIVOT_FDBv3.md` (the full
> grounded plan) and `HANDOFF.md` §4 (what the voice path does and does not do).

## The situation in three sentences

Our project PARLEY was built against an old guide that assumed no public
evaluation kit. The **updated** guide (see `docs/PIVOT_FDBv3.md`) says scoring is
the real, public **Full-Duplex-Bench v3** run inside **LiveKit**, and 60% of
Round 1 is the organisers re-running it on our submission. So the pure-Python
engine in `parley/` is now our *internal* regression net, not the submission —
the submission is a **LiveKit voice agent** that scores well on FDB-v3.

## What is already done and reusable (do NOT rebuild these)

- **The endpointer** — `parley/agent/endpointer.py`, a trained turn-taking model
  (89% acc / 3% false-early vs a 25% silence-timeout baseline; see
  `docs/BUILD_LOG.md` "Session 4"). This is the highest-value reuse: as a LiveKit
  turn detector it stops the agent ending a turn mid-self-correction ("Paris —
  no, Berlin"), which is the extra-tool-call bug FDB-v3 punishes.
- **Dedup / cancellation logic** — `parley/kernel/ledger.py` (idempotency) and
  `parley/kernel/dispatcher.py` (cancel-safe). Port the *logic* (it's pure
  Python, no LLM) into a guard around the agent's tool calls.
- **Camera-frame perception** — `parley/multimodal/` is the 20% extension use
  case, already built.

## Your job, in order (each step is a checkpoint)

**Step 0 — get the stock benchmark running first (baseline).** Before touching
our code, prove the pipeline works end to end with FDB-v3's own cascaded agent.
This is the single most important de-risking step.

1. `git clone` our repo (you'll be added as a contributor).
2. Clone FDB-v3: `github.com/DanielLin94144/Full-Duplex-Bench`, use the `v3/`
   directory.
3. `conda create -n fdb python=3.10 && conda activate fdb` (it wants 3.10, not 3.13).
4. Install deps (from `v3/README.md`):
   `pip install "livekit-agents[openai]~=1.3" "livekit-plugins-silero" "livekit-plugins-openai" "livekit[crypto]~=1.0" nemo_toolkit[asr] pydub ffmpeg-python openai python-dotenv`
   plus `ffmpeg` on the system.
5. Create `v3/.env.local` with the LiveKit + OpenAI keys the team lead gives you
   (see "credentials" below). **Never commit this file.**
6. Download the benchmark data (Google Drive link in `v3/README.md`) into
   `v3/fdb_v3_data_released/`. **Never commit this data.**
7. Run the stock cascaded agent end to end on a few samples:
   - Terminal 1: `cd v3 && python cascaded_agent.py start`
   - Terminal 2: `cd v3 && python run_tool_benchmark_all_released.py --provider cascaded`
   - Then evaluate: `python evaluate_tool_calls.py --benchmark benchmark_data_v2.json --results-dir fdb_v3_data_released --provider cascaded --output cascaded_report.json --use-llm`
   - **Report the baseline scores back to the team.** This is what we must beat.

**Step 1 — build our custom agent.** Copy `v3/cascaded_agent.py` to
`agent_parley.py` and add two things the stock template lacks:
   - **A turn detector using our endpointer** so the agent does not commit while
     the user is still self-correcting. LiveKit's `AgentSession` takes a
     `turn_detection` argument and uses `min/max_endpointing_delay` today — wire
     our `EndpointModel` in there. Confirm the exact hook against the installed
     `livekit-agents ~=1.3` API (it changes between versions).
   - **A tool-call guard** wrapping `registry.call(...)`: refuse a duplicate
     `(tool, args)` in one conversation, and drop a call whose argument was
     superseded within the same turn. Port `parley/kernel/ledger.py` logic.
   Re-run the benchmark and compare against the Step-0 baseline. If we don't
   beat it, say so honestly (that discipline is in `docs/BUILD_LOG.md`).

**Step 2 — reproduction script.** `reproduce.sh` that does install → configure →
agent → inference → evaluate in one command. **Test it on a different machine.**
The guide: if it doesn't reproduce, that 60% scores zero.

## Credentials (the team lead provides; keep them OUT of git)

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — free LiveKit Cloud account
- `OPENAI_API_KEY` — costs money (agent + gpt-4o judge)

These go in `v3/.env.local` only. Add `.env.local` and `fdb_v3_data_released/`
to `.gitignore` before the first push.

## Hard rules from the guide (disqualifiers)

- Do NOT hardcode / fine-tune on benchmark items (they're public).
- Do NOT call your own servers at evaluation time.
- Do NOT cache anything across scenarios.
