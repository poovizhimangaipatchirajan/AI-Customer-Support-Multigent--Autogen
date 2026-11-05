"""Consolidated risk screening tests.

Merged without behaviour changes from:
- test_risk.py: regression tests
- test_risk_extended.py: regression tests
- test_final_hardening.py: Offline deterministic regression for alternative customer risk wording.
- test_phase8_multilingual_risk.py: regression tests
- test_refund_amount_attribution.py: Currency-marked refund amounts next to refund wording drive the INR 500 rule.
- test_refund_amount_patch_smoke.py: Focused refund-amount and quantitative-guard smoke checks (loaded by file path).
- test_short_refund_guard.py: regression tests
- test_tc02_risk_intent.py: regression tests
- test_safety_patch.py: regression tests
- test_customer_safety_classification_patch.py: regression tests
- test_live_scenario_fixes.py: Offline regressions for issues found in the live six-scenario review.
"""
from backend.services.risk import screen
import pytest
import importlib.util
from pathlib import Path
from backend.services.risk import refund_amount_from_text
from backend.services.ticket_classification import classify_ticket
from backend.services.grounding_guard import check_customer_visibility


# ======================================================================
# From test_risk.py
# ======================================================================

def test_refund_boundary():
    assert 'REFUND_GE_500' not in screen('I want a refund',499)
    assert 'REFUND_GE_500' in screen('I want a refund',500)
def test_fraud():
    assert 'FRAUD' in screen('This is fraud',50)
def test_general_policy():
    assert not screen('What is the refund policy for ₹500?')
    # An explicit claimed amount >= INR 500 is fail-closed even with policy wording.
    assert 'REFUND_GE_500' in screen('What is the refund policy for ₹500?', 500)


# ======================================================================
# From test_risk_extended.py
# ======================================================================

def test_boundary():
 assert 'REFUND_GE_500' not in screen('I want a refund',499)
 assert 'REFUND_GE_500' in screen('I want a refund',500)
 assert 'REFUND_GE_500' in screen('I want a refund',501)
def test_fraud_always_review():assert 'FRAUD' in screen('This is fraud, refund me',50)
def test_currency_ambiguous():assert 'REFUND_CURRENCY_UNCERTAIN' in screen('Please refund $20',20)
def test_general_policy__risk_extended():
 assert not screen('What is the general refund policy?')
 # A supplied claimed amount >= INR 500 always requires review (fail closed).
 assert 'REFUND_GE_500' in screen('What is the general refund policy?',10000)


# ======================================================================
# From test_final_hardening.py
# ======================================================================

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
    assert 'REFUND_GE_500' not in screen(wording)
    # A supplied claimed amount >= INR 500 is never waived by policy wording.
    assert 'REFUND_GE_500' in screen(wording, 500)

def test_refund_amount_unknown_escalates():
    assert 'REFUND_AMOUNT_UNVERIFIED' in screen('I need a refund')

def test_payment_dispute_escalates_below_threshold():
    assert 'PAYMENT_DISPUTE' in screen('My payment was charged twice', 10)


# ======================================================================
# From test_phase8_multilingual_risk.py
# ======================================================================

@pytest.mark.parametrize('message,amount,expected', [
    ('Refund me for my damaged item',499,False),
    ('Refund me for my damaged item',500,True),
    ('Refund me for my damaged item',5000,True),
    ('I want my ₹750 back',None,True),
    ('I need my 800 rupees back',None,True),
    ('Enakku 750 rupees refund venum',None,True),
    ('எனக்கு 750 ரூபாய் திருப்பி வேண்டும்',None,True),
    ('Refund me ₹750',499,False), # mismatch must still escalate, but not as confirmed >=500
])
def test_refund_cases(message,amount,expected):
    flags=screen(message,amount)
    if 'Refund me ₹750' in message:
        assert 'REFUND_AMOUNT_UNVERIFIED' in flags
    else:
        assert ('REFUND_GE_500' in flags) == expected

def test_unknown_amount_escalates():
    assert 'REFUND_AMOUNT_UNVERIFIED' in screen('Please refund my order')

def test_foreign_currency_escalates():
    assert 'REFUND_CURRENCY_UNCERTAIN' in screen('Refund me $20',20)

@pytest.mark.parametrize('message', [
    'My card was debited twice', 'I was charged two times',
    'என் பணம் இரண்டு முறை பிடித்தார்கள்', 'rendu thadava debit aachu',
])
def test_payment_disputes(message):
    assert 'PAYMENT_DISPUTE' in screen(message)

def test_fraud_and_legal():
    assert 'FRAUD' in screen('This is a scam')
    assert 'LEGAL' in screen('I will take legal action')

def test_general_policy_question():
    assert screen('What is the refund policy?') == []

def test_large_or_invalid_amounts():
    assert 'REFUND_AMOUNT_UNVERIFIED' in screen('Refund me', float('nan'))
    assert 'REFUND_GE_500' in screen('Refund me', 10**20)


# ======================================================================
# From test_refund_amount_attribution.py
# ======================================================================

@pytest.mark.parametrize('text', [
    'Please refund me INR 500', 'I want my ₹750 back', 'I need my 800 rupees back',
    'Enakku 750 rupees refund venum', 'refund me ₹500 for order 12345',
    'எனக்கு 750 ரூபாய் திருப்பி வேண்டும்',
])
def test_amount_at_or_above_500_is_reviewed(text):
    assert 'REFUND_GE_500' in screen(text)


@pytest.mark.parametrize('text', ['refund me INR 499', 'I want my ₹499 back', 'Refund me ₹100'])
def test_amount_below_500_is_not_flagged(text):
    assert screen(text) == []


def test_order_total_is_not_the_refund_amount_and_fails_closed():
    assert screen('I want a refund, my order of ₹900 was damaged') == ['REFUND_AMOUNT_UNVERIFIED']


def test_order_total_does_not_override_stated_refund_amount():
    assert screen('My order of ₹900 arrived late, refund me ₹200') == []


def test_policy_question_is_not_a_refund_case():
    assert screen('What is the refund policy?') == []


# ======================================================================
# From test_refund_amount_patch_smoke.py
# ======================================================================

SERVICES = Path(__file__).resolve().parents[1] / 'backend' / 'services'


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SERVICES / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SMOKE_RISK = _load('risk')
SMOKE_QUANT = _load('quantitative_grounding')


@pytest.mark.parametrize('msg,amount,expected', [
    ('I received a damaged product and want a refund of ₹300. Order details: Wireless Headphones Qty 1 ₹1,499.00 each', 300, []),
    ('I want a refund of ₹499', 499, []),
    ('I want a refund of ₹500', 500, ['REFUND_GE_500']),
    ('I want a refund of ₹1200', 1200, ['REFUND_GE_500']),
    ('I want a refund', None, ['REFUND_AMOUNT_UNVERIFIED']),
    ('I want a refund of ₹300 and refund of ₹700', 300, ['REFUND_AMOUNT_UNVERIFIED']),
    ('I want a refund of ₹300 and will sue', 300, ['LEGAL']),
    ('My card was charged twice. I want a refund of ₹300', 300, ['PAYMENT_DISPUTE']),
])
def test_refund_amount_smoke(msg, amount, expected):
    assert set(SMOKE_RISK.screen(msg, amount)) == set(expected)


def test_customer_amount_smoke():
    check = SMOKE_QUANT.unsupported_quantitative_claim
    evidence = ['Customers can initiate a return.']
    assert check('Your requested refund amount is ₹300.', evidence, customer_message='I want a refund of ₹300') is False
    assert check('Your requested refund amount is ₹300.', evidence, customer_message='I want a refund of ₹200') is True
    assert check('Refunds of ₹300 require human review.', evidence, customer_message='I want a refund of ₹300') is True


# ======================================================================
# From test_short_refund_guard.py
# ======================================================================

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


# ======================================================================
# From test_tc02_risk_intent.py
# ======================================================================

MODULE = Path(__file__).resolve().parents[1] / 'backend' / 'services' / 'risk.py'
spec = importlib.util.spec_from_file_location('tc02_risk_under_test', MODULE)
risk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(risk)

def test_policy_information_does_not_require_refund_amount():
    text = ('I purchased headphones last week and would like to return them. '
            'Can you explain the return policy, eligibility window, and steps to request a refund?')
    assert risk.screen(text) == []
    assert risk.is_refund_information_request(text)

def test_refund_howto_is_informational():
    assert risk.screen('How do I request a refund?') == []

def test_explicit_refund_without_amount_requires_verification():
    assert 'REFUND_AMOUNT_UNVERIFIED' in risk.screen('Please refund my order now')

def test_high_value_refund_is_reviewed():
    assert 'REFUND_GE_500' in risk.screen('Please refund me INR 500')

def test_payment_dispute_always_escalates():
    assert 'PAYMENT_DISPUTE' in risk.screen('My card was charged twice. Explain the refund policy')

def test_legal_threat_always_escalates():
    assert 'LEGAL' in risk.screen('I will contact a lawyer. Explain the refund policy')

def test_mixed_guidance_and_execution_does_not_bypass_review():
    assert 'REFUND_AMOUNT_UNVERIFIED' in risk.screen('Explain the policy and please refund my order')

def test_form_amount_cannot_be_downgraded_by_policy_words():
    assert 'REFUND_GE_500' in risk.screen('Explain the refund policy', 500)

def test_short_refund_amount_is_enforced():
    assert 'REFUND_GE_500' in risk.screen('refund 900')


# ======================================================================
# From test_safety_patch.py
# ======================================================================

ROOT = Path(__file__).resolve().parents[1]
spec_safety_patch = importlib.util.spec_from_file_location('guard_patch', ROOT/'backend/services/grounding_guard.py')
guard = importlib.util.module_from_spec(spec_safety_patch)
spec_safety_patch.loader.exec_module(guard)
PASSAGES = [{'text':'Customers can request assistance with a return under the applicable policy.'}]

def test_unsupported_tracking_status():
    assert guard.check_response_grounding('Your package is secure and moving forward.', PASSAGES) == 'UNVERIFIED_ORDER_STATUS'

def test_replacement_promise():
    assert guard.check_response_grounding('We will dispatch a replacement immediately at no extra cost.', PASSAGES) == 'UNAUTHORIZED_ACTION_PROMISE'

def test_wrong_threshold():
    assert guard.check_response_grounding('Refunds of Rs. 499 require human approval.', PASSAGES) == 'MISSTATED_REFUND_THRESHOLD'

def test_neutral_policy_guidance():
    assert guard.check_response_grounding('You may request a return, subject to verification.', PASSAGES) is None


# ======================================================================
# From test_customer_safety_classification_patch.py
# ======================================================================

def test_shipping_inquiry_labels():
    assert classify_ticket('Shipping and delivery policy clarification', 'Please explain tracking and delays') == ('Shipping & delivery', 'Low', 'Neutral')

def test_risk_priority():
    assert classify_ticket('Unauthorized charge', 'I am frustrated about a payment', 'PAYMENT_DISPUTE') == ('Payments & billing', 'High', 'Frustrated')

def test_suspicious_details_blocked():
    assert check_customer_visibility('We conduct geofence audits and driver GPS verification.') == 'SENSITIVE_OPERATIONAL_DETAIL'
    assert check_customer_visibility('Tracking updates every 15 minutes.') == 'SENSITIVE_OPERATIONAL_DETAIL'
    assert check_customer_visibility('We provide delivery tracking after dispatch.') is None


# ======================================================================
# From test_live_scenario_fixes.py
# ======================================================================

@pytest.mark.parametrize("text", [
    "You charged my card three times! Fix this now.",
    "I was billed 3 times for one order",
    "My account was debited two times",
    "There are multiple charges on my statement",
    "You charged me more than once",
    "I see two charges for one order, can you check?",
    "There are duplicate transactions on my card",
])
def test_repeated_charges_are_payment_disputes_without_legal_words(text):
    assert "PAYMENT_DISPUTE" in screen(text)


@pytest.mark.parametrize("text", [
    "I ordered three times this year and love the service",
    "Where is my order? It was supposed to arrive yesterday.",
    "I made two orders last week",
    "Are there any extra charges for express delivery?",
])
def test_ordinary_repetition_is_not_a_payment_dispute(text):
    assert "PAYMENT_DISPUTE" not in screen(text)


def test_category_membership_rule_in_resolution_prompt():
    from backend.agents.team import SUPPORTED_CLAIMS_RULES
    rule = SUPPORTED_CLAIMS_RULES.casefold()
    assert "plainly belongs to that category" in rule
    assert "more than one named category" in rule
    assert "needs_human_review to true" in rule
    # Generic rule: no product names hardcoded into the prompt.
    import re
    for product in ("blenders?", "shoes?", "headphones?", "cables?"):
        assert not re.search(r"\b" + product + r"\b", rule)


@pytest.mark.parametrize("text", [
    "The product cost ₹500, and I want a full refund.",
    "I would like a complete refund for this broken lamp.",
    "Please give me a partial refund, the box was torn.",
])
def test_qualified_refund_wording_is_a_refund_request(text):
    flags = screen(text)
    assert flags, "a refund request with an unattributed amount must fail closed"
    assert set(flags) <= {"REFUND_AMOUNT_UNVERIFIED", "REFUND_GE_500"}


def test_customer_stated_price_is_not_an_unsupported_policy_claim():
    from backend.services.quantitative_grounding import unsupported_quantitative_claim
    msg = "The product cost ₹500, and I want a full refund."
    evidence = ["Submission is not approval; eligibility and order ownership must be verified."]
    draft = "Since your product cost ₹500, this refund request will require a human review."
    assert not unsupported_quantitative_claim(draft, evidence, customer_message=msg)
    # Wording seen in live runs.
    for variant in ("Since your product cost is ₹500, your refund request will require human review.",
                    "Since your refund amount is ₹500, this case requires human review.",
                    "Your product's price was ₹500, so a human review is required."):
        assert not unsupported_quantitative_claim(variant, evidence, customer_message=msg), variant
    # A value the customer never stated, or a new policy figure, is still rejected.
    assert unsupported_quantitative_claim(draft.replace("500", "800"), evidence, customer_message=msg)
    assert unsupported_quantitative_claim("Refunds carry a ₹50 processing fee.", evidence, customer_message=msg)
