
from backend.services.quantitative_grounding import (
    unsupported_quantitative_claim,
)

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
