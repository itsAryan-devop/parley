# PARLEY

**A LiveKit voice agent that listens through self-corrections before it acts.**

Samsung PRISM Y2026 GenAI Hackathon (3rd Edition) · **Theme 05 — Interruptible Real-Time Agents**
Team `ThaparPatiala_<TEAM>` · Thapar Institute of Engineering & Technology, Patiala

> *Parley (n.): a conversation between opposing parties, conducted under a truce, in which either
> side may speak at any moment.*

---

## The problem

People do not speak in finished commands. They say *"move my mortgage autopay to checking — wait,
no, savings"*, pause mid-sentence to find an order number, and spell IDs one character at a time.
A voice agent that calls a tool the moment it hears a plausible value acts on the abandoned one.
It then fires a second call for the correction, and for the benchmark and for the user that
extra call *is* the failure.

The scored benchmark, **Full-Duplex-Bench v3** (FDB-v3, [arXiv 2604.04847](https://arxiv.org/abs/2604.04847)),
measures exactly this on 100 real human recordings. Its authors find that self-correction is the
hardest category for every system they test, with even GPT-Realtime failing over 40% of them,
because agents *"commit intermediate parameters before the correction arrives"*.

PARLEY's answer is **cancel before effect**: hold a tool call until the user has actually
finished, and drop it if they were still talking.

## Architecture

```mermaid
flowchart LR
    U([User audio<br/>via LiveKit room]) --> VAD[Silero VAD]
    VAD --> STT[Whisper large-v3-turbo<br/>Groq]
    STT --> TD{{PARLEY turn detector<br/>learned endpointer}}
    TD -- "turn done" --> LLM[gpt-oss-120b<br/>Groq]
    TD -. "still mid-thought:<br/>keep listening" .-> VAD
    LLM -- tool call --> ARGS[Spoken-ID<br/>canonicaliser]
    ARGS --> G{{PARLEY ToolGuard<br/>commit window · dedupe}}
    G -- "user silent" --> API[(FDB-v3 mock APIs)]
    G -. "user still talking:<br/>not executed, wait" .-> LLM
    API --> LLM
    LLM --> TTS[Piper TTS<br/>local, CPU]
    TTS --> O([Agent audio])
    VAD -- "speaking / silent" --> G
```

The agent (`fdb/agent_parley.py`) is a fork of FDB-v3's stock `cascaded_agent.py`: the same 12
tools, tool schemas and logging, so the benchmark's runner and evaluators treat it exactly like
the stock agent. What PARLEY adds, all in `parley/fdb/`:

| Piece | What it does | Why |
|---|---|---|
| **Turn detector** (`turn.py`) | Our trained endpointer (`parley/agent/endpointer.py`) behind LiveKit's turn-detector interface. It keeps the turn open when the words so far end on a dangling word, a repair marker ("no", "I mean") or a trailing-off "…" | Ending the turn inside *"to checking — wait, no …"* hands the LLM the abandoned value |
| **ToolGuard** (`guard.py`) | Every call waits a 0.3 s commit window and runs only if the user is silent. Otherwise the LLM gets *"not executed, the user is still speaking"*. An identical `(tool, args)` runs at most once per conversation | Cancel before effect. It is the only defence once the LLM has already fired |
| **Spoken-ID canonicaliser** (`args.py`) | `P-5-2` → `P52`, but only in ID arguments whose pieces are all ≤ 3 characters | Whisper inserts separators nobody said, and the LLM copies them |
| **Local TTS** (`local_tts.py`) | Piper speaks in-process on CPU | Groq's free TTS allows 100 requests a day, far short of 100 clips |
| **Instructions** | Act only on the final corrected value. Only perform state-changing actions the user asked for. Never claim an action whose tool did not succeed | Each rule addresses a failure seen in our transcripts |

Each conversation gets a fresh `ToolGuard`; nothing is cached across scenarios.

## Run it

**Requirements:** Linux or macOS, Python **3.10 or 3.11**, `ffmpeg`, `git`, `curl`, internet.
A GPU is used by FDB-v3's own ASR scorer if present. The agent itself needs none.

**Keys** — put them in the environment. `reproduce.sh` writes them to FDB-v3's
`v3/.env.local`, which is gitignored:

| Variable | Needed for | Where to get it |
|---|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | the LiveKit room the benchmark streams into | a free LiveKit Cloud project → Settings → Keys |
| `GROQ_API_KEY` | STT and LLM | console.groq.com → API Keys (free tier; see the quota warning below) |
| `OPENAI_API_KEY` *(optional)* | FDB-v3's gpt-4o judge and latency analyser | platform.openai.com. **Without it, scoring is exact-match only and there is no latency report** |

**One command:**

```bash
bash reproduce.sh                      # all 100 recordings
SAMPLES=5 bash reproduce.sh            # the first 5 (development)
SAMPLES=10 SAMPLE_OFFSET=5 SAMPLE_STEP=10 bash reproduce.sh   # 10 spread over all 4 domains
RESUME=1 RUN_DIR=runs/fdb/<run> bash reproduce.sh              # continue a run, keep finished clips
```

It creates `.venv-fdb`, installs the pinned `requirements-fdb.txt`, and clones FDB-v3 at a
pinned commit into `third_party/`. It then downloads the benchmark data and the checksummed Piper
voice, starts the agent, streams every clip through LiveKit, and runs FDB-v3's evaluators.
Everything lands in `runs/fdb/<timestamp>-parley/`: the three reports, `config.txt` (versions,
commits, settings), the agent's tool-call log, the guard's suppressions, a per-room transcript
and LiveKit per-stage metrics. `python scripts/fdb_summary.py <run dir> <results dir>` prints the
per-clip table.

Settings (all optional): `PARLEY_TURN_DETECTOR=0` / `PARLEY_GUARD=0` (ablation),
`PARLEY_LLM` (Groq model id), `PARLEY_REASONING` (gpt-oss effort, default `low`),
`PARLEY_TTS=groq` (Orpheus instead of Piper), `PARLEY_BACKEND=openai` (the stock agent's exact
OpenAI models, for a like-for-like baseline).

> **Quota warning.** Groq's free tier caps each tool-calling model at **200,000 tokens per day**.
> A clip costs about 4,000, so **a full 100-clip run needs about two days of free quota**. See
> [`docs/STATUS.md`](docs/STATUS.md) for the options.

## Models and services

| Role | Model / software | Licence | Where it runs |
|---|---|---|---|
| Speech-to-text | Whisper large-v3-turbo (OpenAI) | MIT | Groq API |
| LLM, tool calling | gpt-oss-120b (OpenAI), `reasoning_effort=low` | Apache-2.0 | Groq API |
| Text-to-speech | Piper 1.2.0 + `en_US-ljspeech-medium` voice (LJ Speech dataset) | MIT / public domain | local CPU |
| Voice activity | Silero VAD via `livekit-plugins-silero` 1.3.12 | MIT | local CPU |
| Turn detection | PARLEY endpointer (logistic regression on our own synthetic corpus) | ours | local CPU |
| Agent framework | LiveKit Agents 1.3.12 | Apache-2.0 | local |
| Benchmark | FDB-v3 @ `3e799c45`, its NeMo parakeet-tdt-0.6b-v2 ASR scorer and optional gpt-4o judge | per FDB-v3 | local / OpenAI |

No calls to servers of our own at evaluation time.

## Results

Every run, with what changed and why, is in [`docs/FDB_RESULTS.md`](docs/FDB_RESULTS.md).
Read the caveats first:

- **Exact-match scoring only.** We have no OpenAI key, so FDB-v3's gpt-4o judge did not run.
  Argument and response accuracy are therefore stricter than in the paper, and response quality
  is unscored.
- **Measured in a cloud sandbox with no GPU and no route to LiveKit Cloud media.** We ran a local
  `livekit-server`, and FDB-v3's ASR scorer ran on CPU through local-only shims that are not in
  this repo. Latency is from that machine.
- **Small samples.** The same clip has flipped between pass and fail on replays. With 5–10
  clips, one clip is noise.
- **No 100-clip run yet.** The free tier's daily token cap stopped us (above).

| Clips | Configuration | Strict pass | Tool selection | Mean latency |
|---|---|---|---|---|
| first 5 (ecommerce) | submitted agent, Orpheus TTS | 4/5 | 93.3% | 10.1 s |
| first 5 | + local Piper TTS | 4/5 | 93.3% | **5.7 s** |
| first 5 | **ablation**: turn detector and guard off | 5/5 | 100% | 5.0 s |
| 10 spread, all domains | before the step-4 fixes (mixed run) | 3/10 | 91.9% | 6.1 s |
| 10 spread | + guard/turn/retry fixes | **4/10** | 81.1% | 6.8 s |

**What the ablation says, honestly:** on the 5 easy clips our additions show no benefit. The
one-clip difference is replay noise, and the guard's commit window costs latency. Those clips
contain no self-corrections. On the spread set, the self-correction clip is where the guard
visibly works: *"switch my mortgage autopay … from the checking — wait, no … make it savings"*
went from two calls (checking, then savings) to one call on savings. For reference, the paper's
stock cascaded agent scores 0.45 Pass@1 on all 100 clips *with* the gpt-4o judge, a different
and more lenient measure.

## Extension (20% use case) — camera frame

A teammate is building the extension, **device troubleshooting from a camera frame**, on branch
[`extension-camera`](../../tree/extension-camera). It builds on the perception
code already in [`parley/multimodal/`](parley/multimodal), which reads an appliance's panel or
LED pattern from a photo and abstains when the frame is undecidable. It is kept separate from the
benchmark agent, and the FDB-v3 scores above do not include it.

## Repository map

| Path | What |
|---|---|
| `fdb/agent_parley.py` | the scored LiveKit agent |
| `parley/fdb/` | turn detector, ToolGuard, ID canonicaliser, Piper TTS |
| `reproduce.sh`, `requirements-fdb.txt` | one-command, pinned reproduction |
| `scripts/fdb_summary.py` | per-clip table and latency breakdown for a run |
| `docs/FDB_RESULTS.md` | every benchmark run and what changed |
| `docs/KERNEL.md` | the original PARLEY kernel and the harness it was built on (now our internal regression net) |
| `docs/RESEARCH.md`, `docs/PRIOR_ART.md` | prior art and what we took from it, with credits |
| `DISCLOSURE.md`, `docs/AI_PROMPT_LOG.md` | AI usage disclosure and prompts |

`python -m pytest` runs the whole suite. The `tests/test_asr.py` failures without the optional
`voice` extra (Vosk) are expected.
