from backend.services.grounding_guard import check_response_grounding


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
