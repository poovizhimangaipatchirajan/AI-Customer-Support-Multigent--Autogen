"""Offline return-window completeness regressions; no model/API/database."""
import pytest
from backend.services.grounding_guard import check_response_grounding

QUESTION = "What is the return window for fashion products, and how do I initiate a return?"
PASSAGE = {
    "id": "approved-return-window",
    "source": "02_Returns_and_Refunds_SOP.pdf",
    "visibility": "customer",
    "text": (
        "Fashion & Apparel 14 Days Unworn, unwashed, brand tags intact. "
        "Consumer Electronics 7 Days Original box and accessories. "
        "Large Home Appliances 10 Days Uninstalled and original packaging. "
        "Returns can be initiated from the orders dashboard."
    ),
}


def check(answer, question=QUESTION, passages=None):
    return check_response_grounding(
        answer, [PASSAGE] if passages is None else passages,
        customer_message=question,
    )


def test_original_live_failure_now_rejected():
    assert check("To initiate a return, use the orders dashboard.") == "MISSING_RETURN_WINDOW_ANSWER"


def test_correct_fashion_window_and_procedure_accepted():
    assert check("Fashion returns are allowed within 14 days. Initiate a return from the orders dashboard.") is None


def test_wrong_fashion_window_rejected():
    assert check("For Fashion & Apparel, returns are accepted within 30 days.") is not None


def test_no_relevant_policy_requires_review():
    unrelated = [{"id": "x", "source": "05_Payments_and_Billing_SOP.pdf", "text": "Payments are reviewed."}]
    assert check("You can request a return from the orders dashboard.", passages=unrelated) == "MISSING_RETURN_WINDOW_ANSWER"


def test_initiation_only_does_not_require_window():
    assert check("Initiate a return from the orders dashboard.", "How do I initiate a return?") is None


@pytest.mark.parametrize("question,answer", [
    ("What is the electronics return window?", "Consumer electronics returns are accepted within 7 days."),
    ("What is the large appliances return window?", "Large home appliances returns are accepted within 10 days."),
])
def test_other_category_windows_derived_from_policy(question, answer):
    assert check(answer, question) is None


def test_number_from_unrelated_policy_is_not_sufficient():
    assert check("Fashion returns are processed in 14 days.", passages=[{
        "id": "x", "source": "05_Payments_and_Billing_SOP.pdf", "text": "14 days",
    }]) == "MISSING_RETURN_WINDOW_ANSWER"


def test_existing_refund_action_guard_unchanged():
    assert check("We have already issued your refund.", "Where is my refund?") == "UNVERIFIED_COMPLETED_ACTION"


def test_no_customer_question_preserves_old_callers():
    assert check_response_grounding("Initiate a return from the orders dashboard.", [PASSAGE]) is None
