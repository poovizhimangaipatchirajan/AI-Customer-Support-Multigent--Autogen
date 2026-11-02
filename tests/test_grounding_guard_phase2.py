"""Offline regression tests; no OpenAI calls, database or RAG index changes."""
from backend.services.grounding_guard import check_response_grounding


def policy(text):
    return [{'id': 'policy-1', 'visibility': 'customer', 'text': text}]


def test_refund_timeline_supported_with_business_working_synonym():
    assert check_response_grounding(
        'Refund processing takes 5 business days.',
        policy('Refund processing takes 5 working days.'),
    ) is None


def test_refund_timeline_not_supported_by_shipping_passage():
    assert check_response_grounding(
        'Refund processing takes 5 business days.',
        policy('Delivery takes 5 business days.'),
    ) == 'UNSUPPORTED_DELIVERY_TIME'


def test_unsupported_refund_timeline_rejected():
    assert check_response_grounding(
        'Refund processing takes 3 business days.',
        policy('Refund processing takes 5 business days.'),
    ) == 'UNSUPPORTED_DELIVERY_TIME'


def test_no_timeline_explanation_is_safe():
    assert check_response_grounding(
        'You can request a return through the orders dashboard, subject to eligibility.',
        policy('Customers can request returns from the orders dashboard.'),
    ) is None


def test_unsupported_replacement_timeline_rejected():
    assert check_response_grounding(
        'Your replacement arrives in 2 days.',
        policy('Replacement dispatch follows verification.'),
    ) == 'UNSUPPORTED_DELIVERY_TIME'


def test_completed_action_not_hidden_by_unrelated_negation():
    assert check_response_grounding(
        'We cannot confirm the ETA, but we have issued your refund.',
        policy('Refund requests can be submitted in the app.'),
    ) == 'UNVERIFIED_COMPLETED_ACTION'


def test_unverified_completed_action_rejected():
    assert check_response_grounding(
        'We have approved your replacement.',
        policy('Replacement requests require review.'),
    ) == 'UNVERIFIED_COMPLETED_ACTION'


def test_negated_eta_not_treated_as_promise():
    assert check_response_grounding(
        'We cannot promise delivery in 3 days.',
        policy('Delivery estimates depend on order status.'),
    ) is None
