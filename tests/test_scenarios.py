"""End-to-end: every public scenario must pass every check it declares.

This is the regression net for the whole system. A unit test can pass while the
agent still books twice, because the bug lives in how the pieces are wired
together on a timeline. These run the real loop against the real mock
environment and score the resulting trace.
"""

from __future__ import annotations

import pytest

from harness.runner import run_scenario
from harness.scenario import Scenario, load_all
from harness.scoring import score
from parley.agent.model import InterruptionModel

SCENARIOS = load_all()
MODEL = InterruptionModel.load_default()


def ids(scenarios):
    return [s.id for s in scenarios]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=ids(SCENARIOS))
def test_scenario_passes_every_declared_check(scenario: Scenario) -> None:
    result = run_scenario(scenario, model=MODEL)
    card = score(result)
    assert result.error is None, result.error
    assert not card.failures, "\n".join(card.failures)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=ids(SCENARIOS))
def test_scenario_is_deterministic(scenario: Scenario) -> None:
    """Same script in, same trace out.

    Without this an adversarial-timing regression is indistinguishable from a
    flaky test, and no amount of tuning would be trustworthy.
    """
    a = run_scenario(scenario, model=MODEL)
    b = run_scenario(scenario, model=MODEL)

    def fingerprint(result):
        return [
            (r.t, r.kind.value, r.name)
            for r in result.trace
            if r.kind.value in ("action", "event")
        ]

    assert fingerprint(a) == fingerprint(b)
    assert a.snapshot.model_dump() == b.snapshot.model_dump()


def test_the_suite_covers_the_published_modality_mix() -> None:
    """The guide's public set is 50% text / 30% audio / 20% visual, and half the
    hidden set is multimodal at 1.5x. A text-only suite would leave us blind to
    the highest-weighted scenarios."""
    mix: dict[str, int] = {}
    for s in SCENARIOS:
        mix[s.modality] = mix.get(s.modality, 0) + 1
    total = len(SCENARIOS)

    assert total >= 9, "the public suite has nine canonical scenarios"
    assert mix.get("audio", 0) / total >= 0.20
    assert mix.get("visual", 0) / total >= 0.18
    assert (mix.get("audio", 0) + mix.get("visual", 0)) / total >= 0.40


def test_every_scenario_declares_what_it_probes() -> None:
    for s in SCENARIOS:
        assert s.probes, f"{s.id} does not say what it is testing"
        assert s.description, f"{s.id} has no description"


def test_no_scenario_blows_the_wall_clock_cap() -> None:
    """120 s per scenario is the published limit; virtual time makes it cheap,
    but a livelock would still hang the grader."""
    for scenario in SCENARIOS:
        result = run_scenario(scenario, model=MODEL)
        last = max((r.t for r in result.trace), default=0.0)
        assert last < 120_000, f"{scenario.id} ran to {last:.0f} ms of virtual time"


def test_the_agent_works_without_the_learned_classifier() -> None:
    """Rules alone are a valid agent. If the weights file is ever missing or
    unreadable, the session must degrade rather than fail."""
    for scenario in SCENARIOS:
        result = run_scenario(scenario, model=None)
        assert result.error is None, f"{scenario.id}: {result.error}"
        assert result.final is not None or score(result).components


# ------------------------------------------------- encoding invariance (metamorphic)

MEDIA_SCENARIOS = [
    s for s in SCENARIOS
    if any(e.get("type") in ("video_frame", "audio_clip") and e.get("path") for e in s.events)
]


@pytest.mark.parametrize("scenario", MEDIA_SCENARIOS, ids=ids(MEDIA_SCENARIOS))
def test_media_scenarios_are_encoding_invariant(scenario: Scenario) -> None:
    """The same bytes, delivered two ways, must produce the same perceptions.

    This is a *metamorphic* property rather than an invariant: it compares two
    runs, so it cannot live in `harness/fuzz.py`, which checks one run at a time.

    It exists because the alternative did not work. `ground_frame` and
    `ground_audio` once collapsed `path` and `data_b64` with `or`, handing a
    base64 string to the image decoder as a filename, and every inline frame
    reported itself as corrupt. Nothing caught it -- and crucially, the obvious
    fix of teaching the fuzzer to re-encode media *still* does not catch it:
    measured with the old behaviour restored, 0 of 17 inlined seeds on S10 and
    0 of 14 on S13 broke a single invariant.

    The reason is that a failed decode degrades gracefully. The agent says it
    could not make the frame out and asks a question, which violates nothing:
    no duplicate effect, no pending call, nothing claimed without a warrant.
    Safety invariants are the wrong instrument for a capability failure. Only
    comparing the two encodings detects it, so that is what this does.
    """
    import base64
    from pathlib import Path

    body = scenario.model_dump()
    inlined = 0
    for event in body["events"]:
        source = event.get("path")
        if event.get("type") not in ("video_frame", "audio_clip") or not source:
            continue
        raw = Path(str(source)).read_bytes()
        event.pop("path", None)
        event["data_b64"] = base64.b64encode(raw).decode("ascii")
        inlined += 1

    assert inlined, "this scenario was selected for having path-delivered media"
    body["id"] = f"{scenario.id}#inline"

    by_path = run_scenario(scenario, model=MODEL)
    by_bytes = run_scenario(Scenario.model_validate(body), model=MODEL)

    def perceptions(result):
        return [
            (r.payload.get("source_id"), r.payload.get("label"),
             r.payload.get("ambiguous"), r.payload.get("error"))
            for r in result.trace.named("perception")
        ]

    assert perceptions(by_bytes) == perceptions(by_path), (
        "delivering the same media inline changed what the agent perceived"
    )

    # And the consequences, not just the perception: a decode that silently
    # failed would also skip the tool call the perception was supposed to drive.
    def tools(result):
        return sorted(
            r.payload.get("tool") for r in result.trace.named("tool_call")
        )

    assert tools(by_bytes) == tools(by_path), (
        "delivering the same media inline changed which tools were called"
    )
