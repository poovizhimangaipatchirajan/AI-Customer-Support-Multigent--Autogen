"""Consolidated grounding guard tests.

Merged without behaviour changes from:
- test_grounding_guard_hardening.py: Offline tests for conservative grounding checks; no API keys required.
- test_grounding_guard_phase2.py: Offline regression tests; no OpenAI calls, database or RAG index changes.
- test_phase2d1_grounding_precision.py: regression tests
- test_phase2d4_boundaries.py: Focused regression tests; no API, database, or model calls.
- test_invoice_grounding.py: Focused regression checks for invoice citation relevance.
- test_shipping_grounding_regression.py: Focused regression tests for dispatch/delivery framing and SOP visibility.
"""
from backend.services.grounding_guard import check_response_grounding
from backend.services.grounding_guard import check_response_grounding, customer_safe_passages
from backend.services.response_diagnostics import inspect_response
from pathlib import Path
import ast
from backend.services.grounding_guard import customer_safe_passages
import importlib.util
import pytest


# ======================================================================
# From test_grounding_guard_hardening.py
# ======================================================================

PASSAGES = [{'text': 'Orders have preparation and in-transit statuses; specific delivery ETA is unavailable.'}]


def test_ordinary_shipping_guidance_not_blocked():
    assert check_response_grounding('We cannot confirm an order-specific ETA.', PASSAGES) is None


def test_unsupported_numeric_delivery_promise_blocked():
    assert check_response_grounding('Your order will arrive in 2 days.', PASSAGES) == 'UNSUPPORTED_DELIVERY_TIME'


def test_completed_refund_claim_blocked():
    assert check_response_grounding('We have processed your refund.', PASSAGES) == 'UNVERIFIED_COMPLETED_ACTION'


def test_negative_refund_statement_allowed():
    assert check_response_grounding('We have not processed your refund.', PASSAGES) is None


def test_empty_draft_blocked():
    assert check_response_grounding('  ', PASSAGES) == 'EMPTY_DRAFT'


# ======================================================================
# From test_grounding_guard_phase2.py
# ======================================================================

def policy(text):
    return [{'id': 'policy-1', 'visibility': 'customer', 'text': text}]


def test_refund_timeline_supported_with_business_working_synonym():
    assert check_response_grounding(
        'Refund processing takes 5 business days.',
        policy('Refund processing takes 5 working days.'),
    ) is None


def test_refund_timeline_not_supported_by_shipping_passage():
    assert check_response_grounding(
        'Refund processing takes 5 business days.',
        policy('Delivery takes 5 business days.'),
    ) == 'UNSUPPORTED_DELIVERY_TIME'


def test_unsupported_refund_timeline_rejected():
    assert check_response_grounding(
        'Refund processing takes 3 business days.',
        policy('Refund processing takes 5 business days.'),
    ) == 'UNSUPPORTED_DELIVERY_TIME'


def test_no_timeline_explanation_is_safe():
    assert check_response_grounding(
        'You can request a return through the orders dashboard, subject to eligibility.',
        policy('Customers can request returns from the orders dashboard.'),
    ) is None


def test_unsupported_replacement_timeline_rejected():
    assert check_response_grounding(
        'Your replacement arrives in 2 days.',
        policy('Replacement dispatch follows verification.'),
    ) == 'UNSUPPORTED_DELIVERY_TIME'


def test_completed_action_not_hidden_by_unrelated_negation():
    assert check_response_grounding(
        'We cannot confirm the ETA, but we have issued your refund.',
        policy('Refund requests can be submitted in the app.'),
    ) == 'UNVERIFIED_COMPLETED_ACTION'


def test_unverified_completed_action_rejected():
    assert check_response_grounding(
        'We have approved your replacement.',
        policy('Replacement requests require review.'),
    ) == 'UNVERIFIED_COMPLETED_ACTION'


def test_negated_eta_not_treated_as_promise():
    assert check_response_grounding(
        'We cannot promise delivery in 3 days.',
        policy('Delivery estimates depend on order status.'),
    ) is None


# ======================================================================
# From test_phase2d1_grounding_precision.py
# ======================================================================

def passage(identifier, source, page, text, visibility='customer'):
    return dict(id=identifier, source=source, page=page, text=text, visibility=visibility)


# Exact approved customer wording (returns SOP, page 1), one passage per category.
RETURNS = passage('r1', '02_Returns_and_Refunds_SOP.pdf', 1,
    "Fashion and apparel have a 14-day return window if unworn and unwashed, with brand tags intact and original packaging. Eligible remedies include refund to source, wallet refund, or size/color exchange, subject to verification. "
    "Consumer electronics have a 7-day return window. The original box, serial numbers matching the invoice, and all accessories are required. A technical replacement or refund requires technician diagnosis and verification. "
    "Large home appliances have a 10-day return window, subject to being uninstalled, retaining original packaging and manuals, and mandatory brand-technician inspection before pickup.")


def test_category_window_14_days_not_delivery_sla():
    assert check_response_grounding('For fashion and apparel, returns are accepted within 14 days.', [RETURNS]) is None


def test_electronics_window_7_days_not_delivery_sla():
    assert check_response_grounding('For Consumer Electronics, you can request a replacement or refund within 7 days of receiving the item.', [RETURNS]) is None


def test_wrong_category_window_not_supported():
    assert check_response_grounding('For Consumer Electronics, returns are accepted within 14 days.', [RETURNS]) == 'UNSUPPORTED_DELIVERY_TIME'


def test_delivery_promise_not_supported_by_return_window():
    assert check_response_grounding('Your delivery will arrive within 14 days.', [RETURNS]) is not None


def test_refund_settlement_not_supported_by_return_window():
    assert check_response_grounding('For fashion and apparel, refunds are processed within 14 days.', [RETURNS]) is not None


def test_customer_filter_blocks_mixed_internal_pages():
    pages = [
        passage('s3', '01_Shipping_and_Delivery_SOP.pdf', 3, 'Carrier GPS audit'),
        passage('s4', '01_Shipping_and_Delivery_SOP.pdf', 4, 'We dispatched replacement'),
        passage('r3', '02_Returns_and_Refunds_SOP.pdf', 3, 'Fraud investigation'),
        passage('p4', '03_Product_and_Warranty_SOP.pdf', 4, 'We dispatched replacement'),
        RETURNS,
    ]
    assert [p['id'] for p in customer_safe_passages(pages)] == ['r1']


def test_diagnostics_does_not_trust_unsafe_page_metadata():
    bad = passage('p4', '03_Product_and_Warranty_SOP.pdf', 4, 'Replacement dispatched')
    result = inspect_response('My item is broken', 'Please provide your order ID.', ['p4'], {'p4': bad})
    assert 'UNSAFE_OR_EMPTY_CITATION' in result.codes


def test_diagnostics_accepts_valid_category_window():
    result = inspect_response('What is the return policy?', 'For fashion and apparel, returns are accepted within 14 days.', ['r1'], {'r1': RETURNS})
    assert 'UNSUPPORTED_DELIVERY_TIME' not in result.codes


def test_filter_preserves_original_evidence():
    before = RETURNS.copy()
    customer_safe_passages([RETURNS])
    assert RETURNS == before


# ======================================================================
# From test_phase2d4_boundaries.py
# ======================================================================

def _p(source, page, text, ident='x'):
    return {'id': ident, 'source': source, 'page': page,
            'visibility': 'customer', 'text': text}


def test_internal_guidance_addendum_excluded():
    assert customer_safe_passages([_p('02_Returns_and_Refunds_SOP.pdf', 5,
        'FIXFLOW AI | IMPLEMENTATION GUIDANCE ADDENDUM | Not a policy amendment')]) == []


def test_sensitive_operational_pages_excluded_even_if_mislabeled():
    for source, page in [('01_Shipping_and_Delivery_SOP.pdf', 3),
                         ('01_Shipping_and_Delivery_SOP.pdf', 4),
                         ('02_Returns_and_Refunds_SOP.pdf', 3),
                         ('03_Product_and_Warranty_SOP.pdf', 4)]:
        assert customer_safe_passages([_p(source, page, 'Operational procedures')]) == []


def test_customer_policy_kept():
    p = _p('01_Shipping_and_Delivery_SOP.pdf', 1, 'Promised Estimated Delivery Date (EDD).')
    assert customer_safe_passages([p]) == [p]


def test_worker_keeps_fail_closed_publication_boundary():
    source = Path(__file__).resolve().parents[1] / 'backend' / 'worker' / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {'persist', 'finish'} <= functions.keys()
    body = ast.get_source_segment(source.read_text(encoding='utf-8'), functions['finish'])
    # finish() now publishes a customer-safe holding message for escalated
    # tickets, so the invariant is not a source string but that escalation is
    # derived from every backend gate and decides the final ticket status.
    for gate in ('not success', 'not result', 'ticket["risk"]',
                 'mandatory_refund_review(dict(ticket))', 'requires_review'):
        assert gate in body, gate
    assert '"ESCALATED" if escalate else "ANSWERED"' in body
    assert 'No refund or replacement has been approved or processed.' in body
    persist = ast.get_source_segment(source.read_text(encoding='utf-8'), functions['persist'])
    assert 'MISSING_DOMAIN_CITATION' in persist
    assert 'UNSAFE_POLICY_PASSAGE' in persist


# ======================================================================
# From test_invoice_grounding.py
# ======================================================================

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


# ======================================================================
# From test_shipping_grounding_regression.py
# ======================================================================

# When copied to tests/, import the installed project module.

EVIDENCE = [{"id": "shipping-p1", "source": "01_Shipping_and_Delivery_SOP.pdf", "page": 1, "visibility": "customer", "text": "The order has a promised Estimated Delivery Date (EDD). DMF sellers generate dispatch manifests within 48 business hours."}]

@pytest.mark.parametrize("draft", [
    "NovaCart's standard delivery timeframe varies depending on the fulfillment channel: DMF sellers dispatch within 48 business hours.",
    "Standard delivery times depend on FBN, DMF and HBN.",
    "Your order will be delivered within 48 business hours.",
])
def test_dispatch_not_delivery(draft):
    assert check_response_grounding(draft, EVIDENCE) == "DISPATCH_MISREPRESENTED_AS_DELIVERY"


def test_honest_missing_timeframe_is_allowed():
    draft = "The available shipping SOP does not specify a universal delivery duration. It refers to a promised Estimated Delivery Date (EDD)."
    assert check_response_grounding(draft, EVIDENCE) is None


def test_shipping_page_four_is_not_customer_visible():
    passages = [*EVIDENCE, {"id": "shipping-p4", "source": "01_Shipping_and_Delivery_SOP.pdf", "page": 4, "visibility": "customer", "text": "Immediate replacement priority dispatch and discretionary goodwill credit."}]
    safe = customer_safe_passages(passages)
    assert [p["id"] for p in safe] == ["shipping-p1"]


# ======================================================================
# Category-membership repair (claims seen in live runs; rules 17 and 26)
# ======================================================================
import pytest as _pytest_category
from backend.services.grounding_guard import (
    repair_unsupported_category_membership, CATEGORY_LIMITATION)

_CATEGORY_PASSAGES = [
    {'id': 'e', 'text': 'Consumer electronics have a 7-day return window.'},
    {'id': 'f', 'text': 'Fashion and apparel have a 14-day return window if unworn and unwashed.'},
]


@_pytest_category.mark.parametrize('draft,expected', [
    ('Consumer electronics like your cable have a 7-day return window.',
     'Consumer electronics have a 7-day return window.'),
    ('For fashion and apparel items like shoes, there is a 14-day return window if unworn.',
     'For fashion and apparel items, there is a 14-day return window if unworn.'),
    ('The return window for fashion and apparel items, including your shirt, is 14 days.',
     'The return window for fashion and apparel items is 14 days.'),
    ('The blender you received is considered a consumer electronic and has a 7-day return window. '
     'A technician diagnosis is required.',
     'Consumer electronics have a 7-day return window. ' + CATEGORY_LIMITATION
     + ' A technician diagnosis is required.'),
    ('The blender you received, being a consumer electronic, has a 7-day return window.',
     'Consumer electronics have a 7-day return window. ' + CATEGORY_LIMITATION),
    ('Shoes are categorized under fashion and apparel, which have a 14-day return window if unworn.',
     'Fashion and apparel items have a 14-day return window if unworn. ' + CATEGORY_LIMITATION),
    ('Since it is a consumer electronic item, it has a 7-day return window.',
     'Consumer electronics have a 7-day return window. ' + CATEGORY_LIMITATION),
])
def test_unsupported_category_membership_is_removed(draft, expected):
    repaired, changed = repair_unsupported_category_membership(draft, _CATEGORY_PASSAGES)
    assert changed and repaired == expected
    assert check_response_grounding(repaired, _CATEGORY_PASSAGES) is None


@_pytest_category.mark.parametrize('draft', [
    'If your product is consumer electronics, please note there is a 7-day return window.',
    'Consumer electronics have a 7-day return window.',
    'Please upload photographs of the damaged item.',
    'Refunds for items that are consumer electronics require a technician diagnosis.',
    'The return window for consumer electronics is a 7-day window.',
])
def test_supported_or_conditional_wording_is_unchanged(draft):
    assert repair_unsupported_category_membership(draft, _CATEGORY_PASSAGES) == (draft, False)


def test_item_named_by_the_passage_is_kept():
    passages = [{'id': 'x', 'text': 'Consumer electronics such as headphones have a 7-day window.'}]
    draft = 'Consumer electronics like headphones have a 7-day return window.'
    assert repair_unsupported_category_membership(draft, passages) == (draft, False)


def test_paragraphs_are_preserved():
    draft = 'Your USB-C cable is considered a consumer electronic item.\n\nPlease initiate a return.'
    repaired, changed = repair_unsupported_category_membership(draft, _CATEGORY_PASSAGES)
    assert changed and repaired == CATEGORY_LIMITATION + '\n\nPlease initiate a return.'


def test_example_before_a_clause_comma_is_removed():
    draft = 'Please note that for fashion items like shoes, the return window is 14 days.'
    repaired, changed = repair_unsupported_category_membership(draft, _CATEGORY_PASSAGES)
    assert changed and repaired == 'Please note that for fashion items, the return window is 14 days.'


def test_category_list_is_left_for_revalidation():
    draft = 'Consumer electronics, including phones, laptops and tablets, have a 7-day window.'
    assert repair_unsupported_category_membership(draft, _CATEGORY_PASSAGES) == (draft, False)
