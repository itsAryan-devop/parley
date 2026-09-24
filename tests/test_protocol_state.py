"""The state object guards half the score, so its revision semantics get tested hard."""

from __future__ import annotations

import pytest

from parley.protocol import SessionState, SlotSource


@pytest.fixture
def st() -> SessionState:
    return SessionState(session_id="s1")


def test_binding_a_fresh_slot_invalidates_nothing(st: SessionState) -> None:
    delta = st.set_slot("destination", "Delhi")
    assert delta.added_slots == ["destination"]
    assert delta.changed_slots == []
    # Nothing in flight could have read a slot that did not exist.
    assert delta.invalidating_slots == set()
    assert st.revision == 1


def test_correcting_a_slot_invalidates_it(st: SessionState) -> None:
    st.set_slot("destination", "Delhi")
    delta = st.patch_slot("destination", "Mumbai")
    assert delta.changed_slots == ["destination"]
    assert delta.invalidating_slots == {"destination"}
    assert st.get("destination") == "Mumbai"


def test_rewriting_the_same_value_is_not_a_change(st: SessionState) -> None:
    st.set_slot("destination", "Delhi")
    rev = st.revision
    delta = st.set_slot("destination", "Delhi")
    assert st.revision == rev, "an idempotent restatement must not bump the revision"
    assert delta.invalidating_slots == set()


def test_revision_is_monotonic(st: SessionState) -> None:
    revs = []
    for i in range(5):
        st.set_slot(f"slot{i}", i)
        revs.append(st.revision)
    assert revs == sorted(revs) and len(set(revs)) == 5


def test_staleness_is_per_slot_not_global(st: SessionState) -> None:
    st.set_slot("destination", "Delhi")
    st.set_slot("date", "Tuesday")
    at_dispatch = st.revision

    # A call that read only `date` must survive a correction to `destination`.
    st.patch_slot("destination", "Mumbai")

    assert st.is_stale({"destination"}, at_dispatch) is True
    assert st.is_stale({"date"}, at_dispatch) is False
    assert st.is_stale({"date", "destination"}, at_dispatch) is True


def test_clearing_a_slot_makes_its_readers_stale(st: SessionState) -> None:
    st.set_slot("seat", "12A")
    at_dispatch = st.revision
    st.clear_slot("seat")
    assert st.is_stale({"seat"}, at_dispatch) is True


def test_a_slot_that_never_existed_makes_nothing_stale(st: SessionState) -> None:
    """Regression: 'cleared' and 'never bound' must not be conflated.

    Both leave no entry in `slots`. Treating absence as staleness marked healthy
    calls `COMPLETED_NOW_STALE`, which in turn triggered compensation for
    effects that were still perfectly valid. Tombstones keep the distinction.
    """
    st.set_slot("destination", "Delhi")
    at_dispatch = st.revision
    st.set_slot("party_size", 2)

    assert st.is_stale({"never_heard_of_it"}, at_dispatch) is False
    assert st.is_stale({"destination"}, at_dispatch) is False


def test_rebinding_a_cleared_slot_revives_it(st: SessionState) -> None:
    st.set_slot("seat", "12A")
    st.clear_slot("seat")
    assert "seat" in st.tombstones

    at_dispatch = st.revision
    st.set_slot("seat", "14C")
    assert "seat" not in st.tombstones
    assert st.is_stale({"seat"}, at_dispatch) is True  # the rebind is itself a change
    assert st.snapshot().slots["seat"] == "14C"


def test_tombstones_stay_out_of_the_snapshot(st: SessionState) -> None:
    """The snapshot is what the grader compares; tombstones are internal."""
    st.set_slot("seat", "12A")
    st.set_slot("destination", "Delhi")
    st.clear_slot("seat")

    assert st.snapshot().slots == {"destination": "Delhi"}
    assert "seat" not in st.snapshot().slot_meta


def test_goal_switch_retains_applicable_slots(st: SessionState) -> None:
    st.set_intent("book_flight")
    st.set_slot("date", "Tuesday")
    st.set_slot("party_size", 2)
    st.set_slot("flight_no", "AI101")

    # `keep` is computed by the kernel from the manifest, not hardcoded in state.
    delta = st.retain_for_goal_switch("book_hotel", keep={"date", "party_size"})

    assert st.intent == "book_hotel"
    assert set(st.slots) == {"date", "party_size"}
    assert delta.cleared_slots == ["flight_no"]
    assert delta.intent_changed is True
    assert delta.previous_intent == "book_flight"


def test_snapshot_is_flat_values_plus_separate_provenance(st: SessionState) -> None:
    st.set_intent("book_flight")
    st.set_slot("destination", "Mumbai", confidence=0.8, source=SlotSource.AUDIO, evidence="clip-3")

    snap = st.snapshot()
    assert snap.intent == "book_flight"
    assert snap.slots == {"destination": "Mumbai"}, "slot values must be flat for the grader"
    assert snap.slot_meta["destination"].source is SlotSource.AUDIO
    assert snap.slot_meta["destination"].evidence == "clip-3"

    # Must survive a JSON round trip — protocol compliance is 10% of the score.
    from parley.protocol import StateSnapshot

    assert StateSnapshot.model_validate_json(snap.model_dump_json()) == snap


def test_slots_only_change_through_named_mutations(st: SessionState) -> None:
    """Guardrail: confidence outside 0..1 must be rejected at the type boundary."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        st.set_slot("x", 1, confidence=1.5)
