from backend.services.grounding_guard import check_response_grounding, customer_safe_passages
from backend.services.response_diagnostics import inspect_response


def passage(identifier, source, page, text, visibility='customer'):
    return dict(id=identifier, source=source, page=page, text=text, visibility=visibility)


RETURNS = passage('r1', '02_Returns_and_Refunds_SOP.pdf', 1,
    'Fashion & Apparel 14 Days Unworn, unwashed, brand tags intact. '
    'Consumer Electronics 7 Days Original box, matching serial numbers, all accessories. '
    'Large Home Appliances 10 Days Uninstalled, original packaging.')


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
