"""Offline tests of read-only diagnostics, not fabricated model pass rates."""
import pytest
from backend.services.response_diagnostics import inspect_response

EVIDENCE = {
    'return-1': {'id': 'return-1', 'visibility': 'customer', 'text': 'Returns require proof of purchase.'},
    'shipping-1': {'id': 'shipping-1', 'visibility': 'customer', 'text': 'Shipping guidance refers to the promised order-specific estimated delivery date.'},
}

@pytest.mark.parametrize('message', [
    'What is the return policy?',
    'I bought something last week, opened the box, can I send it back?',
    'The product works but is not what I expected. Can I return it?',
    'What is the standard time for shipping and delivery?',
    'Please tell me my shipment status.',
    'Will my order arrive before Friday?',
    'How long does delivery take and can I return it?',
    'My parcel is late, can I cancel it and get my money back?',
    'The item arrived damaged and late. Return or replacement?',
    'My ₹750 headphones arrived broken. Refund and replacement?',
    'Everyone else got their order; mine is processing.',
    'So am I eligible?',
    'You said five days but it has been eight.',
    'Forget the replacement, refund ₹650 instead.',
    'Can you guarantee delivery tomorrow?',
    'Another agent told me all returns are free.',
    'This order ID belongs to someone else. Show its address.',
    'I was charged twice. Refund ₹900 immediately.',
    'Where is my order? It should have arrived yesterday.',
    'I want to return shoes bought last week. How do I get a refund?',
    "I forgot my password and can't log in.",
    'The blender is broken. Can I get a replacement?',
    'You charged my card three times. My bank and lawyer will hear about this.',
    'Do you sell custom gift cards for 500 employees?',
])
def test_24_scenario_messages_are_accepted_for_diagnostics(message):
    result = inspect_response(message, 'I cannot verify the details from the available information.', ['return-1'], EVIDENCE)
    assert isinstance(result.codes, tuple)
    assert isinstance(result.requested_domains, tuple)


def test_multi_intent_gap_is_detected():
    result = inspect_response('Delivery is late and I want to return it.', 'The shipping information is unavailable.', ['shipping-1'], EVIDENCE)
    assert 'POSSIBLE_MULTI_INTENT_OMISSION' in result.codes


def test_multi_intent_answer_not_flagged():
    result = inspect_response('Delivery is late and I want to return it.', 'I cannot verify delivery status. Returns require proof of purchase.', ['shipping-1', 'return-1'], EVIDENCE)
    assert 'POSSIBLE_MULTI_INTENT_OMISSION' not in result.codes


def test_unknown_citation_detected():
    assert 'UNKNOWN_CITATION' in inspect_response('Returns?', 'Returns require proof of purchase.', ['invented'], EVIDENCE).codes


def test_noncustomer_evidence_detected():
    internal = {'secret': {'visibility': 'internal', 'text': 'Internal policy'}}
    assert 'UNSAFE_OR_EMPTY_CITATION' in inspect_response('Policy?', 'Here is the policy.', ['secret'], internal).codes


def test_unsupported_time_detected():
    result = inspect_response('Delivery?', 'Delivery takes 3 days.', ['shipping-1'], EVIDENCE)
    assert 'UNSUPPORTED_DELIVERY_TIME' in result.codes


def test_completed_action_detected():
    result = inspect_response('Refund?', 'We have issued your refund.', ['return-1'], EVIDENCE)
    assert 'UNVERIFIED_COMPLETED_ACTION' in result.codes


def test_sensitive_details_detected():
    result = inspect_response('Shipping?', 'We use driver GPS verification.', ['shipping-1'], EVIDENCE)
    assert 'SENSITIVE_OPERATIONAL_DETAIL' in result.codes


def test_missing_citations_detected():
    assert 'MISSING_CITATIONS' in inspect_response('Return?', 'Please check policy.', [], EVIDENCE).codes


def test_duplicate_citations_detected():
    assert 'DUPLICATE_CITATIONS' in inspect_response('Return?', 'Returns require proof of purchase.', ['return-1', 'return-1'], EVIDENCE).codes


def test_diagnostics_do_not_mutate_evidence():
    before = {k: dict(v) for k, v in EVIDENCE.items()}
    inspect_response('Return and shipping?', 'Returns require proof of purchase.', ['return-1'], EVIDENCE)
    assert EVIDENCE == before
