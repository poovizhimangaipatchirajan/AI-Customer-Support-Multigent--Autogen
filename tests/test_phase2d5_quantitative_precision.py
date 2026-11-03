from backend.services.quantitative_grounding import unsupported_quantitative_claim


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
