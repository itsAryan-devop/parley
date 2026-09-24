"""Interpretation: which of the seven things did the user just do?

The pair that earns its own section is SELF_REPAIR vs SLOT_CORRECTION. They look
identical on the surface and demand opposite behaviour, and getting it wrong in
either direction costs real points: a missed correction leaves a stale call
running, a spurious one cancels work that was fine.
"""

from __future__ import annotations

import pytest

from parley.agent import Interpreter, Lexicon, Turn
from parley.kernel import FloorPolicy, InterruptionKind, WorkPolicy
from parley.protocol import SessionState, parse_manifest

MANIFEST = parse_manifest(
    [
        {
            "name": "search_flights",
            "description": "search available flights",
            "read_only": True,
            "intent": "book_flight",
            "params": [{"name": "origin"}, {"name": "destination"}, {"name": "date"},
                       {"name": "time_of_day", "enum": ["morning", "afternoon", "evening"]}],
        },
        {"name": "book_flight", "mutating": True, "intent": "book_flight",
         "params": [{"name": "flight_no"}]},
        {
            "name": "search_hotels",
            "description": "search available hotels",
            "read_only": True,
            "intent": "book_hotel",
            "params": [{"name": "city"}, {"name": "date"}],
        },
    ]
)


@pytest.fixture
def interp() -> Interpreter:
    return Interpreter(Lexicon.for_manifest(MANIFEST))


@pytest.fixture
def st() -> SessionState:
    s = SessionState(session_id="t")
    s.set_intent("book_flight")
    s.set_slot("destination", "DEL")
    s.set_slot("date", "Tuesday")
    return s


def kind_of(interp, st, text, **kw) -> InterruptionKind:
    return interp.interpret(Turn(text=text, **kw), st, in_flight=kw.pop("in_flight", 1)).kind


# ==================================================== the hard pair

def test_a_different_value_for_a_bound_slot_is_a_correction(interp, st) -> None:
    r = interp.interpret(Turn("to Delhi — no, Mumbai", overlapping_agent_speech=True), st, in_flight=1)
    assert r.kind is InterruptionKind.SLOT_CORRECTION
    assert r.changed_slots == {"destination"}
    assert r.corrections[0].value == "BOM"
    assert r.policy.work is WorkPolicy.SELECTIVE


def test_restating_a_bound_value_is_a_self_repair(interp, st) -> None:
    """"book the… uh… the Tuesday one" — the repair restates what is already bound.

    Re-planning here is how an agent manufactures stale re-runs.
    """
    r = interp.interpret(Turn("book the uh the Tuesday one"), st, in_flight=1)
    assert r.kind is InterruptionKind.SELF_REPAIR
    assert r.restatements and not r.corrections
    assert r.policy.work is WorkPolicy.KEEP_ALL
    assert r.policy.floor is FloorPolicy.CONTINUE
    assert r.policy.replans is False


def test_hesitation_with_no_value_at_all_is_a_self_repair(interp, st) -> None:
    assert kind_of(interp, st, "um, I mean, er") is InterruptionKind.SELF_REPAIR


def test_both_interregnum_types_still_resolve_by_value(interp, st) -> None:
    """"uh, no, Mumbai" has a filled pause AND an editing term.

    The cue words are corroborating evidence; the value comparison decides.
    """
    r = interp.interpret(Turn("uh, no, Mumbai"), st, in_flight=1)
    assert r.features["filled_pause"] == 1.0 and r.features["editing_term"] == 1.0
    assert r.kind is InterruptionKind.SLOT_CORRECTION


def test_correcting_a_slot_that_was_never_bound_is_just_new_information(interp) -> None:
    st = SessionState(session_id="t")
    st.set_intent("book_flight")
    r = interp.interpret(Turn("to Mumbai please", end_of_turn=True), st, in_flight=0)
    assert r.kind is InterruptionKind.NEW_REQUEST
    assert r.additions and not r.corrections


# ==================================================== the other five

def test_a_different_goal_is_a_goal_switch(interp, st) -> None:
    r = interp.interpret(Turn("forget flights, find me a hotel"), st, in_flight=2)
    assert r.kind is InterruptionKind.GOAL_SWITCH
    assert r.policy.work is WorkPolicy.CANCEL_ALL
    assert r.policy.replans is True


def test_intent_cues_come_from_the_manifest_not_a_hardcoded_list(interp, st) -> None:
    """"hotel" separates the goals; "book" and "search" appear in both and cannot."""
    lex = Lexicon.for_manifest(MANIFEST)
    assert "hotel" in lex.intent_cues["book_hotel"]
    assert "flight" in lex.intent_cues["book_flight"]
    assert "book" not in lex.intent_cues["book_hotel"]
    assert "search" not in lex.intent_cues["book_flight"]


def test_an_unseen_tool_brings_its_own_intent_vocabulary() -> None:
    manifest = parse_manifest(
        [
            {"name": "search_flights", "read_only": True, "intent": "book_flight", "params": []},
            {"name": "reserve_kayak", "description": "reserve a kayak", "mutating": True,
             "intent": "rent_kayak", "params": []},
        ]
    )
    lex = Lexicon.for_manifest(manifest)
    assert "kayak" in lex.intent_cues["rent_kayak"]

    st = SessionState(session_id="t")
    st.set_intent("book_flight")
    r = Interpreter(lex).interpret(Turn("actually get me a kayak"), st, in_flight=1)
    assert r.kind is InterruptionKind.GOAL_SWITCH


def test_narrowing_an_existing_query_is_a_refinement(interp, st) -> None:
    """Must not cancel: the call can finish and its result be filtered."""
    r = interp.interpret(Turn("make it morning flights only"), st, in_flight=1)
    assert r.kind is InterruptionKind.REFINEMENT
    assert r.policy.work is WorkPolicy.KEEP_ALL
    assert r.policy.floor is FloorPolicy.ADAPT


def test_changing_an_already_bound_narrowing_slot_is_a_correction(interp, st) -> None:
    """Refinement only applies the first time. Once `time_of_day` fed a call's
    arguments, changing it invalidates that call."""
    st.set_slot("time_of_day", "morning")
    r = interp.interpret(Turn("actually make it evening"), st, in_flight=1)
    assert r.kind is InterruptionKind.SLOT_CORRECTION
    assert r.changed_slots == {"time_of_day"}


def test_a_repeat_request_never_re_runs_a_tool(interp, st) -> None:
    for text in ["sorry, say that again", "what was that?", "come again", "repeat that please"]:
        r = interp.interpret(Turn(text, overlapping_agent_speech=True), st, in_flight=1)
        assert r.kind is InterruptionKind.REPEAT_REQUEST, text
        assert r.policy.work is WorkPolicy.KEEP_ALL


def test_a_floor_grab_yields_the_floor_but_keeps_the_work(interp, st) -> None:
    r = interp.interpret(Turn("wait, hold on", overlapping_agent_speech=True), st, in_flight=2)
    assert r.kind is InterruptionKind.BARGE_IN
    assert r.policy.floor is FloorPolicy.YIELD
    assert r.policy.work is WorkPolicy.KEEP_ALL


def test_a_backchannel_does_not_stop_us_speaking(interp, st) -> None:
    """A VAD-triggered system stops on "mhm". That is the VAD's mistake."""
    for text in ["mhm", "yeah", "okay", "right"]:
        r = interp.interpret(Turn(text, overlapping_agent_speech=True), st, in_flight=1)
        assert r.kind is InterruptionKind.BACKCHANNEL, text
        assert r.policy.floor is FloorPolicy.CONTINUE
        assert r.policy.is_interruption is False


def test_punctuation_does_not_hide_cue_phrases(interp, st) -> None:
    """Regression: commas and question marks swallowed multi-word cues.

    "uh, no, Mumbai" hid the editing term behind a comma and "what was that?"
    hid the repeat cue behind a question mark. Both misclassified into branches
    with different cancellation behaviour, so this is a scoring bug, not cosmetic.
    """
    assert interp.interpret(Turn("uh, no, Mumbai"), st, in_flight=1).features["editing_term"] == 1.0
    assert interp.interpret(Turn("what was that?"), st, in_flight=1).kind is InterruptionKind.REPEAT_REQUEST
    assert interp.interpret(Turn("Hold on!!", overlapping_agent_speech=True), st, in_flight=1).kind is (
        InterruptionKind.BARGE_IN
    )


def test_multiword_floor_grabs_are_recognised_as_phrases(interp, st) -> None:
    """"hold on" is a floor grab; "hold" and "on" separately are not."""
    r = interp.interpret(Turn("wait, hold on", overlapping_agent_speech=True), st, in_flight=1)
    assert r.features["floor_grab_only"] == 1.0
    assert r.kind is InterruptionKind.BARGE_IN


def test_the_same_word_means_different_things_in_speech_and_silence(interp, st) -> None:
    """"wait" over our voice is a floor grab; in silence it is hesitation."""
    over = interp.interpret(Turn("wait", overlapping_agent_speech=True), st, in_flight=1)
    quiet = interp.interpret(Turn("wait, um"), st, in_flight=1)
    assert over.kind is InterruptionKind.BARGE_IN
    assert quiet.kind is InterruptionKind.SELF_REPAIR


# ==================================================== extraction mechanics

def test_longest_surface_form_wins(interp) -> None:
    st = SessionState(session_id="t")
    lex = Lexicon.for_manifest(MANIFEST)
    found = {m.slot: m.value for m in lex.find("fly to new delhi the day after tomorrow")}
    assert found["destination"] == "DEL"
    assert found["date"] == "day_after", "'tomorrow' must not shadow 'day after tomorrow'"


def test_manifest_enums_become_vocabulary() -> None:
    lex = Lexicon.for_manifest(MANIFEST)
    assert lex.values["time_of_day"]["evening"] == "evening"


def test_word_boundaries_are_respected() -> None:
    lex = Lexicon.for_manifest(MANIFEST)
    assert lex.find("nominal goanna") == [], "substring hits must not fire"


def test_pattern_slots_extract_and_coerce() -> None:
    lex = Lexicon.for_manifest(MANIFEST)
    found = {m.slot: m.value for m in lex.find("book AI101 for 3 people")}
    assert found["flight_no"] == "AI101"
    assert found["party_size"] == 3


# ==================================================== trace legibility

def test_every_interpretation_explains_itself(interp, st) -> None:
    """The jury will ask "where did that come from?". The answer is in the trace."""
    r = interp.interpret(Turn("no, Mumbai", overlapping_agent_speech=True), st, in_flight=1)
    payload = r.to_payload()
    assert payload["rationale"]
    assert payload["kind"] == "slot_correction"
    assert payload["corrections"] == [{"slot": "destination", "value": "BOM", "span": "Mumbai"}]
    assert payload["policy"]["work"] == "selective"
    assert payload["features"]["n_corrections"] == 1.0
