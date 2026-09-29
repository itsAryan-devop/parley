# Rules for every Claude Code session in this repo

Source of truth: `Theme05_Participant_Guide_UPDATED_FBD` — Full-Duplex-Bench v3
run inside LiveKit. The older `Theme_5_Guide.pdf` (virtual-clock harness,
40/35/15/10 rubric) is superseded; `harness/` is only an internal regression net.

Start with `docs/PIVOT_FDBv3.md`, then `fdb/agent_parley.py` and `parley/fdb/`.

## Every session, no exceptions

1. Before ending, append an entry to `docs/AI_PROMPT_LOG.md`: date, teammate
   name, tool + model, the human's prompts **verbatim**, files created/changed,
   and what the human changed or rejected. Never paste API keys into it.
2. Never commit `.env*`, API keys, `third_party/` or `fdb_v3_data_released/`.
3. No paid services. Default backend is Groq's free tier (`GROQ_API_KEY`).
   Develop with `SAMPLES=5 bash reproduce.sh`; run all 100 only when the team
   lead approves (free-tier rate limits are per day).

## Guide rules (breaking one = disqualification)

- Never hardcode, memorize, or fine-tune on FDB-v3 test items. Do not tune
  thresholds or prompts against benchmark transcripts; use our own phrasings.
- No calls to our own servers at evaluation time.
- No caching across scenarios; each conversation starts fresh (one `ToolGuard`
  per LiveKit session).
- Pin seeds and versions (`requirements-fdb.txt`, FDB commit in `reproduce.sh`);
  cite every model/API in the README.
- Document which API keys are needed and where they go; never include them.

## Deliverables (updated guide section 4)

README (one architecture diagram, setup/run steps, extension clearly marked) ·
`reproduce.sh` + provider declaration · results + run logs (scores, seeds,
config — `runs/fdb/<run>/`) · 3–5 min video · ≤8 slides.

## Checks before pushing

`python -m pytest` (the `tests/test_asr.py` failures without the `voice` extra
are pre-existing) · `bash -n reproduce.sh`.
