# FDB-v3 run logs

Every `reproduce.sh` run from 29–30 Sep 2026, committed for the guide's "results + run logs"
deliverable and so a teammate can pick up where we left off. What changed between runs, and
what each result means, is in [`docs/FDB_RESULTS.md`](../../docs/FDB_RESULTS.md). Read its
caveats first.

**None of these is a 100-clip run.** All are exact-match only (no gpt-4o judge). They were run
in a cloud sandbox with no GPU, on a local `livekit-server` with FDB-v3's ASR on CPU, through
local-only shims that are not in the repo. Audio outputs are not committed. Each folder has
`config.txt` (versions, commits, settings), FDB-v3's reports (`evaluation_report.json`,
`pass_rate_report.json`, `eval_*.log`), `inference.log`, `agent.log`, and, from the second
night on, the agent's tool calls, guard suppressions, per-room transcript and LiveKit metrics.
`python scripts/fdb_summary.py runs/fdb/<run> third_party/Full-Duplex-Bench/v3/<results dir>`
prints a per-clip table when the results folder is present.

| Run | Clips | What it was | Strict pass |
|---|---|---|---|
| `20260929-222834` | 5 | first attempt: stopped at `model.cuda()` (no GPU) | — |
| `20260929-223408` | 5 | LiveKit Cloud media unreachable from the sandbox; all clips silent | — |
| `20260929-225908` | 5 | local LiveKit; worker refused clips on CPU load (4/5 silent) | 0/5 |
| `20260929-230531` | 5 | stopped by hand to add the load fix | — |
| `20260929-231017` | 5 | lost to a container restart | — |
| `20260929-231553` | 5 | `load_threshold=inf` only; server's own 0.7 cut-off still refused 4 clips | 1/5 |
| `20260929-232747` | 5 | job-slot load reporting; first complete run (Orpheus TTS) | 4/5 |
| `20260929-235723` | 5 | step 1: local Piper TTS + metrics | 4/5 |
| `20260930-000431` | 5 | step 2: explicit-request rule | 4/5 |
| `20260930-001207` | 5 | step 2, stronger variant (reverted) | 4/5 |
| `20260930-001831` | 5 | step 3 ablation: turn detector + guard off | 5/5 |
| `20260930-002544` | 10 spread | step 4, **mixed config** (`turn.py` edited mid-run) | 3/10 |
| `20260930-003856` | 10 spread | step 4 fixes (without ID canonicaliser) | 4/10 |
| `20260930-005125` | 10 spread | + ID canonicaliser; **void from clip 5** (Groq daily token cap) | 3/4 valid |
| `20260930-010534` | 10 spread | step 9 A/B baseline, gpt-oss-20b | 6/10 |
| `20260930-011713` | 10 spread | + stale-value resolver (dropped) | 3/10 |
| `20260930-012934` | 10 spread | + guard waits for untranscribed speech (kept) | 5/10 |

"10 spread" = `SAMPLES=10 SAMPLE_OFFSET=5 SAMPLE_STEP=10`.
