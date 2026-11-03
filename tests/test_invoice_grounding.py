
"""Focused regression checks for invoice citation relevance."""

from backend.services.grounding_guard import check_response_grounding


UNRELATED_PAYMENT = [{
    "id": "42362ee603686761",
    "source": "05_Payments_and_Billing_SOP.pdf",
    "text": (
        "A pending card authorization is not necessarily a completed charge. "
        "If you see a possible duplicate payment, report both transaction "
        "statuses for verification; no bank hold release is guaranteed."
    ),
}]


def test_actual_invoice_failure_is_rejected():
    draft = (
        "To get a bill for your old order, please provide the order number "
        "or purchase date so we can assist you further. You can also check "
        "your orders dashboard on NovaCart, where past bills or invoices "
        "might be available for download or viewing."
    )
    assert check_response_grounding(draft, UNRELATED_PAYMENT) == (
        "UNSUPPORTED_INVOICE_GUIDANCE"
    )


def test_supported_invoice_retrieval_is_not_rejected():
    draft = "You can download your invoice from your orders dashboard."
    evidence = [{
        "text": "Customers can download invoices from the orders dashboard."
    }]
    assert check_response_grounding(draft, evidence) is None


def test_order_details_request_is_not_feature_claim():
    draft = "Please provide your order number for billing verification."
    assert check_response_grounding(draft, UNRELATED_PAYMENT) is None


def test_existing_return_policy_answer_is_not_blocked():
    draft = (
        "Fashion and apparel have a 14-day return window for unworn items "
        "with tags intact."
    )
    evidence = [{
        "text": (
            "Fashion and apparel have a 14-day return window if unworn "
            "and unwashed, with brand tags intact and original packaging."
        )
    }]
    assert check_response_grounding(draft, evidence) is None
