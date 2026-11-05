"""Consolidated grounding return windows tests.

Merged without behaviour changes from:
- test_return_window_anchor_fix.py: regression tests
- test_return_window_anchor_variants.py: Unsupported return-window start points are rejected in every common phrasing.
- test_return_window_completeness.py: Offline return-window completeness regressions; no model/API/database.
- test_return_policy_grounding_regression.py: Focused regression tests for return-policy eligibility timing.
- test_category_window_guard.py: Category return windows are accepted only against the SAME category's passage.
- test_doa_timeline_grounding.py: Targeted regression for approved DOA assessment timing vs invented ETAs.
- test_tc02_return_window_publication.py: TC02 regression: approved return eligibility is not a delivery promise.
- test_answer_repair.py: Offline safety tests; no API, model or Docker required.
- test_answer_repair_return_window_safety.py: Regression coverage for conservative repair of return-window sentences.
- test_phase2d6_return_window.py: Phase 2D.6: distinguish return eligibility from delivery promises.
"""
from backend.services.grounding_guard import check_response_grounding, repair_unsupported_return_anchor
from backend.services.grounding_guard import check_response_grounding
import pytest
import importlib.util
from pathlib import Path
from backend.services.answer_repair import repair_isolated_time_claim
from backend.services import answer_repair


# ======================================================================
# From test_return_window_anchor_fix.py
# ======================================================================

POLICY = [{'id': 'approved', 'visibility': 'customer', 'text': 'Consumer Electronics 7 Days Original box and accessories. Technician diagnosis required for defective items.'}]


def test_exact_customer_draft_repaired_without_dropping_guidance():
    draft = ('You can return the headphones within 7 days of purchase since they are consumer electronics. '
             'Please keep the original box, all accessories, and ensure the serial numbers match your invoice.')
    assert check_response_grounding(draft, POLICY) == 'UNSUPPORTED_RETURN_WINDOW_ANCHOR'
    candidate = repair_unsupported_return_anchor(draft, POLICY)
    assert 'within 7 days since' in candidate
    assert 'original box' in candidate
    assert 'serial numbers' in candidate
    assert check_response_grounding(candidate, POLICY) is None


def test_supported_explicit_purchase_anchor_retained():
    passages = [{'text': 'Consumer Electronics returns are allowed within 7 days of purchase.'}]
    draft = 'You can return the headphones within 7 days of purchase since they are consumer electronics.'
    assert check_response_grounding(draft, passages) is None
    assert repair_unsupported_return_anchor(draft, passages) == draft


def test_wrong_category_window_never_repaired():
    draft = 'You can return consumer electronics within 14 days of purchase.'
    assert repair_unsupported_return_anchor(draft, POLICY) == draft
    assert check_response_grounding(draft, POLICY) is not None


def test_delivery_promise_still_rejected():
    draft = 'We will deliver your order within 7 days.'
    assert check_response_grounding(draft, POLICY) == 'UNSUPPORTED_DELIVERY_TIME'
    assert repair_unsupported_return_anchor(draft, POLICY) == draft


def test_refund_execution_still_rejected():
    assert check_response_grounding('We have already issued your refund.', POLICY) == 'UNVERIFIED_COMPLETED_ACTION'


def test_missing_category_evidence_not_repaired():
    draft = 'You can return headphones within 7 days of purchase.'
    assert repair_unsupported_return_anchor(draft, [{'text': 'Fashion & Apparel 7 Days.'}]) == draft


# ======================================================================
# From test_return_window_anchor_variants.py
# ======================================================================

NO_ANCHOR = [{'id': 'p1', 'visibility': 'customer', 'text': (
    'Consumer electronics have a 7-day return window. The original box, serial '
    'numbers matching the invoice, and all accessories are required.')}]
OF_DELIVERY = [{'id': 'p2', 'visibility': 'customer', 'text': (
    'Consumer electronics may be returned within 7 days of delivery.')}]
REJECT = 'UNSUPPORTED_RETURN_WINDOW_ANCHOR'


def test_window_from_delivery_rejected():
    draft = 'Consumer electronics have a 7-day return window from delivery.'
    assert check_response_grounding(draft, NO_ANCHOR) == REJECT


def test_within_days_from_delivery_rejected():
    draft = 'You can return the item within 7 days from delivery.'
    assert check_response_grounding(draft, NO_ANCHOR) == REJECT


def test_days_after_purchase_rejected():
    draft = 'Returns are accepted 7 days after purchase.'
    assert check_response_grounding(draft, NO_ANCHOR) == REJECT


def test_from_the_date_of_delivery_rejected():
    draft = 'Please start the return within 7 days from the date of delivery.'
    assert check_response_grounding(draft, NO_ANCHOR) == REJECT


def test_original_of_delivery_form_still_rejected():
    draft = 'You can return the item within 7 days of delivery.'
    assert check_response_grounding(draft, NO_ANCHOR) == REJECT


def test_duration_without_anchor_accepted():
    draft = 'Consumer electronics have a 7-day return window.'
    assert check_response_grounding(draft, NO_ANCHOR) is None


def test_anchor_present_in_passage_is_supported_in_any_phrasing():
    for draft in ('You can return the item within 7 days of delivery.',
                  'Consumer electronics have a 7-day return window from delivery.',
                  'Returns are accepted 7 days after delivery.'):
        assert check_response_grounding(draft, OF_DELIVERY) is None, draft


def test_different_duration_is_not_supported_by_the_passage():
    draft = 'Consumer electronics have a 14-day return window from delivery.'
    assert check_response_grounding(draft, OF_DELIVERY) is not None


def test_different_anchor_is_not_supported_by_the_passage():
    draft = 'You can return the item within 7 days from purchase.'
    assert check_response_grounding(draft, OF_DELIVERY) == REJECT


def test_non_return_sentence_is_not_treated_as_a_return_window():
    draft = 'Your invoice is available 7 days after delivery of the order.'
    assert check_response_grounding(draft, NO_ANCHOR) != REJECT


# ======================================================================
# From test_return_window_completeness.py
# ======================================================================

QUESTION = "What is the return window for fashion products, and how do I initiate a return?"
# Exact approved customer wording (returns SOP, page 1).
PASSAGE = {
    "id": "approved-return-window",
    "source": "02_Returns_and_Refunds_SOP.pdf",
    "visibility": "customer",
    "text": (
        "Fashion and apparel have a 14-day return window if unworn and unwashed, with brand tags intact and original packaging. Eligible remedies include refund to source, wallet refund, or size/color exchange, subject to verification. "
        "Consumer electronics have a 7-day return window. The original box, serial numbers matching the invoice, and all accessories are required. A technical replacement or refund requires technician diagnosis and verification. "
        "Large home appliances have a 10-day return window, subject to being uninstalled, retaining original packaging and manuals, and mandatory brand-technician inspection before pickup. "
        "Customers can initiate a return through the orders dashboard."
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


# ======================================================================
# From test_return_policy_grounding_regression.py
# ======================================================================

PASSAGES = [{
    "id": "approved-return-policy",
    "source": "02_Returns_and_Refunds_SOP.pdf",
    "text": (
        "Fashion & Apparel 14 Days Unworn, unwashed, brand tags intact. "
        "Consumer Electronics 7 Days Original box and accessories. "
        "Large Home Appliances 10 Days Uninstalled and original packaging."
    ),
}]


def test_supported_electronics_return_window_not_delivery_promise():
    draft = "Consumer electronics must be returned within 7 days in their original box."
    assert check_response_grounding(draft, PASSAGES) is None


def test_wrong_category_return_window_rejected():
    draft = "Consumer electronics must be returned within 14 days."
    assert check_response_grounding(draft, PASSAGES) == "UNSUPPORTED_DELIVERY_TIME"


def test_unsupported_delivery_promise_still_rejected():
    draft = "We will deliver your order within 7 days."
    assert check_response_grounding(draft, PASSAGES) == "UNSUPPORTED_DELIVERY_TIME"


def test_unverified_refund_action_still_rejected():
    draft = "We have already issued your refund."
    assert check_response_grounding(draft, PASSAGES) == "UNVERIFIED_COMPLETED_ACTION"


# ======================================================================
# From test_category_window_guard.py
# ======================================================================

SRC = '02_Returns_and_Refunds_SOP.pdf'
FASHION = {'id': 'f19af5c766a3e755', 'source': SRC, 'visibility': 'customer', 'text': (
    'Fashion and apparel have a 14-day return window if unworn and unwashed, with brand '
    'tags intact and original packaging. Eligible remedies include refund to source, '
    'wallet refund, or size/color exchange, subject to verification.')}
ELECTRONICS = {'id': 'e613368ccb42a62f', 'source': SRC, 'visibility': 'customer', 'text': (
    'Consumer electronics have a 7-day return window. The original box, serial numbers '
    'matching the invoice, and all accessories are required. A technical replacement or '
    'refund requires technician diagnosis and verification.')}
APPLIANCES = {'id': '1b363a308af3b0a3', 'source': SRC, 'visibility': 'customer', 'text': (
    'Large home appliances have a 10-day return window, subject to being uninstalled, '
    'retaining original packaging and manuals, and mandatory brand-technician inspection '
    'before pickup.')}
ALL = [FASHION, ELECTRONICS, APPLIANCES]


@pytest.mark.parametrize('draft', [
    'Fashion and apparel have a 14-day return window if unworn and unwashed.',
    'For fashion and apparel, returns are accepted within 14 days.',
    'Fashion and apparel items can be returned within 14 days if they are unworn.',
    'Fashion returns are allowed within 14 days.',
    'Large appliances returns are accepted within 10 days.',
    'Large home appliances returns are accepted within 10 days.',
    'Consumer electronics returns are accepted within 7 days.',
    'Since the blender is a consumer electronic item, returns are accepted within 7 days.',
])
def test_correct_category_window_is_accepted(draft):
    assert check_response_grounding(draft, ALL) is None


@pytest.mark.parametrize('draft', [
    'For consumer electronics, returns are accepted within 14 days.',      # fashion's window
    'For fashion and apparel, returns are accepted within 7 days.',        # electronics' window
    'Large home appliances returns are accepted within 14 days.',          # fashion's window
    'Fashion and apparel items can be returned within 30 days.',           # not in policy
    'Headphones can be returned within 14 days.',                          # electronics is 7
    'Fashion returns are allowed within 7 days.',                          # electronics' window
    'Apparel returns are accepted within 10 days.',                        # appliances' window
])
def test_wrong_category_or_duration_is_rejected(draft):
    assert check_response_grounding(draft, ALL) is not None


@pytest.mark.parametrize('draft', [
    'Your fashion and apparel order will be delivered within 14 days.',
    'For fashion and apparel, refunds are processed within 14 days.',
    'We will dispatch the replacement within 14 days.',
])
def test_delivery_dispatch_and_refund_sla_are_not_return_windows(draft):
    assert check_response_grounding(draft, ALL) is not None


def test_sentence_naming_two_categories_is_not_attributed_to_either():
    draft = 'Fashion and apparel and consumer electronics returns are accepted within 14 days.'
    assert check_response_grounding(draft, ALL) is not None


def test_window_needs_its_own_category_passage_in_the_evidence():
    # Only the electronics passage was retrieved; a fashion window is unsupported.
    draft = 'For fashion and apparel, returns are accepted within 14 days.'
    assert check_response_grounding(draft, [ELECTRONICS]) is not None


def test_category_window_does_not_waive_an_unsupported_start_point():
    draft = 'For fashion and apparel, returns are accepted within 14 days of delivery.'
    assert check_response_grounding(draft, ALL) == 'UNSUPPORTED_RETURN_WINDOW_ANCHOR'


# ======================================================================
# From test_doa_timeline_grounding.py
# ======================================================================

DOA = {"text": "An electronic device or appliance that fails to power on or has severe functional failure within seven calendar days of delivery may qualify for Dead-on-Arrival assessment. Certification and replacement require verification; no replacement dispatch is confirmed by this policy."}
RETURN = {"text": "Consumer electronics have a 7-day return window. The original box, serial numbers matching the invoice, and all accessories are required. A technical replacement or refund requires technician diagnosis and verification."}


def test_actual_doa_draft_not_falsely_rejected():
    draft = ("Since your blender is a consumer electronic item, it is eligible for return within 7 days of delivery. "
             "To request a replacement, please ensure you return the blender in its original box with serial numbers matching the invoice and include all accessories. "
             "A technician will need to diagnose and verify the issue before a replacement or refund can be authorized. "
             "If the blender failed to power on or has severe functional failure within 7 days, it may qualify for a Dead-on-Arrival assessment, "
             "but certification and replacement still require verification. Please initiate the return process promptly and keep the original packaging and accessories ready for assessment.")
    assert check_response_grounding(draft, [DOA, RETURN]) is None


def test_invented_shipping_eta_still_rejected():
    assert check_response_grounding("Your blender will arrive within 7 days.", [DOA, RETURN]) == "UNSUPPORTED_DELIVERY_TIME"


def test_wrong_doa_duration_rejected():
    assert check_response_grounding("If the device fails to power on within 10 days, it may qualify for Dead-on-Arrival assessment.", [DOA]) == "UNSUPPORTED_DELIVERY_TIME"


def test_business_days_not_equated_to_calendar_days():
    assert check_response_grounding("If the device fails to power on within 7 business days, it may qualify for Dead-on-Arrival assessment.", [DOA]) == "UNSUPPORTED_DELIVERY_TIME"


# ======================================================================
# From test_tc02_return_window_publication.py
# ======================================================================

# Test the project module in its normal package location.

PASSAGES_tc02_return_window_publication = [
    {"id": "e613368ccb42a62f", "source": "02_Returns_and_Refunds_SOP.pdf", "visibility": "customer",
     "text": "Consumer electronics have a 7-day return window. The original box, serial numbers matching the invoice, and all accessories are required. A technical replacement or refund requires technician diagnosis and verification."},
    {"id": "ca91ca28b962d2b7", "source": "02_Returns_and_Refunds_SOP.pdf", "visibility": "customer",
     "text": "Customers can initiate a return through the orders dashboard. Photographs are required for damaged or defective products. Submission is not approval; eligibility and order ownership must be verified."},
]
QUERY = "I purchased headphones last week. Can you explain the return policy, eligibility window, and steps to request a refund?"
DRAFT = ("You can return headphones, classified as consumer electronics, within 7 days of purchase. "
         "The product must be in its original box with serial numbers matching your invoice and include all accessories. "
         "If the headphones are damaged or defective, please provide photographs when initiating the return. "
         "To start the return process, please use the orders dashboard on NovaCart. "
         "Keep in mind that submitting a return request does not guarantee approval, as eligibility and order ownership will be verified. "
         "For technical replacements or refunds, a technician's diagnosis may be required. "
         "If you provide your order details, we can assist further with your specific case.")


def test_tc02_repairs_only_unsupported_purchase_anchor():
    assert check_response_grounding(DRAFT, PASSAGES_tc02_return_window_publication, customer_message=QUERY) == 'UNSUPPORTED_RETURN_WINDOW_ANCHOR'
    repaired = repair_unsupported_return_anchor(DRAFT, PASSAGES_tc02_return_window_publication)
    assert repaired != DRAFT
    assert 'within 7 days of purchase' not in repaired
    assert 'within 7 days' in repaired
    assert 'orders dashboard' in repaired
    assert check_response_grounding(repaired, PASSAGES_tc02_return_window_publication, customer_message=QUERY) is None


@pytest.mark.parametrize('draft', [
    'You can return headphones, classified as consumer electronics, within 14 days.',
    'We will deliver your order within 7 days.',
    'We will issue your refund within 7 days.',
])
def test_unsupported_timelines_still_rejected(draft):
    assert check_response_grounding(draft, PASSAGES_tc02_return_window_publication, customer_message=QUERY) is not None


# ======================================================================
# From test_answer_repair.py
# ======================================================================

EVIDENCE = [{'id':'returns-1','text':'Returns require an eligible item and proof of purchase.','visibility':'customer'}]

@pytest.mark.parametrize('draft', [
    'Delivery takes 3 days.',
    'Your order is in transit. Returns require an eligible item and proof of purchase.',
    'We will issue your refund. Returns require an eligible item and proof of purchase.',
    'Delivery takes 3 days. Delivery takes 4 days. Returns require proof of purchase.',
])
def test_no_unsafe_repair(draft):
    result, reason = repair_isolated_time_claim(draft, EVIDENCE)
    assert result == draft and reason is None


def test_isolated_unsupported_timing_removed_and_rechecked():
    draft = 'Returns require an eligible item and proof of purchase. Delivery takes 3 days.'
    result, reason = repair_isolated_time_claim(draft, EVIDENCE)
    assert reason == 'UNSUPPORTED_DELIVERY_TIME'
    assert result == 'Returns require an eligible item and proof of purchase.'
    assert check_response_grounding(result, EVIDENCE) is None


def test_dangling_reference_prevents_repair():
    draft = 'Delivery takes 3 days. Therefore you can plan your return.'
    assert repair_isolated_time_claim(draft, EVIDENCE) == (draft, None)


def test_empty_evidence_does_not_trigger_repair():
    draft = 'Returns require proof of purchase. Delivery takes 3 days.'
    assert repair_isolated_time_claim(draft, []) == (draft, None)


# ======================================================================
# From test_answer_repair_return_window_safety.py
# ======================================================================

def test_never_removes_return_window_even_if_checker_misclassifies(monkeypatch):
    draft = (
        "Fashion and apparel have a 14-day return window if unworn and unwashed. "
        "You can initiate a return through your orders dashboard."
    )
    def checker(text, passages):
        return "UNSUPPORTED_DELIVERY_TIME" if "14-day" in text else None
    monkeypatch.setattr(answer_repair, "check_response_grounding", checker)
    monkeypatch.setattr(answer_repair, "check_customer_visibility", lambda text: None)
    assert answer_repair.repair_isolated_time_claim(draft, []) == (draft, None)


def test_still_repairs_isolated_unsupported_delivery_claim(monkeypatch):
    draft = "Delivery is guaranteed within 2 days. Please check your order details."
    def checker(text, passages):
        return "UNSUPPORTED_DELIVERY_TIME" if "2 days" in text else None
    monkeypatch.setattr(answer_repair, "check_response_grounding", checker)
    monkeypatch.setattr(answer_repair, "check_customer_visibility", lambda text: None)
    assert answer_repair.repair_isolated_time_claim(draft, []) == (
        "Please check your order details.", "UNSUPPORTED_DELIVERY_TIME"
    )


# ======================================================================
# From test_phase2d6_return_window.py
# ======================================================================

# Exact wording of the approved customer passage (returns SOP, page 1). It states
# a 7-day window but names NO starting point (purchase or delivery).
EVIDENCE_phase2d6_return_window = [{
    "id": "electronics-return-policy",
    "source": "02_Returns_and_Refunds_SOP.pdf",
    "page": 1,
    "visibility": "customer",
    "text": (
        "Consumer electronics have a 7-day return window. The original box, serial numbers matching the invoice, and all accessories are required. A technical replacement or refund requires technician diagnosis and verification."
    ),
}]


ANCHOR = "UNSUPPORTED_RETURN_WINDOW_ANCHOR"


@pytest.mark.parametrize("draft,expected", [
    # Rule 25: the passage gives a duration only, so a purchase/delivery start
    # point must not be asserted. (Earlier versions accepted "of delivery".)
    (
        "For consumer electronics like headphones, our policy allows returns "
        "within 7 days of delivery, provided the original box and accessories are intact.",
        ANCHOR,
    ),
    (
        "Returns are allowed within 7 days of delivery for consumer electronics.",
        ANCHOR,
    ),
    # The same duration without an invented start point is supported.
    (
        "For consumer electronics like headphones, our policy allows returns "
        "within 7 days, provided the original box and accessories are intact.",
        None,
    ),
    (
        "Consumer electronics have a 7-day return window.",
        None,
    ),
    (
        "For consumer electronics, our policy allows returns within 14 days of delivery.",
        "UNSUPPORTED_DELIVERY_TIME",
    ),
    (
        "Your headphones will be delivered within 7 days.",
        "UNSUPPORTED_DELIVERY_TIME",
    ),
    (
        "Your replacement will arrive within 7 days.",
        "UNSUPPORTED_DELIVERY_TIME",
    ),
])
def test_return_window_not_delivery_promise(draft, expected):
    assert check_response_grounding(draft, EVIDENCE_phase2d6_return_window) == expected
