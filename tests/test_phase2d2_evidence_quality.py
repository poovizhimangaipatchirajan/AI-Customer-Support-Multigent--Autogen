from backend.services.grounding_guard import customer_safe_passages
from backend.services.response_diagnostics import inspect_response


def p(id, source, page, text):
    return dict(id=id, source=source, page=page, visibility='customer', text=text)


SHIPPING = p('s1', '01_Shipping_and_Delivery_SOP.pdf', 1,
             'Delivery EDD depends on order details and delivery location.')
RETURNS = p('r1', '02_Returns_and_Refunds_SOP.pdf', 1,
            'Fashion & Apparel 14 Days. Return requests may be initiated in orders dashboard.')


def test_shipping_and_returns_requires_both_sources():
    result = inspect_response('How long does delivery take and can I return it?',
        'Delivery depends on order details. Returns depend on category.', ['r1'], {'r1': RETURNS})
    assert 'MISSING_DOMAIN_CITATION' in result.codes


def test_both_sources_clear_domain_gap():
    result = inspect_response('How long does delivery take and can I return it?',
        'Delivery depends on order details. Returns depend on category.',
        ['s1', 'r1'], {'s1': SHIPPING, 'r1': RETURNS})
    assert 'MISSING_DOMAIN_CITATION' not in result.codes


def test_general_return_policy_only_needs_return_source():
    result = inspect_response('What is your return policy?',
        'Fashion and apparel returns are available subject to eligibility.', ['r1'], {'r1': RETURNS})
    assert 'MISSING_DOMAIN_CITATION' not in result.codes


def test_safe_abstention_not_forced_to_cite_every_domain():
    result = inspect_response('How long is shipping and what is the return policy?',
        'I cannot verify shipping information. Returns depend on category.', ['r1'], {'r1': RETURNS})
    assert 'MISSING_DOMAIN_CITATION' not in result.codes


def test_addendum_not_customer_policy_evidence():
    addendum = p('a', '02_Returns_and_Refunds_SOP.pdf', 5,
        'FIXFLOW AI | IMPLEMENTATION GUIDANCE ADDENDUM | Not a policy amendment. '
        'Expected customer-facing answer: fashion returns in 14 days.')
    assert customer_safe_passages([addendum, RETURNS]) == [RETURNS]
    result = inspect_response('What is the return policy?', 'Return windows vary.', ['a'], {'a': addendum})
    assert 'UNSAFE_OR_EMPTY_CITATION' in result.codes


def test_existing_internal_pages_still_blocked():
    internal = p('internal', '03_Product_and_Warranty_SOP.pdf', 4,
        'We have dispatched a replacement already.')
    assert customer_safe_passages([internal]) == []


def test_no_mutation_of_original_evidence():
    original = RETURNS.copy()
    customer_safe_passages([RETURNS])
    assert RETURNS == original
