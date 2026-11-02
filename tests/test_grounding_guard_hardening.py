"""Offline tests for conservative grounding checks; no API keys required."""
from backend.services.grounding_guard import check_response_grounding

PASSAGES = [{'text': 'Orders have preparation and in-transit statuses; specific delivery ETA is unavailable.'}]


def test_ordinary_shipping_guidance_not_blocked():
    assert check_response_grounding('We cannot confirm an order-specific ETA.', PASSAGES) is None


def test_unsupported_numeric_delivery_promise_blocked():
    assert check_response_grounding('Your order will arrive in 2 days.', PASSAGES) == 'UNSUPPORTED_DELIVERY_TIME'


def test_completed_refund_claim_blocked():
    assert check_response_grounding('We have processed your refund.', PASSAGES) == 'UNVERIFIED_COMPLETED_ACTION'


def test_negative_refund_statement_allowed():
    assert check_response_grounding('We have not processed your refund.', PASSAGES) is None


def test_empty_draft_blocked():
    assert check_response_grounding('  ', PASSAGES) == 'EMPTY_DRAFT'
