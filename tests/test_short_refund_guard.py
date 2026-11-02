from backend.services.risk import screen, refund_amount_from_text
import pytest

@pytest.mark.parametrize("text", ["refund 1300", "refund ₹1300", "refund Rs. 1300", "refund INR 1300", "refund 500"])
def test_short_refund_escalates(text):
    assert "REFUND_GE_500" in screen(text)
    assert refund_amount_from_text(text) >= 500

def test_below_threshold():
    assert "REFUND_GE_500" not in screen("refund 499")

def test_policy_question_not_a_claim():
    assert "REFUND_GE_500" not in screen("What is the refund policy?")

def test_order_id_not_amount():
    assert refund_amount_from_text("refund for order 1300") is None

def test_explicit_amount_conflict_escalates():
    assert "REFUND_AMOUNT_UNVERIFIED" in screen("refund 1300", 1200)
