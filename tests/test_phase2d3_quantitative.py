from backend.services.quantitative_grounding import unsupported_quantitative_claim


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
