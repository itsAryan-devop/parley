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
