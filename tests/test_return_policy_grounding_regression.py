"""Focused regression tests for return-policy eligibility timing."""
from backend.services.grounding_guard import check_response_grounding

PASSAGES = [{
    "id": "approved-return-policy",
    "source": "02_Returns_and_Refunds_SOP.pdf",
    "text": (
        "Fashion & Apparel 14 Days Unworn, unwashed, brand tags intact. "
        "Consumer Electronics 7 Days Original box and accessories. "
        "Large Home Appliances 10 Days Uninstalled and original packaging."
    ),
}]


def test_supported_electronics_return_window_not_delivery_promise():
    draft = "Consumer electronics must be returned within 7 days in their original box."
    assert check_response_grounding(draft, PASSAGES) is None


def test_wrong_category_return_window_rejected():
    draft = "Consumer electronics must be returned within 14 days."
    assert check_response_grounding(draft, PASSAGES) == "UNSUPPORTED_DELIVERY_TIME"


def test_unsupported_delivery_promise_still_rejected():
    draft = "We will deliver your order within 7 days."
    assert check_response_grounding(draft, PASSAGES) == "UNSUPPORTED_DELIVERY_TIME"


def test_unverified_refund_action_still_rejected():
    draft = "We have already issued your refund."
    assert check_response_grounding(draft, PASSAGES) == "UNVERIFIED_COMPLETED_ACTION"
