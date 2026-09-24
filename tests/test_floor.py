"""Floor management and the provable-speech gate.

The gate is the defence for the quality multiplier (0.80-1.20 on the whole
score) and for objective 1's ban on false completion claims. These tests mostly
assert what the agent *cannot* say.
"""

from __future__ import annotations

import pytest

from harness.clock import Clock, run_virtual
from harness.trace import Trace
from parley.agent.floor import (
    SPEAK_THRESHOLD,
    FloorManager,
    Utterance,
    describe_tool,
    phrase_slot,
)
from parley.kernel.calls import CallOutcome, CallRecord, CallRegistry
from parley.kernel.policy import InterruptionKind, policy_for
from parley.protocol import SessionState, SlotSource
from parley.protocol.actions import Claim, ClaimKind, SpeechKind


def build() -> tuple[FloorManager, SessionState, CallRegistry, Trace]:
    clock, trace = Clock(), Trace(session_id="t")
    state, registry = SessionState(session_id="t"), CallRegistry()
    fm = FloorManager(clock=clock, trace=trace, state=state, registry=registry)
    return fm, state, registry, trace


def add_call(registry: CallRegistry, **kw) -> CallRecord:
    defaults = dict(
        call_id=registry.next_call_id(), tool="search_flights", args={}, read_slots=set(),
        state_revision=0, mutating=False, dispatched_at=0.0,
    )
    return registry.add(CallRecord(**(defaults | kw)))


# =============================================================== the gate

def test_a_completion_cannot_be_claimed_while_the_call_is_in_flight() -> None:
    """The single most costly lie: "Booked!" before the tool returned.

    Penalised in floor management AND again in the quality multiplier.
    """

    async def main():
        fm, state, registry, trace = build()
        rec = add_call(registry, tool="book_flight", mutating=True)

        spoken = fm.final(
            "Booked.",
            claims=[Claim(kind=ClaimKind.COMPLETED, subject=rec.call_id, warrant="wishful thinking")],
        )
        blocked = trace.named("final_claims_dropped")
        return spoken.claims, blocked

    claims, blocked = run_virtual(main())
    assert claims == [], "an in-flight call must not warrant a completion claim"
    assert blocked and "not a valid completion" in blocked[0].payload["reasons"][0]


def test_a_stale_completion_cannot_be_claimed() -> None:
    """The call finished — but the plan moved on, so saying "done" is still false."""

    async def main():
        fm, state, registry, trace = build()
        rec = add_call(registry, tool="book_flight", mutating=True)
        rec.outcome = CallOutcome.COMPLETED_NOW_STALE

        spoken = fm.final(
            "All set.",
            claims=[Claim(kind=ClaimKind.COMPLETED, subject=rec.call_id, warrant="it returned")],
        )
        return spoken.claims

    assert run_virtual(main()) == []


def test_a_valid_completion_is_allowed_through() -> None:
    async def main():
        fm, state, registry, _ = build()
        rec = add_call(registry, tool="book_flight", mutating=True)
        rec.outcome = CallOutcome.COMPLETED_STILL_VALID
        return fm.final(
            "Booked.",
            claims=[Claim(kind=ClaimKind.COMPLETED, subject=rec.call_id, warrant="settled valid")],
            grounded_on=[rec.call_id],
        )

    final = run_virtual(main())
    assert len(final.claims) == 1
    assert final.grounded_on == ["call-1"]


def test_an_unbound_slot_cannot_be_spoken() -> None:
    async def main():
        fm, state, registry, trace = build()
        speak = fm._emit(
            Utterance(
                text="to Mumbai",
                kind=SpeechKind.ACK,
                claims=[Claim(kind=ClaimKind.SLOT_VALUE, subject="destination",
                              value="BOM", warrant="invented")],
            )
        )
        return speak, trace.named("speech_blocked")

    speak, blocked = run_virtual(main())
    assert speak is None
    assert "not bound" in blocked[0].payload["reasons"][0]


def test_a_low_confidence_slot_is_used_but_not_spoken() -> None:
    """Below the threshold a value may still drive a tool call; it just does not
    get asserted aloud as though it were certain."""

    async def main():
        fm, state, registry, _ = build()
        state.set_slot("destination", "BOM", confidence=SPEAK_THRESHOLD - 0.1, source=SlotSource.AUDIO)
        rec = add_call(registry, read_slots={"destination"})
        return fm.acknowledge_dispatch(rec)

    speak = run_virtual(main())
    assert "Mumbai" not in speak.text and "BOM" not in speak.text
    assert speak.text == "Searching flights."


def test_a_stale_value_cannot_be_spoken_even_from_a_replay() -> None:
    """Repeating a past utterance re-verifies its claims, so a repeat cannot
    resurrect a fact that has since stopped being true."""

    async def main():
        fm, state, registry, _ = build()
        state.set_slot("destination", "DEL", surface="Delhi")
        rec = add_call(registry, read_slots={"destination"})
        first = fm.acknowledge_dispatch(rec)

        state.patch_slot("destination", "BOM", surface="Mumbai")  # the user corrected it
        repeat = fm.repeat_last()
        return first.text, repeat

    first, repeat = run_virtual(main())
    assert "Delhi" in first
    assert repeat is None, "the gate must refuse to re-assert a superseded value"


def test_we_say_what_the_user_said_not_the_canonical_code() -> None:
    """The tool argument is "BOM"; the sentence should say "Mumbai".

    Reading an airport code back to someone who named a city is exactly the kind
    of small unnaturalness the quality multiplier exists to penalise.
    """

    async def main():
        fm, state, registry, _ = build()
        state.set_slot("destination", "BOM", surface="Mumbai")
        rec = add_call(registry, read_slots={"destination"})
        speak = fm.acknowledge_dispatch(rec)
        return speak.text, speak.claims[0].value, state.snapshot().slots

    text, claimed, snapshot = run_virtual(main())
    assert text == "Searching flights to Mumbai."
    assert claimed == "BOM", "the claim asserts the canonical value"
    assert snapshot == {"destination": "BOM"}, "the scored snapshot stays canonical"


# =============================================================== acknowledgments

def test_an_acknowledgment_names_the_slots_that_fed_the_call() -> None:
    """A generic "one moment" is cheaper to write and worth less on every axis:
    naming the slots lets the user hear a wrong one immediately."""

    async def main():
        fm, state, registry, _ = build()
        state.set_slot("destination", "Mumbai")
        state.set_slot("date", "Tuesday")
        rec = add_call(registry, read_slots={"destination", "date"})
        return fm.acknowledge_dispatch(rec)

    speak = run_virtual(main())
    assert speak.text == "Searching flights on Tuesday to Mumbai."
    assert speak.kind is SpeechKind.ACK
    assert {c.subject for c in speak.claims} == {"destination", "date", "call-1"}
    assert all(c.warrant for c in speak.claims)


def test_the_same_call_is_not_acknowledged_twice() -> None:
    async def main():
        fm, state, registry, _ = build()
        state.set_slot("destination", "Mumbai")
        rec = add_call(registry, read_slots={"destination"})
        return fm.acknowledge_dispatch(rec), fm.acknowledge_dispatch(rec)

    first, second = run_virtual(main())
    assert first is not None and second is None


def test_tool_descriptions_come_from_the_tool_name() -> None:
    """Unseen tools must still be describable without a phrasing table."""
    assert describe_tool("search_flights") == "searching flights"
    assert describe_tool("book_flight") == "booking flight"
    assert describe_tool("lookup_manual") == "looking up manual"
    assert describe_tool("summon_a_unicorn") == "summoning a unicorn"
    assert describe_tool("reserve_kayak") == "reserving kayak"


def test_unknown_slots_still_get_a_phrase() -> None:
    assert phrase_slot("destination", "Mumbai") == "to Mumbai"
    assert phrase_slot("cabin_class", "business") == "cabin class business"


# =============================================================== rationing

def test_fillers_are_rationed_but_grounded_acks_are_not() -> None:
    """"Excessive fillers" is named in objective 1. A content-free hold is
    rationed; an acknowledgment that names real slots carries information."""

    async def main():
        fm, state, registry, trace = build()
        state.set_slot("destination", "Mumbai")

        first = fm.progress()  # no live calls -> filler
        second = fm.progress()  # budget spent

        acks = [fm.acknowledge_dispatch(add_call(registry, read_slots={"destination"}))
                for _ in range(3)]
        return first, second, acks, trace.named("filler_suppressed")

    first, second, acks, suppressed = run_virtual(main())
    assert first is not None and second is None
    assert suppressed
    assert all(a is not None for a in acks), "grounded acks must not be rationed"


def test_progress_narration_needs_a_live_call() -> None:
    async def main():
        fm, state, registry, _ = build()
        add_call(registry, tool="search_flights")  # PENDING by default
        return fm.progress()

    speak = run_virtual(main())
    assert speak.kind is SpeechKind.PROGRESS
    assert "Still searching flights" in speak.text


def test_speculative_calls_are_not_narrated() -> None:
    """Speculation is invisible: narrating a guess would be claiming we are
    doing something the user never asked for."""

    async def main():
        fm, state, registry, _ = build()
        add_call(registry, speculative=True)
        return fm.progress()

    assert run_virtual(main()).kind is SpeechKind.FILLER


# =============================================================== disclosure

def test_yielding_the_floor_is_recorded_even_though_it_is_silent() -> None:
    async def main():
        fm, state, registry, trace = build()
        state.set_slot("destination", "Mumbai")
        fm.acknowledge_dispatch(add_call(registry, read_slots={"destination"}))
        fm.yield_floor(policy_for(InterruptionKind.BARGE_IN))
        return trace.named("floor_yielded"), fm.speaking

    yielded, speaking = run_virtual(main())
    assert yielded and yielded[0].payload["was_speaking"]
    assert speaking is None


def test_backchannel_does_not_yield_the_floor() -> None:
    async def main():
        fm, state, registry, trace = build()
        fm.yield_floor(policy_for(InterruptionKind.BACKCHANNEL))
        return trace.named("floor_yielded")

    assert run_virtual(main()) == []


def test_an_unverifiable_effect_is_said_out_loud() -> None:
    """Silence here is what makes a state snapshot lie."""

    async def main():
        fm, state, registry, trace = build()
        rec = add_call(registry, tool="create_ticket", args={"subject": "leak"}, mutating=True)
        rec.outcome = CallOutcome.CANCELLED_UNCERTAIN
        speak = fm.disclose_uncertain_effect(rec)
        return speak, trace.named("disclosed_uncertain_effect")

    speak, traced = run_virtual(main())
    assert "can't confirm" in speak.text
    assert speak.kind is SpeechKind.REPAIR
    assert speak.interruptible is False, "a disclosure should not be cut off"
    assert traced


def test_a_compensation_is_said_out_loud_and_specifically() -> None:
    async def main():
        fm, state, registry, _ = build()
        rec = add_call(registry, tool="book_flight", args={"flight_no": "AI101"}, mutating=True)
        rec.outcome = CallOutcome.COMPENSATED
        return fm.disclose_compensation(rec)

    speak = run_virtual(main())
    assert "AI101" in speak.text and "reversed" in speak.text


def test_clarification_is_specific_and_offers_the_options() -> None:
    async def main():
        fm, _, _, trace = build()
        action = fm.clarify(
            "Is that the power LED or the WAN LED?",
            slot="label", options=["router_power_led_red", "router_wan_led_amber"],
            reason="ambiguous",
        )
        return action, trace.named("clarify")

    action, traced = run_virtual(main())
    assert len(action.options) == 2 and action.slot == "label"
    assert traced


def test_the_final_response_carries_the_snapshot() -> None:
    async def main():
        fm, state, registry, _ = build()
        state.set_intent("book_flight")
        state.set_slot("destination", "Mumbai")
        return fm.final("Done.")

    final = run_virtual(main())
    assert final.state.intent == "book_flight"
    assert final.state.slots == {"destination": "Mumbai"}
