"""The trace is the scoring surface: "scored 0-100 based strictly on trace logs".

So these tests are about durability and fidelity, not convenience. Anything that
could silently drop or corrupt a record is a scoring bug, not a logging bug.
"""

from __future__ import annotations

import json

import pytest

from harness.trace import RecordKind, Trace


def test_records_are_ordered_and_sequenced() -> None:
    tr = Trace(session_id="s")
    tr.event(0.0, "session_start")
    tr.action(120.0, "speak")
    tr.action(120.0, "tool_call")  # same millisecond, still ordered

    assert [r.seq for r in tr] == [1, 2, 3]
    assert [r.t for r in tr] == [0.0, 120.0, 120.0]


def test_payload_may_use_header_words_as_keys() -> None:
    """Regression: `kind`/`name`/`t` in a payload must not collide with the header.

    A fault record naturally wants its own `kind`. Before positional-only
    parameters this raised TypeError mid-scenario, which would have destroyed
    the trace for that run.
    """
    tr = Trace()
    rec = tr.tool(70.0, "fault", kind="permanent", name="book_flight", t=999, seq=7)

    assert rec.kind is RecordKind.TOOL and rec.name == "fault" and rec.t == 70.0
    assert rec.payload == {"kind": "permanent", "name": "book_flight", "t": 999, "seq": 7}


def test_records_are_flushed_immediately(tmp_path) -> None:
    """A scenario that blows the 120 s cap must still leave everything up to the cut."""
    path = tmp_path / "trace.jsonl"
    tr = Trace(path, session_id="s")
    tr.event(0.0, "session_start")
    tr.action(50.0, "speak", text="checking")

    # Read from disk without closing the writer.
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["payload"]["text"] == "checking"


def test_round_trips_through_disk(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    with Trace(path, session_id="s") as tr:
        tr.event(0.0, "transcript_chunk", text="to Delhi", end_of_turn=False)
        tr.kernel(10.0, "invalidate", call_id="c1", by_slots=["destination"])
        tr.action(12.0, "cancel", call_id="c1")

    revived = Trace.load(path)
    assert [r.name for r in revived] == ["transcript_chunk", "invalidate", "cancel"]
    assert revived.named("invalidate")[0].payload["by_slots"] == ["destination"]


def test_pydantic_models_are_serialised_not_repr_d() -> None:
    """The grader and the viewer do not import our types."""
    from parley.protocol import SessionState

    st = SessionState(session_id="s")
    st.set_intent("book_flight")
    st.set_slot("destination", "Mumbai")

    tr = Trace()
    rec = tr.action(300.0, "final_response", state=st.snapshot())

    body = json.loads(rec.model_dump_json())
    assert body["payload"]["state"]["slots"] == {"destination": "Mumbai"}
    assert body["payload"]["state"]["intent"] == "book_flight"


def test_enums_and_paths_are_flattened(tmp_path) -> None:
    from parley.protocol import SpeechKind

    tr = Trace()
    rec = tr.action(1.0, "speak", kind=SpeechKind.ACK, source=tmp_path / "x.wav")
    body = json.loads(rec.model_dump_json())
    assert body["payload"]["kind"] == "ack"
    assert isinstance(body["payload"]["source"], str)


def test_non_serialisable_values_degrade_rather_than_crash() -> None:
    """Losing the whole trace to one odd value would be catastrophic; losing
    fidelity on that one value is merely bad."""

    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    tr = Trace()
    rec = tr.note(1.0, "debug", obj=Opaque())
    assert "opaque" in json.dumps(rec.payload)


def test_query_helpers() -> None:
    tr = Trace()
    tr.action(1.0, "tool_call", call_id="c1")
    tr.action(2.0, "tool_call", call_id="c2")
    tr.action(3.0, "cancel", call_id="c1")
    tr.event(4.0, "tool_result", call_id="c2")

    assert len(tr.named("tool_call")) == 2
    assert len(tr.of_kind(RecordKind.ACTION)) == 3
    assert len(tr.named("tool_result", RecordKind.EVENT)) == 1
    assert len(tr) == 4


def test_unknown_record_kind_is_rejected() -> None:
    from pydantic import ValidationError

    from harness.trace import TraceRecord

    with pytest.raises(ValidationError):
        TraceRecord(t=0, seq=1, kind="gossip", name="x")
