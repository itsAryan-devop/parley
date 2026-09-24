"""Manifest parsing and event/action wire shapes.

The real kit's dialect is unknown, so the manifest parser is tested against every
spelling we might plausibly be handed. `mutating` must never be *guessed* for a
tool that declares a compensator — that would risk a duplicate state change, and
duplicate state-changing calls are the one thing the 10% block is explicit about.
"""

from __future__ import annotations

import pytest

from parley.protocol import (
    Cancel,
    ClaimKind,
    ManifestError,
    SessionState,
    Speak,
    SpeechKind,
    ToolCall,
    parse_action,
    parse_event,
    parse_manifest,
)
from parley.protocol.actions import Claim, FinalResponse
from parley.protocol.events import TranscriptChunk


# --------------------------------------------------------------------- manifest

def test_parses_list_dialect_with_json_schema_params() -> None:
    m = parse_manifest(
        {
            "tools": [
                {
                    "name": "search_flights",
                    "description": "find flights",
                    "read_only": True,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "origin": {"type": "string"},
                            "destination": {"type": "string"},
                            "date": {"type": "string"},
                        },
                        "required": ["origin", "destination"],
                    },
                }
            ]
        }
    )
    spec = m["search_flights"]
    assert spec.mutating is False
    assert spec.param_names == {"origin", "destination", "date"}
    assert spec.required_params == {"origin", "destination"}


def test_parses_dict_dialect_and_flat_param_list() -> None:
    m = parse_manifest(
        {"tools": {"book_flight": {"state_modifying": True, "params": [{"name": "flight_no", "required": True}]}}}
    )
    assert m["book_flight"].mutating is True
    assert m["book_flight"].required_params == {"flight_no"}


def test_parses_bare_list() -> None:
    m = parse_manifest([{"name": "lookup_manual", "readOnly": True}])
    assert "lookup_manual" in m
    assert m.read_only[0].name == "lookup_manual"


@pytest.mark.parametrize(
    "entry,expected",
    [
        ({"name": "t", "mutating": True}, True),
        ({"name": "t", "state_modifying": True}, True),
        ({"name": "t", "stateModifying": True}, True),
        ({"name": "t", "write": True}, True),
        ({"name": "t", "read_only": True}, False),
        ({"name": "t", "readOnly": False}, True),
        ({"name": "t", "safe": True}, False),
        ({"name": "t"}, False),  # unflagged defaults to read-only
    ],
)
def test_mutating_flag_dialects(entry: dict, expected: bool) -> None:
    assert parse_manifest([entry])["t"].mutating is expected


def test_compensator_without_an_explicit_flag_is_a_hard_error() -> None:
    with pytest.raises(ManifestError, match="compensator"):
        parse_manifest([{"name": "cancel_booking", "inverse_of": "book_flight"}])


def test_inverse_lookup_finds_the_compensating_tool() -> None:
    m = parse_manifest(
        [
            {"name": "book_flight", "mutating": True},
            {"name": "cancel_booking", "mutating": True, "inverse_of": "book_flight"},
        ]
    )
    assert m.inverse_for("book_flight").name == "cancel_booking"
    assert m.inverse_for("cancel_booking") is None


def test_params_for_intent_drives_goal_switch_retention() -> None:
    m = parse_manifest(
        [
            {"name": "search_flights", "intent": "book_flight", "params": [{"name": "date"}, {"name": "flight_no"}]},
            {"name": "search_hotels", "intent": "book_hotel", "params": [{"name": "date"}, {"name": "city"}]},
        ]
    )
    assert m.params_for_intent("book_hotel") == {"date", "city"}
    assert m.intents() == {"book_flight", "book_hotel"}


def test_unseen_tool_needs_no_code_change() -> None:
    """Nothing in the kernel may key off a known tool name."""
    m = parse_manifest([{"name": "summon_a_unicorn", "mutating": True, "params": [{"name": "colour"}]}])
    spec = m["summon_a_unicorn"]
    assert spec.mutating and spec.param_names == {"colour"}


# ------------------------------------------------------------------- wire types

def test_event_discriminator_round_trips() -> None:
    ev = parse_event({"type": "transcript_chunk", "t": 120.0, "text": "to Delhi", "end_of_turn": False})
    assert isinstance(ev, TranscriptChunk)
    assert ev.end_of_turn is False
    assert parse_event(ev.model_dump(mode="json")) == ev


def test_tool_result_event_parses() -> None:
    ev = parse_event({"type": "tool_result", "t": 900.0, "call_id": "c1", "ok": True, "result": {"n": 3}})
    assert ev.call_id == "c1" and ev.result == {"n": 3}


def test_unknown_event_type_is_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        parse_event({"type": "telepathy", "t": 0.0})


def test_tool_call_action_carries_the_dataflow_edge() -> None:
    a = parse_action(
        {
            "type": "tool_call",
            "t": 50.0,
            "call_id": "c1",
            "tool": "search_flights",
            "args": {"destination": "Delhi"},
            "read_slots": ["destination"],
            "state_revision": 3,
        }
    )
    assert isinstance(a, ToolCall)
    assert a.read_slots == ["destination"] and a.state_revision == 3
    assert a.mutating is False and a.speculative is False


def test_cancel_records_why() -> None:
    c = Cancel(t=210.0, call_id="c1", reason="slot_correction:destination", invalidated_by_slots=["destination"])
    assert parse_action(c.model_dump(mode="json")) == c


def test_speak_carries_claims_with_warrants() -> None:
    s = Speak(
        t=180.0,
        text="checking flights to Mumbai for Tuesday",
        kind=SpeechKind.ACK,
        claims=[
            Claim(kind=ClaimKind.SLOT_VALUE, subject="destination", value="Mumbai", warrant="slot bound at rev 4"),
            Claim(kind=ClaimKind.SLOT_VALUE, subject="date", value="Tuesday", warrant="slot bound at rev 3"),
        ],
    )
    assert parse_action(s.model_dump(mode="json")) == s
    assert all(c.warrant for c in s.claims), "every claim must carry its warrant into the trace"


def test_final_response_embeds_a_valid_snapshot() -> None:
    st = SessionState(session_id="s1")
    st.set_intent("book_flight")
    st.set_slot("destination", "Mumbai")

    fr = FinalResponse(t=3000.0, text="Booked.", state=st.snapshot(), grounded_on=["c2"])
    revived = parse_action(fr.model_dump(mode="json"))
    assert revived.state.slots == {"destination": "Mumbai"}
    assert revived.state.intent == "book_flight"
