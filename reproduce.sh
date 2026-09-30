#!/usr/bin/env bash
# One-command FDB-v3 reproduction for PARLEY: install -> configure -> agent ->
# inference -> evaluate. Run from the repo root:
#
#   bash reproduce.sh                 # all 100 recordings
#   SAMPLES=5 bash reproduce.sh       # first 5 only (development, cheap)
#   SAMPLES=10 SAMPLE_OFFSET=5 SAMPLE_STEP=10 bash reproduce.sh   # 10 clips spread over all domains
#   RESUME=1 RUN_DIR=runs/fdb/<run> bash reproduce.sh   # continue a run, keep finished clips
#   PROVIDER_LABEL=cascaded AGENT=stock bash reproduce.sh   # FDB-v3's stock agent (baseline)
#
# Keys are read from the environment (or an existing v3/.env.local), never from git:
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   (free LiveKit Cloud project)
#   GROQ_API_KEY                                       (default backend, free tier)
#   OPENAI_API_KEY   optional: PARLEY_BACKEND=openai, the stock agent, and the
#                    benchmark's gpt-4o judge (--use-llm). Without it, evaluation
#                    runs FDB-v3's exact-match mode and response accuracy is skipped.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
FDB_DIR="${FDB_DIR:-$ROOT/third_party/Full-Duplex-Bench}"
FDB_COMMIT="3e799c45a045256f47d5f1c9cda90157e2d2ec9e"
DATA_ID="1SO_4MTazWQ_jvCx0dtmpQ-t40bdd07yz"   # Google Drive id from FDB-v3's v3/README.md
AGENT="${AGENT:-parley}"                       # parley | stock
LABEL="${PROVIDER_LABEL:-parley}"
SAMPLES="${SAMPLES:-all}"
SAMPLE_OFFSET="${SAMPLE_OFFSET:-0}"            # skip this many clips (sorted by folder name)
SAMPLE_STEP="${SAMPLE_STEP:-1}"                # then take every Nth clip
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ljspeech/medium"
PIPER_SHA="6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22"
PIPER_JSON_SHA="141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860"
PY="${PYTHON:-python3}"
RUN_DIR="${RUN_DIR:-$ROOT/runs/fdb/$(date +%Y%m%d-%H%M%S)-$LABEL}"
case "$RUN_DIR" in /*) ;; *) RUN_DIR="$ROOT/$RUN_DIR" ;; esac

say() { printf '\n==> %s\n' "$*"; }

say "1/6 Python environment"
# 3.10-3.11: piper-phonemize (local TTS) ships no wheel for 3.12; FDB-v3 itself asks for 3.10.
"$PY" -c 'import sys; assert (3,10) <= sys.version_info[:2] <= (3,11), "need Python 3.10 or 3.11"'
command -v ffmpeg >/dev/null || { echo "ffmpeg not found: apt install ffmpeg / brew install ffmpeg"; exit 1; }
[ -d "$ROOT/.venv-fdb" ] || "$PY" -m venv "$ROOT/.venv-fdb"
# shellcheck disable=SC1091
source "$ROOT/.venv-fdb/bin/activate"
pip install -q --upgrade pip
pip install -q -r "$ROOT/requirements-fdb.txt"
pip install -q -e "$ROOT"
mkdir -p "$ROOT/models/piper"
for f in en_US-ljspeech-medium.onnx en_US-ljspeech-medium.onnx.json; do
  [ -f "$ROOT/models/piper/$f" ] || curl -sSfL -o "$ROOT/models/piper/$f" "$PIPER_BASE/$f"
done
echo "$PIPER_SHA  $ROOT/models/piper/en_US-ljspeech-medium.onnx
$PIPER_JSON_SHA  $ROOT/models/piper/en_US-ljspeech-medium.onnx.json" | sha256sum -c --quiet

say "2/6 FDB-v3 at pinned commit ${FDB_COMMIT:0:8}"
if [ ! -d "$FDB_DIR/.git" ]; then
  git clone -q https://github.com/DanielLin94144/Full-Duplex-Bench "$FDB_DIR"
fi
git -C "$FDB_DIR" fetch -q --depth 1 origin "$FDB_COMMIT" 2>/dev/null || true
git -C "$FDB_DIR" checkout -q "$FDB_COMMIT"
V3="$FDB_DIR/v3"

say "3/6 Benchmark data"
if [ ! -d "$V3/fdb_v3_data_released" ]; then
  gdown -q "$DATA_ID" -O "$V3/fdb_v3_data.archive"
  (cd "$V3" && python -c "
import tarfile, zipfile
f = 'fdb_v3_data.archive'
(zipfile.ZipFile(f).extractall('.') if zipfile.is_zipfile(f) else tarfile.open(f).extractall('.'))
" && rm fdb_v3_data.archive)
fi
[ -d "$V3/fdb_v3_data_released" ] || { echo "data folder missing after download"; exit 1; }

say "4/6 Keys -> v3/.env.local (local only, gitignored)"
if [ -n "${LIVEKIT_URL:-}" ]; then
  : > "$V3/.env.local"
  for k in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET GROQ_API_KEY OPENAI_API_KEY; do
    [ -n "${!k:-}" ] && echo "$k=${!k}" >> "$V3/.env.local"
  done
fi
[ -f "$V3/.env.local" ] || { echo "set LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET and GROQ_API_KEY"; exit 1; }
set -a; source "$V3/.env.local"; set +a

ROOT_DIR="fdb_v3_data_released"
if [ "$SAMPLES" != "all" ]; then
  ROOT_DIR="fdb_v3_subset_${SAMPLES}_${SAMPLE_OFFSET}_${SAMPLE_STEP}"
  [ "${RESUME:-0}" = 1 ] || rm -rf "$V3/$ROOT_DIR"
  mkdir -p "$V3/$ROOT_DIR"
  for d in $(ls -d "$V3"/fdb_v3_data_released/*/ | sort | tail -n +"$((SAMPLE_OFFSET + 1))" \
             | awk -v s="$SAMPLE_STEP" '(NR - 1) % s == 0' | head -n "$SAMPLES"); do
    [ -d "$V3/$ROOT_DIR/$(basename "$d")" ] && continue
    cp -r "$d" "$V3/$ROOT_DIR/"
  done
fi

say "5/6 Agent ($AGENT) + inference over $ROOT_DIR"
mkdir -p "$RUN_DIR"
LOGS="/tmp/agent_tool_calls.log /tmp/agent_heartbeat.log /tmp/parley_guard.log /tmp/parley_metrics.log"
if [ "${RESUME:-0}" = 1 ]; then FORCE=""; else FORCE="--force"; rm -f $LOGS; fi
cd "$V3"
if [ "$AGENT" = "stock" ]; then
  python cascaded_agent.py start >> "$RUN_DIR/agent.log" 2>&1 &
else
  PYTHONPATH="$ROOT:$V3" python "$ROOT/fdb/agent_parley.py" start >> "$RUN_DIR/agent.log" 2>&1 &
fi
AGENT_PID=$!
trap 'kill $AGENT_PID 2>/dev/null || true' EXIT
for _ in $(seq 60); do grep -qi "registered worker" "$RUN_DIR/agent.log" && break; sleep 1; done
grep -qi "registered worker" "$RUN_DIR/agent.log" || { echo "agent did not register; see $RUN_DIR/agent.log"; exit 1; }
python run_tool_benchmark_all_released.py --provider "$LABEL" --root_dir "$ROOT_DIR" $FORCE \
  2>&1 | tee -a "$RUN_DIR/inference.log"
kill $AGENT_PID 2>/dev/null || true

say "6/6 Evaluation"
JUDGE=""; [ -n "${OPENAI_API_KEY:-}" ] && JUDGE="--use-llm"
python evaluate_tool_calls.py --benchmark benchmark_data_v2.json --results-dir "$ROOT_DIR" \
  --provider "$LABEL" --output "$RUN_DIR/evaluation_report.json" $JUDGE 2>&1 | tee "$RUN_DIR/eval_tools.log"
python evaluate_pass_rate.py --benchmark benchmark_data_v2.json --results-dir "$ROOT_DIR" \
  --provider "$LABEL" --output "$RUN_DIR/pass_rate_report.json" $JUDGE 2>&1 | tee "$RUN_DIR/eval_pass.log"
python analyze_tool_latency.py --results-dir "$ROOT_DIR" --provider "$LABEL" \
  --output "$RUN_DIR/latency_report.json" 2>&1 | tee "$RUN_DIR/eval_latency.log" || true

cp $LOGS "$RUN_DIR/" 2>/dev/null || true
{
  echo "date: $(date -u +%FT%TZ)"
  echo "agent: $AGENT   label: $LABEL   samples: $SAMPLES  offset: $SAMPLE_OFFSET  step: $SAMPLE_STEP  resume: ${RESUME:-0}"
  echo "backend: ${PARLEY_BACKEND:-groq}   llm: ${PARLEY_LLM:-openai/gpt-oss-120b}   reasoning: ${PARLEY_REASONING:-low}   tts: ${PARLEY_TTS:-local}"
  echo "grace: ${PARLEY_COMMIT_GRACE:-0.3}   max_delay: ${PARLEY_MAX_DELAY:-1.5}"
  echo "turn_detector: ${PARLEY_TURN_DETECTOR:-1}   guard: ${PARLEY_GUARD:-1}"
  echo "judge: ${JUDGE:-exact-match (no OPENAI_API_KEY)}"
  echo "fdb_commit: $FDB_COMMIT   parley_commit: $(git -C "$ROOT" rev-parse HEAD)"
  pip freeze | grep -iE "^livekit|^openai=="
} > "$RUN_DIR/config.txt"
say "Done. Reports, logs and config in $RUN_DIR"
