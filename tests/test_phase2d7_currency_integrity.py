"""Phase 2D.7 malformed-currency detector regression tests."""
from backend.agents.team import has_malformed_money


def test_tab_prefixed_refund_amount_rejected():
    assert has_malformed_money("Since your refund request is for \t750, it requires review.")

def test_tab_prefixed_threshold_rejected():
    assert has_malformed_money("The amount is INR \t500 and requires review.")

def test_valid_ascii_inr_accepted():
    assert not has_malformed_money("Your INR 750 refund request requires human review at INR 500.")

def test_valid_rupee_accepted():
    assert not has_malformed_money("Your refund request is ₹750; human review starts at ₹500.")

def test_unrelated_tab_accepted():
    assert not has_malformed_money("Reference:\t750 units were counted.")
