from backend.services.quantitative_grounding import unsupported_quantitative_claim

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
