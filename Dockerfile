# PARLEY — Samsung PRISM Y2026 GenAI Hackathon, Theme 05
#
# Two properties this image must have, both driven by the rules rather than by
# taste:
#
#   1. NO RUNTIME DOWNLOADS. The guide allows a 300 s setup hook and caps each
#      scenario at 120 s. A model or dataset fetched at scenario time would eat
#      both, and the latency block with them. Every weight this agent uses is a
#      few kilobytes of JSON committed to the repo and copied in at build time.
#
#   2. PINNED PYTHON. The guide specifies 3.10-3.12. 3.13 is outside that band,
#      so the version is pinned here rather than inherited from whatever the
#      host happens to have.
#
# Build and run:
#   docker build -t parley .
#   docker run --rm parley                      # public suite + scorecard
#   docker run --rm parley pytest               # full test suite
#   docker run --rm parley python scripts/fuzz.py --trials 60

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so edits to source do not invalidate the wheel layer.
COPY pyproject.toml ./
RUN python -m pip install --upgrade pip \
 && python -m pip install "pydantic>=2.6" "numpy>=1.24" "pillow>=10.0" \
                          "pytest>=8" "pytest-asyncio>=0.23" "scikit-learn>=1.3"

COPY parley/ ./parley/
COPY harness/ ./harness/
COPY scenarios/ ./scenarios/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY viz/ ./viz/
COPY media/scenarios/ ./media/scenarios/
COPY media/undecidable/ ./media/undecidable/
COPY README.md DISCLOSURE.md ./
COPY docs/ ./docs/

RUN python -m pip install --no-deps -e .

# Fail the build rather than the evaluation if the committed model weights are
# missing: without them the agent silently degrades to rules-only, which is a
# valid agent but not the one we measured.
RUN python -c "\
from pathlib import Path; import sys;\
missing=[p for p in ['parley/agent/interruption_model.json',\
 'parley/multimodal/vision_model.json','parley/multimodal/audio_model.json']\
 if not Path(p).exists()];\
sys.exit('missing committed weights: '+', '.join(missing)) if missing else print('weights present')"

# Warm every import and every model on the 300 s setup hook rather than inside a
# scenario's 120 s budget. numpy, pydantic and Pillow all have non-trivial
# import cost, and the first classification touches lazily-loaded code paths.
RUN python -c "\
from harness.runner import run_scenario;\
from harness.scenario import load_all;\
from parley.agent.model import InterruptionModel;\
from parley.multimodal import vision, audio;\
assert InterruptionModel.load_default() is not None;\
assert vision._get_model() is not None and audio._get_model() is not None;\
r=run_scenario(load_all()[0]);\
assert r.error is None, r.error;\
print('warm-up OK')"

CMD ["python", "scripts/run_scenarios.py"]
