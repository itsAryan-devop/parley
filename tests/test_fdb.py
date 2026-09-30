"""The FDB-v3 port: tool guard and turn detector. No LiveKit, no API keys.

Utterances here are our own phrasings, not FDB-v3 items (the guide disqualifies
tuning on benchmark test items).
"""

import asyncio

import pytest

from parley.fdb import ParleyTurnDetector, ToolGuard
from parley.fdb.args import canonical_spoken_id, clean_args
from parley.fdb.guard import action_key

TOOLS = [
    "search_flights", "book_flight", "update_identity_doc", "get_card_benefits",
    "get_exchange_rate", "modify_autopay", "search_apartments", "calculate_commute",
    "update_search_filter", "track_order", "search_products", "add_to_cart",
]


async def _no_wait(_):
    return None


# -- guard -------------------------------------------------------------------

def test_action_key_ignores_case_whitespace_and_none():
    assert action_key("book_flight", {"passenger_name": "Ana  Ruiz"}) == \
        action_key("book_flight", {"passenger_name": "ana ruiz"})
    assert action_key("search_products", {"query": "mug", "max_price": None}) == \
        action_key("search_products", {"query": "mug"})
    assert action_key("get_exchange_rate", {"amount": 50.0}) == \
        action_key("get_exchange_rate", {"amount": 50})


async def test_duplicate_action_executes_once():
    guard, runs = ToolGuard(sleep=_no_wait), []
    first, ran1 = await guard.run("book_flight", {"passenger_name": "Ana"}, lambda: runs.append(1) or "ok")
    second, ran2 = await guard.run("book_flight", {"passenger_name": "ana"}, lambda: runs.append(1) or "ok")
    assert (ran1, ran2) == (True, False) and len(runs) == 1
    assert second["status"] == "already_done" and second["result"] == "ok"


async def test_different_arguments_are_different_actions():
    guard = ToolGuard(sleep=_no_wait)
    _, a = await guard.run("search_flights", {"destination": "Rome", "date": "May 2"}, lambda: 1)
    _, b = await guard.run("search_flights", {"destination": "Madrid", "date": "May 2"}, lambda: 1)
    assert a and b


async def test_user_resuming_inside_grace_cancels_before_effect():
    guard, runs = ToolGuard(grace_s=0.05), []

    async def call():
        return await guard.run("search_flights", {"destination": "Rome"}, lambda: runs.append(1))

    task = asyncio.create_task(call())
    await asyncio.sleep(0.01)
    guard.user_started_speaking()          # "... actually, no --"
    result, executed = await task
    assert not executed and runs == [] and result["status"] == "not_executed"
    guard.user_stopped_speaking()          # "... make it Madrid."
    guard.transcript_final()               # ... and STT delivers it
    # the corrected request still goes through afterwards
    _, ok = await guard.run("search_flights", {"destination": "Madrid"}, lambda: runs.append(1))
    assert ok and runs == [1]


async def test_call_issued_while_user_is_still_talking_is_deferred():
    # The turn ended on a pause the user talked straight through: speech began
    # before the call was issued, so no onset lands inside the grace window.
    guard, runs = ToolGuard(grace_s=0.01), []
    guard.user_started_speaking()          # "... from checking -- hang on, no"
    result, executed = await guard.run(
        "modify_autopay", {"bill_type": "water", "source_account": "checking"},
        lambda: runs.append(1))
    assert not executed and runs == [] and result["status"] == "not_executed"
    guard.user_stopped_speaking()
    guard.transcript_final()
    _, ok = await guard.run(
        "modify_autopay", {"bill_type": "water", "source_account": "credit card"},
        lambda: runs.append(1))
    assert ok and runs == [1]


async def test_call_waits_for_speech_stt_has_not_delivered():
    # The user said "checking", then "-- wait, no, savings" and fell silent just
    # before the call. VAD says silent, but that speech is still in STT.
    now = [100.0]
    guard, runs = ToolGuard(grace_s=0.0, clock=lambda: now[0]), []
    guard.user_started_speaking(); guard.user_stopped_speaking(); guard.transcript_final()
    guard.user_started_speaking(); guard.user_stopped_speaking()      # not transcribed yet
    _, ok = await guard.run("modify_autopay", {"bill_type": "gas", "source_account": "checking"},
                            lambda: runs.append(1))
    assert not ok and runs == []
    guard.transcript_final()                                          # the correction arrives
    _, ok = await guard.run("modify_autopay", {"bill_type": "gas", "source_account": "savings"},
                            lambda: runs.append(1))
    assert ok and runs == [1]


async def test_untranscribed_noise_does_not_block_forever():
    now = [100.0]
    guard, runs = ToolGuard(grace_s=0.0, clock=lambda: now[0]), []
    guard.user_started_speaking(); guard.user_stopped_speaking()      # a cough, never transcribed
    now[0] += 5.0
    _, ok = await guard.run("track_order", {"order_id": "Z1"}, lambda: runs.append(1))
    assert ok and runs == [1]


async def test_concurrent_copies_claim_once():
    guard, runs = ToolGuard(grace_s=0.01), []
    results = await asyncio.gather(*[
        guard.run("add_to_cart", {"product_id": "P1", "quantity": 1}, lambda: runs.append(1))
        for _ in range(3)])
    assert len(runs) == 1 and sum(executed for _, executed in results) == 1


async def test_async_execute_is_awaited():
    async def execute():
        return {"status": "success"}
    result, executed = await ToolGuard(sleep=_no_wait).run("track_order", {"order_id": "X1"}, execute)
    assert executed and result == {"status": "success"}


# -- turn detector -----------------------------------------------------------

@pytest.fixture(scope="module")
def detector():
    return ParleyTurnDetector(TOOLS, vad_silence_s=0.55)


@pytest.mark.parametrize("text", [
    "Find me a flight to Lisbon on June 4th.",
    "Where is my order QX77?",
    "Put three of those in my cart please.",
    "Convert 80 GBP to JPY.",
    "Flights to Oslo, sorry, I mean Bergen.",
    "Scratch that, make it Porto instead.",
])
def test_complete_requests_release_the_turn(detector, text):
    assert detector.probability(text) > 0.5, text


@pytest.mark.parametrize("text", [
    "Find me a flight to Lisbon, actually, no",
    "Find me a flight to Lisbon, um",
    "Flights to Oslo, sorry, I mean",
    "I need to update my passport number to",
    "Search apartments in Austin and",
])
def test_mid_correction_and_dangling_turns_are_held(detector, text):
    assert detector.probability(text) < 0.15, text


@pytest.mark.parametrize("text", [
    # Whisper capitalises each segment ("  For") and writes "I'm"; neither is a
    # proper noun, and a trailing "..." means the speaker trailed off.
    "So, um...  I'm trying to get, uh,  For the...",
    "Okay.  I'll need a, um,  Something like...",
    "Hmm, I've been meaning to, uh…",
])
def test_trailing_off_with_stt_capitals_is_held(detector, text):
    assert detector.probability(text) < 0.15, text


async def test_livekit_protocol_surface(detector):
    class Msg:
        role, text_content = "user", "Track order ZZ9 for me."

    class Ctx:
        items = [Msg()]

    assert await detector.supports_language("en")
    assert await detector.unlikely_threshold("en") == 0.15
    assert await detector.predict_end_of_turn(Ctx()) > 0.5


# -- spelled-out identifiers ----------------------------------------------------

@pytest.mark.parametrize("heard,meant", [
    ("Q-7-7", "Q77"),
    ("Z, Y, 4, 1", "ZY41"),
    ("K L 9", "KL9"),
    ("MN-4-02", "MN402"),
])
def test_spelled_identifiers_are_joined(heard, meant):
    assert canonical_spoken_id(heard) == meant


@pytest.mark.parametrize("value", ["ORD-12345", "SAVE-2026", "QX77", "gold", "New York"])
def test_real_identifiers_and_words_are_left_alone(value):
    assert canonical_spoken_id(value) == value


def test_only_identifier_arguments_are_cleaned():
    got = clean_args({"order_id": "B-4-4", "query": "a b c", "quantity": 2, "doc_number": "X 9"})
    assert got == {"order_id": "B44", "query": "a b c", "quantity": 2, "doc_number": "X9"}
