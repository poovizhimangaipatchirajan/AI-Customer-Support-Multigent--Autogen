"""Phase 2D.6: distinguish return eligibility from delivery promises."""
import pytest
from backend.services.grounding_guard import check_response_grounding


EVIDENCE = [{
    "id": "electronics-return-policy",
    "source": "02_Returns_and_Refunds_SOP.pdf",
    "page": 1,
    "visibility": "customer",
    "text": (
        "Consumer Electronics 7 Days Original box, serial numbers matching "
        "invoice, all accessories. Technical replacement or refund post-technician diagnostic."
    ),
}]


@pytest.mark.parametrize("draft,expected", [
    (
        "For consumer electronics like headphones, our policy allows returns "
        "within 7 days of delivery, provided the original box and accessories are intact.",
        None,
    ),
    (
        "For consumer electronics, our policy allows returns within 14 days of delivery.",
        "UNSUPPORTED_DELIVERY_TIME",
    ),
    (
        "Your headphones will be delivered within 7 days.",
        "UNSUPPORTED_DELIVERY_TIME",
    ),
    (
        "Returns are allowed within 7 days of delivery for consumer electronics.",
        None,
    ),
    (
        "Your replacement will arrive within 7 days.",
        "UNSUPPORTED_DELIVERY_TIME",
    ),
])
def test_return_window_not_delivery_promise(draft, expected):
    assert check_response_grounding(draft, EVIDENCE) == expected
