# AI prompt log

Feeds section 4 ("prompts used") of the `LangAI3.0_AI_Disclosure.docx` form.
Entries before 29 Sep 2026 are recorded per feature in `../DISCLOSURE.md` (F1–F6).
Every Claude Code session appends one entry (see `../CLAUDE.md`). Keys are never
pasted here.

---

## 2026-09-29 — Aryan (lead) — Claude Code (cloud session)

**Prompts (verbatim, abridged):**
- "tell me wht i need ot do for the livkit thing step by step tell me and what do
  i need to send to my teamame he will bacially drop whatver you give to his claude"
- "any heavy work which would cost more creds tell me i will send that work to him"
- "go trhough all the repo all .md files to fullextent and check eveythign to
  understand whst already built so we dont wste time doing whts already done"
- "we need to follwo this [updated guide + Theme 5 guide] and keep the ai prompt
  log maitained whatevr her es mentioned"
- "i need you to do it you will do eveythign on yoru own"
- "could you still do most of the work like building and updating the rest of the
  thing ... my friedn will become controvutor"
- "i wont buy the openai api i cant go for any paid options so lets shift to free
  things ... or i have ollama in my pc"

**Output:**
- Planning: LiveKit account steps, work split, repo audit. Findings: no
  LiveKit code existed; the endpointer's strongest features were computed from
  PARLEY's own manifest; docs describe the superseded harness.
- `parley/fdb/turn.py` — endpointer exposed as a LiveKit turn detector, with
  manifest-derived features for the 12 FDB-v3 tools.
- `parley/fdb/guard.py` — tool guard (duplicate refusal, commit grace window).
- `fdb/agent_parley.py` — fork of FDB-v3 `cascaded_agent.py`; Groq free backend
  by default, OpenAI optional.
- `tests/test_fdb.py` (18 tests), `reproduce.sh`, `requirements-fdb.txt`,
  `CLAUDE.md`, this log, `.gitignore` additions.

**Human decisions:** no paid APIs (switched default backend OpenAI → Groq free
tier); a teammate commits and pushes because the Claude GitHub App is not installed.
**Not yet verified:** no end-to-end benchmark run (container had no LiveKit/Groq
network access and no keys).

## 2026-09-29 — Aryan (lead) — Claude Code (cloud session): first end-to-end run

**Prompts (verbatim):**
- "Read CLAUDE.md. Test fdb/agent_parley.py end to end on this machine: run
  SAMPLES=5 bash reproduce.sh, fix what breaks, and report the scores honestly.
  This machine has no GPU. The benchmark runner calls model.cuda(); work around
  that for local testing only, without changing what we submit."
- "you there are you doing this [first prompt repeated] tou seemed stagnant thats
  why just asking"
- "just checking up how much more time jsut asking"
- "Good diagnosis. Rather than only a local shim, make it robust in the submitted
  agent too: in fdb/agent_parley.py, raise the worker's load threshold (e.g.
  AgentServer(load_threshold=0.95) or the 1.3.12 equivalent; verify the parameter
  exists) so a busy evaluation machine never silently refuses a clip. Keep the
  stock agent untouched. Add one line to the report and the README noting that
  FDB-v3's latency analysis and LLM judge need an OpenAI key, so our free runs are
  exact-match only. Then finish the 5-clip run and report per-clip results."
- "update?" / "how much approx time remain"

**Output:**
- `fdb/agent_parley.py`: default Groq LLM `llama-3.3-70b-versatile` → `openai/gpt-oss-120b`
  (Groq returns model_not_found for the old id, so the agent never replied; chosen on
  availability, not on benchmark scores). `AgentServer(load_threshold=math.inf,
  load_fnc=job_load)`: the worker reports job-slot load instead of machine CPU.
- `reproduce.sh`: recorded LLM default updated. `README.md`: FDB-v3 section with the
  OpenAI-key note.
- First end-to-end run: `SAMPLES=5`, Groq backend, exact-match (no judge), run dir
  `runs/fdb/20260929-232747-parley` (local, gitignored). Turn-taking 5/5, tool
  selection 93.3%, argument accuracy 100%, strict pass 4/5 (80%). One failure:
  ecommerce_05, an unrequested extra `add_to_cart` after the correct
  `search_products`. Not tuned against (guide rule). Mean perceived latency 10.1 s
  (6.7–21.0 s), measured on a CPU-only sandbox.
- Local-only test workarounds, kept out of the repo: CPU no-op for `model.cuda()`,
  a localhost `livekit-server` (the sandbox cannot reach LiveKit Cloud media), and
  a proxy bypass for localhost.

**Human changes:** asked for the load fix in the submitted agent instead of a local
shim only. The suggested 0.95 was replaced: this box measured 0.952, and
livekit-server's own 0.7 `target_load` cut-off also has to be avoided.

## 2026-09-30 (overnight) — Aryan (lead) — Claude Code (cloud session): autonomous steps 1–9

**Prompt (verbatim, abridged only where marked):**
"Work autonomously until everything below is done. I'm asleep and pre-approve all steps listed
here. Save credits: work quietly, don't narrate, don't re-read big files you've already read,
don't check on long runs every minute (run them in the background and wait for them to finish).
Rules (from CLAUDE.md, never break): no paid services; never tune prompts or thresholds on
benchmark sentences; no caching across scenarios; never commit keys, data, `third_party/` or
local test workarounds. Name in the prompt log: "Aryan (lead)". After every step, verify: run
`python -m pytest` (the `test_asr` failures are pre-existing) and `bash -n reproduce.sh`, and
check the step actually did what it should. If something broke, fix it and verify again before
moving on. If a step can't be fixed after 2 honest attempts, write down why and continue with
the next step. Steps, in order: 1. Latency: break down where the ~10 s goes (heartbeat log). Try
`reasoning_effort="low"` on gpt-oss-120b and compare on the same 5 clips. Keep it only if tool
accuracy holds. 2. Extra actions: add one general instruction: only perform state-changing
actions the user explicitly asked for; searching isn't buying. Re-test on 5 clips. 3. Ablation:
the same clips with `PARLEY_TURN_DETECTOR=0 PARLEY_GUARD=0`, to show honestly whether our
additions help. 4. 10 different clips with the best config and per-clip results. Fix general
failure causes only. 5. One full 100-clip run (approved, once only). If Groq rate-limits you,
wait and resume. Don't restart from zero. Commit the run's reports and config with `git add -f
runs/fdb/<that run>` (the guide requires our logs). Leave out audio files. 6. README rewrite for
the new guide [...] 7. ≤8-slide deck (.pptx in `deck/`) [...] 8. docs/STATUS.md: a short morning
summary for me [...] 9. Competitive research (budget: about 20 web searches/fetches total) [...]
Commit and push after each step. Keep one draft PR against master updated, and append your
prompts to docs/AI_PROMPT_LOG.md. Don't merge, tag a release, rotate keys, or submit anything.
Those are mine."

**Output:** see `docs/STATUS.md` (summary), `docs/FDB_RESULTS.md` (every run), and
`docs/RESEARCH.md` Round 4. Code: `parley/fdb/local_tts.py` (Piper), `args.py` (spoken-ID
canonicaliser), `resolver.py` (stale-value check), fixes to `guard.py` and `turn.py`, and
metrics and transcript logging in `fdb/agent_parley.py`. Scripts: `reproduce.sh`
(sampling, resume, voice download, Python 3.10–3.11) and `scripts/fdb_summary.py`. Docs: new
`README.md` (old one moved to `docs/KERNEL.md`) and `deck/ThaparPatiala_TEAM_FDBv3_ppt.pptx`.

**Decisions taken without the human (flagged in STATUS.md for review):** replaced Groq's
Orpheus TTS with local Piper, because the free TTS quota is 100 requests/day. Step 5 was not
run: Groq's free tier caps every tool-calling model at 200K tokens/day, and 100 clips need
~400K. The step-9 A/B used gpt-oss-20b, because gpt-oss-120b's daily cap was spent.


## 2026-09-30 (morning) — Aryan (lead) — Claude Code (cloud session): follow-up

**Prompt (verbatim):** "finish of the remainign tasks"

**Output:** `PARLEY_LLM_BASE_URL` (+ `PARLEY_LLM_API_KEY`, `PARLEY_LLM`) in
`fdb/agent_parley.py` sends LLM calls to any OpenAI-compatible server (vLLM or Ollama on the
evaluation GPU), which is the unblocking path for a 100-clip run. It was smoke-tested on one
clip against Groq's OpenAI-compatible endpoint. `reproduce.sh`, README and STATUS updated.
**Still not done:** the 100-clip run, because the free-tier daily token cap is unchanged; and the
human-only checklist in `docs/STATUS.md`.
---

## 2026-09-30 — Devaansh — Claude Code (desktop app, Opus 5.5)

**Prompts (verbatim, abridged; keys redacted):**
- (pasted brief) "We're continuing PARLEY ... pivoted to Full-Duplex-Bench v3 ... Step 0: get
  FDB-v3's stock cascaded_agent.py running ... Step 1: fork it into agent_parley.py ... Step 2:
  write a one-command reproduce.sh ... Cost rule: develop and test against only 3–5 samples."
- "https://github.com/itsAryan-devop/parley"
- "can you pause for now"
- "start from the begining of the prompt and" + (pasted from the lead) "Stop building
  agent_parley.py, reproduce.sh, the tool guard or the turn detector. They're already done and
  merged into master ... Run a 5-clip test: SAMPLES=5 bash reproduce.sh ... fix setup errors
  only. Don't rebuild anything." + LiveKit keys [REDACTED] — "here is more work"
- "ffmpeg installed" / "here is the key [GROQ key REDACTED]" / "ffmpeg installed in ubuntu now"
- "in wsl it shows my server as zulip server how do i get out of it"
- "you will continue working after the setup right and how much time will it take"
- (pasted) "Task: build the extension use case (20% of the score): camera-based device
  troubleshooting as a live LiveKit voice agent. Do NOT touch fdb/agent_parley.py, parley/fdb/ or
  reproduce.sh ... One tool, diagnose_device_frame() ... The agent must ask, not guess ... Offline
  tests in tests/test_extension.py ... Test with 2–3 real phone photos ... Test it live with a
  webcam in the free LiveKit Agents Playground ... Add a short README section ... Append this
  session to docs/AI_PROMPT_LOG.md. Then commit, push to a new branch extension-camera, and open a
  pull request."
- "Use LLM model openai/gpt-oss-120b on Groq (same as the new default in fdb/agent_parley.py), not
  llama-3.3-70b-versatile, which Groq no longer serves. Pull master again first."

**Output:**
- Benchmark setup in WSL2 Ubuntu (`~/parley`, Python 3.10 via uv, `.venv-fdb` pre-installed).
  First 5-clip run: agent did not register within reproduce.sh's 60 s on a cold start (7 s warm).
  Second run stopped by us before inference (see "Findings"). No benchmark scores yet.
- `parley/extension/camera.py` — latest-frame holder, RGBA→PNG, `FrameDiagnoser` (reuses
  `parley.fdb.ToolGuard` for per-session frame dedup), `summarise` → diagnosed / ask / retake with
  manual steps from `harness/mockenv/world.py`.
- `fdb/agent_extension.py` — separate LiveKit agent (explicit name `parley-extension`, so it never
  joins benchmark rooms), Groq STT + `openai/gpt-oss-120b` + Orpheus TTS, video-track subscription,
  one raw-schema tool, `token` subcommand for the Agents Playground.
- `tests/test_extension.py` (11 tests, offline), README "Extension" section.
- Small refactor stashed/abandoned before the lead's instruction (endpointer manifest work);
  kept on a local stash and branch `backup/pre-merge-gitignore`, not pushed.

**Findings worth the team's attention:**
- A no-argument tool is rejected by Groq (HTTP 400, `required` without `properties`); fixed with
  an explicit raw schema.
- livekit-agents 1.3.12 cuts Groq Orpheus audio after the first sentence when synthesis is slower
  than real time ("Invalid WAV file: missing RIFF/WAVE"). The extension buffers each WAV whole;
  **`fdb/agent_parley.py` uses the same TTS and likely has the same truncation**, which would hurt
  FDB-v3 response accuracy.
- Groq's free Orpheus TTS quota is ~3,600 characters / 100 requests per day per account. That is
  far below what 100 benchmark clips need.

**Human decisions:** WSL instead of Windows; keys only in gitignored `.env.local` files and
`~/.parley_keys`; extension kept separate from the benchmark agent.
**Not yet verified:** real phone photos (none provided yet); live webcam test in the Agents
Playground (needs the human's webcam). Automated end-to-end with a fake camera track passed for
washer E4, router WAN amber, ambiguous router (asked) and unreadable washer (retake).

**Later in the same session (verbatim prompts):**
- "how do i run it" / "where should i go" / "where" (screenshots of the LiveKit Cloud dashboard
  and login page) / "it says registered worker, now what"
- "it was talking before but it seems to have stopped talking" (screenshot: Groq TTS 429, then
  `session closed ... tts_error recoverable=False`)
- "can you my own claude api key which i am using right now to chat with you" (answered: no
  access to it, the Claude API has no TTS, and it is a paid service)
- "try the offline piper voice as a fallback"
- "check github now there have been new pushs see them first"

**Changes from those:** run instructions moved from the Agents Playground (now requires the
project owner's LiveKit Cloud login) to LiveKit Meet's custom URL + token tab; TTS is now
`tts.FallbackAdapter([Groq Orpheus, Piper])` so a 429 changes the voice instead of ending the
session. After seeing PR #2, the branch was rebased onto `claude/dreamy-volta-fedkme` and reuses
its `parley.fdb.local_tts.PiperTTS` (piper-tts 1.2.0, checksummed ljspeech voice) instead of a
second Piper implementation; README Extension section replaces PR #2's placeholder. The 5-clip
benchmark run was left to PR #2's results (4/5 strict) rather than repeated.
