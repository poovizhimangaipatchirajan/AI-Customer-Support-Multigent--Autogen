"""Offline deterministic regression for alternative customer risk wording."""
import pytest
from backend.services.risk import screen

@pytest.mark.parametrize('wording', [
    'I need a refund',
    'Need a refund for this order',
    'I would like a refund',
    'I require a refund',
    'I am requesting a refund',
    'Please refund my order',
])
def test_refund_alternative_wording_at_threshold(wording):
    assert 'REFUND_GE_500' in screen(wording, 500)
    assert 'REFUND_GE_500' not in screen(wording, 499)

@pytest.mark.parametrize('wording', [
    'This was a fraudulent payment',
    'I suspect fraud',
    'I was scammed',
    'This looks like a scam',
])
def test_fraud_variants_always_escalate(wording):
    assert 'FRAUD' in screen(wording, 1)

@pytest.mark.parametrize('wording', [
    'What is the refund policy for ₹500?',
    'Explain refund policy',
])
def test_general_policy_not_customer_refund_request(wording):
    assert 'REFUND_GE_500' not in screen(wording, 500)

def test_refund_amount_unknown_escalates():
    assert 'REFUND_AMOUNT_UNVERIFIED' in screen('I need a refund')

def test_payment_dispute_escalates_below_threshold():
    assert 'PAYMENT_DISPUTE' in screen('My payment was charged twice', 10)
