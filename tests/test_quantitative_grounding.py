"""Consolidated quantitative grounding tests.

Merged without behaviour changes from:
- test_quantitative_grounding.py: regression tests
- test_phase2d3_quantitative.py: regression tests
- test_phase2d5_quantitative_precision.py: regression tests
- test_phase2d7_currency_integrity.py: Phase 2D.7 malformed-currency detector regression tests.
- test_phase2d8_quantitative_attribution.py: regression tests
- test_phase2d8c_comparison.py: regression tests
"""
from backend.services.grounding_guard import check_response_grounding
from backend.services.quantitative_grounding import unsupported_quantitative_claim
from backend.agents.team import has_malformed_money


# ======================================================================
# From test_quantitative_grounding.py
# ======================================================================

def evidence(text):
    return [{'id': 'p1', 'visibility': 'customer', 'text': text}]


def test_policy_fee_grounded():
    assert check_response_grounding('The return fee is ₹50.', evidence('The return fee is INR 50.')) is None


def test_policy_fee_unsupported():
    assert check_response_grounding('The return fee is ₹75.', evidence('The return fee is INR 50.')) == 'UNSUPPORTED_QUANTITATIVE_POLICY_CLAIM'


def test_discount_not_borrowed_from_unrelated_fee():
    assert check_response_grounding('The discount is 10%.', evidence('The shipping fee is 10%.')) == 'UNSUPPORTED_QUANTITATIVE_POLICY_CLAIM'


def test_policy_percent_grounded():
    assert check_response_grounding('The discount is 10%.', evidence('The discount is 10%.')) is None


def test_no_claim_remains_unchanged():
    assert check_response_grounding('Return eligibility depends on the policy.', evidence('Return eligibility depends on the policy.')) is None


# ======================================================================
# From test_phase2d3_quantitative.py
# ======================================================================

def test_customer_value_and_exact_review_threshold_are_not_sop_claims():
    draft = ("Since your order amount is ₹750, any refund request of ₹500 or more "
             "will require human review before processing.")
    assert not unsupported_quantitative_claim(draft, ["Consumer Electronics 7 Days"])


def test_other_thresholds_remain_unsupported():
    assert unsupported_quantitative_claim(
        "Any refund request of ₹600 or more will require human review.", ["Returns policy"]
    )


def test_unsubstantiated_fees_remain_unsupported():
    assert unsupported_quantitative_claim(
        "A return fee of ₹750 applies.", ["Returns policy"]
    )


def test_other_quantitative_claims_not_whitelisted():
    assert unsupported_quantitative_claim(
        "Shipping has a 25% surcharge.", ["Shipping policy"]
    )


# ======================================================================
# From test_phase2d5_quantitative_precision.py
# ======================================================================

def test_customer_attributed_refund_amount_and_review_not_policy_claim():
    draft = ('Since your refund request is ₹750, it will require human review '
             'before any refund or replacement can be confirmed.')
    assert not unsupported_quantitative_claim(
        draft, ['Consumer Electronics 7 Days'],
        customer_message='I ordered headphones for ₹750 and want a refund.'
    )


def test_unsupported_fee_still_rejected():
    assert unsupported_quantitative_claim('The return fee is ₹750.', ['The return fee is INR 50.'])


def test_unsupported_discount_still_rejected():
    assert unsupported_quantitative_claim('The discount is 10%.', ['The shipping fee is 10%.'])


def test_wrong_review_threshold_still_rejected():
    assert unsupported_quantitative_claim('Refund requests of ₹600 or more will require human review.', ['Returns policy'])


def test_unattributed_refund_amount_not_exempt():
    assert unsupported_quantitative_claim('The refund amount is ₹750.', ['Returns policy'])


def test_customer_refund_request_without_original_message_fails_closed():
    draft = 'Since your refund request is INR 750, it requires human review.'
    assert unsupported_quantitative_claim(draft, ['Consumer Electronics 7 Days'])


def test_customer_refund_request_with_mismatched_amount_fails_closed():
    draft = 'Since your refund request is INR 950, it requires human review.'
    assert unsupported_quantitative_claim(
        draft, ['Consumer Electronics 7 Days'],
        customer_message='I ordered headphones for INR 750 and want a refund.'
    )


# ======================================================================
# From test_phase2d7_currency_integrity.py
# ======================================================================

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


# ======================================================================
# From test_phase2d8_quantitative_attribution.py
# ======================================================================

EVIDENCE = ['Consumer Electronics 7 Days. Refund after technician diagnostic.']


def check(draft, customer='I ordered headphones for ₹750 and want a refund.'):
    return unsupported_quantitative_claim(draft, EVIDENCE, customer_message=customer)


def test_matching_refund_amount_is_case_fact():
    assert not check('Since your refund amount is INR 750, human review is required.')


def test_nonmatching_amount_is_not_exempt():
    assert check('Since your refund amount is INR 950, human review is required.')


def test_missing_customer_context_fails_closed():
    assert unsupported_quantitative_claim('Your refund amount is INR 750, requiring review.', EVIDENCE)


def test_unsupported_policy_fee_still_flagged():
    assert check('A refund fee of INR 750 applies.')


def test_unsupported_policy_discount_still_flagged():
    assert check('Your refund discount is INR 750.')


def test_wrong_threshold_still_flagged():
    assert check('Refund requests of INR 750 require human review.')


def test_correct_threshold_rule_remains_exempt():
    assert not check('Refund requests of INR 500 require human review.')


def test_matching_amount_does_not_hide_second_policy_claim():
    assert check('Your refund amount is INR 750, and the refund fee is INR 200.')


def test_percentage_still_checked():
    assert check('A refund charge of 15% applies.')


# ======================================================================
# From test_phase2d8c_comparison.py
# ======================================================================

CUSTOMER = "I ordered headphones for ₹750, but they arrived broken."

DRAFT = (
    "Since your refund request is for INR 750, "
    "which is above INR 500, it will require human review "
    "before any refund can be processed."
)


def test_s10_comparison_is_supported():
    assert not unsupported_quantitative_claim(DRAFT, [], CUSTOMER)


def test_without_customer_context_is_flagged():
    assert unsupported_quantitative_claim(DRAFT, [], None)


def test_wrong_customer_amount_is_flagged():
    assert unsupported_quantitative_claim(
        DRAFT, [], "I paid ₹300"
    )


def test_below_threshold_comparison_is_flagged():
    draft = DRAFT.replace("INR 750", "INR 300")
    assert unsupported_quantitative_claim(
        draft, [], "I paid ₹300"
    )


def test_wrong_threshold_is_flagged():
    draft = DRAFT.replace("INR 500", "INR 600")
    assert unsupported_quantitative_claim(
        draft, [], CUSTOMER
    )


def test_unrelated_refund_fee_is_flagged():
    draft = DRAFT + " A refund fee of INR 99 applies."
    assert unsupported_quantitative_claim(
        draft, [], CUSTOMER
    )


def test_customer_amount_without_comparison():
    assert not unsupported_quantitative_claim(
        "Your refund request is for INR 750.",
        [],
        CUSTOMER,
    )
