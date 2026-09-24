"""Can the agent run against a kit whose wire format we did not design?

The official evaluation kit was never released publicly, so the largest single
risk here is that its schema differs from ours. The README claims swapping
harnesses is "a boundary change, not a rewrite". This file is that claim under
test rather than asserted.

The foreign schema below is deliberately unlike ours in every way we could
plausibly be wrong about:

    * a different discriminator key, with different type names
    * timestamps in SECONDS, not milliseconds
    * `is_final` instead of `end_of_turn`
    * payloads nested inside a `data` envelope
    * heartbeat events that do not exist in our protocol at all
    * fields delivered out of chronological order

If the agent completes such a scenario with the right snapshot and no duplicate
effects, the boundary is real.
"""

from __future__ import annotations

import pytest

from harness.adapter import AdapterError, FieldMap, actions_to, translate_stream
from harness.clock import Clock, run_virtual
from harness.mockenv import EnvConfig, MockEnvironment, World
from harness.trace import Trace
from parley.agent.agent import ParleyAgent
from parley.agent.model import InterruptionModel
from parley.protocol import parse_manifest
from parley.protocol.events import TranscriptChunk

FOREIGN = FieldMap(
    type_key="kind",
    time_key="ts",
    time_scale=1000.0,  # their clock is in seconds
    type_names={
        "begin": "session_start",
        "tools": "tool_manifest",
        "user_text": "transcript_chunk",
        "user_cut_in": "interruption",
        "picture": "video_frame",
        "recording": "audio_clip",
        "tool_reply": "tool_result",
        "finish": "session_end",
    },
    field_names={
        "transcript_chunk": {"content": "text", "is_final": "end_of_turn"},
        "session_start": {"conversation_id": "session_id"},
        "tool_manifest": {"catalogue": "manifest"},
        "video_frame": {"id": "frame_id", "uri": "path"},
        "audio_clip": {"id": "clip_id", "uri": "path"},
    },
    unwrap="data",
    defaults={"session_end": {"reason": "complete"}},
    drop={"heartbeat", "metrics"},
)

MANIFEST = [
    {"name": "search_flights", "description": "search available flights", "read_only": True,
     "intent": "book_flight",
     "params": [{"name": "destination", "required": True}, {"name": "date"}]},
    {"name": "book_flight", "mutating": True, "intent": "book_flight",
     "params": [{"name": "flight_no", "required": True}]},
    {"name": "search_hotels", "description": "search available hotels", "read_only": True,
     "intent": "book_hotel", "params": [{"name": "city", "required": True}]},
]


# ------------------------------------------------------------------ unit level

def test_seconds_become_milliseconds() -> None:
    ev = FOREIGN({"kind": "user_text", "ts": 1.25, "data": {"content": "hello", "is_final": True}})
    assert ev["t"] == 1250.0
    assert ev["text"] == "hello" and ev["end_of_turn"] is True


def test_envelope_is_unwrapped() -> None:
    ev = FOREIGN({"kind": "tool_reply", "ts": 2.0,
                  "data": {"call_id": "c1", "ok": True, "result": {"n": 3}}})
    assert ev["call_id"] == "c1" and ev["result"] == {"n": 3}


def test_dropped_types_are_dropped_deliberately() -> None:
    assert FOREIGN({"kind": "heartbeat", "ts": 1.0}) is None


def test_an_unmappable_event_is_loud() -> None:
    """A foreign event silently discarded at the boundary is indistinguishable
    from an agent that ignored it."""
    strict = FieldMap(type_key="kind", type_names={"known": None})
    with pytest.raises(AdapterError):
        strict({"kind": "known", "t": 0})


def test_stream_is_reordered_on_translated_time() -> None:
    events = translate_stream(
        [
            {"kind": "user_text", "ts": 2.0, "data": {"content": "second", "is_final": True}},
            {"kind": "user_text", "ts": 0.5, "data": {"content": "first", "is_final": False}},
            {"kind": "heartbeat", "ts": 1.0},
        ],
        FOREIGN,
    )
    assert [e.text for e in events] == ["first", "second"]
    assert [e.t for e in events] == [500.0, 2000.0]
    assert all(isinstance(e, TranscriptChunk) for e in events)


# ------------------------------------------------------------- the real claim

def test_the_agent_runs_a_whole_scenario_off_a_foreign_stream() -> None:
    """Nothing in `parley/` knows this schema exists."""
    foreign_events = [
        {"kind": "begin", "ts": 0.0, "data": {"conversation_id": "FOREIGN-1"}},
        {"kind": "tools", "ts": 0.0, "data": {"catalogue": {"tools": MANIFEST}}},
        {"kind": "heartbeat", "ts": 0.05},
        {"kind": "user_text", "ts": 0.1, "data": {"content": "find me a flight to Delhi"}},
        {"kind": "metrics", "ts": 0.15, "data": {"cpu": 0.4}},
        {"kind": "user_text", "ts": 0.4, "data": {"content": "on Tuesday", "is_final": True}},
        {"kind": "user_cut_in", "ts": 0.9, "data": {"source": "vad"}},
        {"kind": "user_text", "ts": 0.92, "data": {"content": "no, Mumbai", "is_final": True}},
        {"kind": "user_text", "ts": 2.2, "data": {"content": "book AI101", "is_final": True}},
        {"kind": "finish", "ts": 5.0},
    ]
    events = translate_stream(foreign_events, FOREIGN)

    async def main():
        clock, trace = Clock(), Trace(session_id="foreign")
        env = MockEnvironment(
            clock, trace, World(),
            EnvConfig(latency_ms={"search_flights": 900, "book_flight": 400}),
        )
        actions: list = []
        agent = ParleyAgent(
            "foreign", clock=clock, trace=trace, executor=env.call,
            manifest=parse_manifest({"tools": MANIFEST}),
            model=InterruptionModel.load_default(),
            on_action=actions.append,
        )

        async def stream():
            for event in events:
                delay = event.t - clock.now
                if delay > 0:
                    await clock.sleep(delay)
                yield event

        await agent.run(stream())
        return agent, env.world, actions

    agent, world, actions = run_virtual(main())
    snapshot = agent.state.snapshot()

    assert snapshot.intent == "book_flight"
    assert snapshot.slots["destination"] == "BOM", "the mid-stream correction was lost"
    assert snapshot.slots["date"] == "Tuesday", "an unrelated slot was dropped"
    assert snapshot.slots["flight_no"] == "AI101"

    live = world.live_effects()
    assert len(live) == 1 and live[0].tool == "book_flight"
    assert world.duplicates() == {}

    cancelled = [c for c in agent.kernel.registry if c.cancel_reason]
    assert any("slot_correction" in (c.cancel_reason or "") for c in cancelled), (
        "the Delhi search should have been cancelled by the correction"
    )
    assert any(a.type.value == "final_response" for a in actions)


def test_our_actions_translate_back_out() -> None:
    """The return leg of the boundary: their kit will want its own shape."""
    from parley.protocol.actions import Cancel, ToolCall

    outbound = actions_to(
        [
            ToolCall(t=120.0, call_id="c1", tool="search_flights", args={"destination": "BOM"}),
            Cancel(t=300.0, call_id="c1", reason="slot_correction:destination"),
        ],
        lambda body: {
            "kind": {"tool_call": "invoke", "cancel": "abort"}[body["type"]],
            "ts": body["t"] / 1000.0,
            "data": {k: v for k, v in body.items() if k not in ("type", "t", "seq")},
        },
    )

    assert outbound[0]["kind"] == "invoke" and outbound[0]["ts"] == 0.12
    assert outbound[0]["data"]["call_id"] == "c1"
    assert outbound[1]["kind"] == "abort" and outbound[1]["data"]["reason"].startswith("slot_correction")
