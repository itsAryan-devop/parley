"""Stale-value check (parley/fdb/resolver.py). Phrasings are our own."""

import pytest

from parley.fdb.resolver import StaleValueCheck, stale_value


@pytest.mark.parametrize("args,text,correction", [
    ({"destination": "Lisbon"}, "Flights to Lisbon, no wait, make it Porto.", "Porto"),
    ({"bill_type": "water", "source_account": "credit card"},
     "Pay my water bill from credit card, sorry, I mean from savings.", "from savings"),
    ({"order_id": "QX77"}, "Track Q X 7 7, scratch that, it's ZZ90.", "it's ZZ90"),
])
def test_call_that_ignores_the_correction_is_stale(args, text, correction):
    assert stale_value(args, text) == correction


@pytest.mark.parametrize("args,text", [
    ({"destination": "Porto"}, "Flights to Lisbon, no wait, make it Porto."),       # the repair
    ({"destination": "Lisbon"}, "Flights to Lisbon on Friday please."),            # no marker
    ({"destination": "Lisbon"}, "Lisbon, no, sorry, Lisbon is right."),            # restated
    ({"order_id": "AB1"}, "I have not received order AB1, can you check?"),        # 'not' ignored
    ({"destination": "Lisbon"}, "Flights to Lisbon, no wait"),                     # nothing after yet
    ({"bill_type": "water", "source_account": "savings"},
     "Pay my water bill from credit card, sorry, I mean from savings."),          # unchanged slot
    ({"quantity": 2, "product_id": "K9"}, "Two of K9, no rush."),                  # value after marker
])
def test_current_or_unmarked_values_pass(args, text):
    assert stale_value(args, text) is None


def test_refuses_once_then_lets_the_llm_insist():
    chk = StaleValueCheck()
    chk.user_said("Book it under Dana Reyes, no wait, under Sam Ortiz.")
    args = {"passenger_name": "Dana Reyes"}
    assert chk.check("book_flight", args, "k1")["status"] == "not_executed"
    assert chk.check("book_flight", args, "k1") is None      # second attempt goes through
    chk.agent_replied()
    assert chk.turn_text == ""
