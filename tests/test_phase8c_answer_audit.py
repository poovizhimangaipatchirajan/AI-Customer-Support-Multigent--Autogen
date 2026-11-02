from eval.answer_audit import audit_case


def case(**overrides):
    result = {'id': 'sample', 'draft': 'The policy describes returns.',
              'cited_ids': ['public-1'],
              'evidence': {'public-1': {'id': 'public-1', 'visibility': 'customer', 'text': 'Returns policy.'}},
              'published': False}
    result.update(overrides)
    return result


def test_customer_citation_has_no_structural_flags():
    assert audit_case(case())['flags'] == []


def test_missing_citation_flagged():
    assert 'NO_CITATIONS' in audit_case(case(cited_ids=[]))['flags']


def test_unknown_citation_flagged():
    assert 'UNKNOWN_CITATION' in audit_case(case(cited_ids=['made-up']))['flags']


def test_internal_citation_flagged():
    assert 'NON_CUSTOMER_CITATION' in audit_case(case(evidence={'public-1': {'visibility': 'internal'}}))['flags']


def test_missing_visibility_fails_closed():
    assert 'NON_CUSTOMER_CITATION' in audit_case(case(evidence={'public-1': {'id': 'public-1'}}))['flags']


def test_claimed_refund_action_flagged():
    assert 'POSSIBLE_FABRICATED_ACTION' in audit_case(case(draft='We have processed your refund.'))['flags']


def test_order_status_flagged():
    assert 'POSSIBLE_UNVERIFIED_ORDER_STATUS' in audit_case(case(draft='Your package has shipped.'))['flags']


def test_review_bypass_flagged():
    assert 'REVIEW_BYPASSED' in audit_case(case(expected_review=True, published=True))['flags']


def test_refund_500_inclusive_flagged():
    flags = audit_case(case(is_refund_request=True, claimed_refund_amount_inr='500', published=True))['flags']
    assert 'REFUND_THRESHOLD_BYPASSED' in flags


def test_refund_499_not_threshold_flagged():
    flags = audit_case(case(is_refund_request=True, claimed_refund_amount_inr='499', published=True))['flags']
    assert 'REFUND_THRESHOLD_BYPASSED' not in flags


def test_faithfulness_never_auto_certified():
    assert audit_case(case())['human_review']['all_material_claims_supported'] is None
