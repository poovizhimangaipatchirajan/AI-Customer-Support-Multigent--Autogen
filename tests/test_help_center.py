"""Consolidated help center tests.

Merged without behaviour changes from:
- test_help_center_regression.py: Run with: python -m pytest tests/test_help_center_regression.py -q
- test_help_center_conditional_guidance.py: Offline, no embedding API: generic conditional policy answerability tests.
- test_phase2c_response_harness.py: regression tests
- test_phase8c_answer_audit.py: regression tests
- test_response_diagnostics.py: Offline tests of read-only diagnostics, not fabricated model pass rates.
"""
from backend.services.help_center import answer_question
from backend.rag.customer_policy import approved_passages
from eval.phase2c_live_responses import CASES, POLICIES, assess, select_cases
from eval.answer_audit import audit_case
import pytest
from backend.services.response_diagnostics import inspect_response


# ======================================================================
# From test_help_center_regression.py
# ======================================================================

class FakeSearch:
    def __init__(self, docs):
        self.docs = docs

    def search(self, question, k=4):
        return self.docs[:k]


def doc(source, text, visibility='customer', chunk_id='test-chunk'):
    return {'id': chunk_id, 'source': source, 'text': text,
            'visibility': visibility, 'version': 'pdf-sop-v1'}


RETURNS = doc('02_Returns_and_Refunds_SOP.pdf',
              '1.2 Category-Specific Return Windows & Constraints '
              'Fashion & Apparel 14 Days Unworn, unwashed, brand tags intact, '
              'original polybag. Return initiation through the customer app.')
WARRANTY = doc('03_Product_and_Warranty_SOP.pdf',
               '1.2 Multi-Tiered Warranty Structure NovaCart 30-Day Platform '
               'Assurance covers manufacturing defects. NovaCare Extended '
               'Protection is optional purchased accidental damage coverage '
               'for up to 36 months.')
BILLING = doc('05_Payments_and_Billing_SOP.pdf',
              '3.3 Cash on Delivery (COD) Discrepancy Protocols: '
              'Overcharging by driver above invoice total. '
              'Audit courier branch cash vault; confirm delivery status.')


def test_hc001_general_returns_has_real_citation():
    result = answer_question('What is NovaCart return policy?', FakeSearch([RETURNS]))
    assert result['status'] == 'answered'
    assert result['sources'][0]['chunk_id'] == 'test-chunk'
    assert '14-day' in result['answer']


def test_hc002_fashion_requirements():
    q = ('What are the return eligibility conditions for Fashion & Apparel '
         'products, including the return window, product condition, and '
         'packaging requirements?')
    result = answer_question(q, FakeSearch([RETURNS]))
    assert result['status'] == 'answered'
    assert 'original polybag' in result['answer']


def test_hc003_no_unsubstantiated_lifetime_warranty():
    q = ('Does NovaCart provide free lifetime warranty coverage for every '
         'product, including accidental damage?')
    result = answer_question(q, FakeSearch([WARRANTY]))
    assert result['status'] == 'answered'
    assert 'could not verify free lifetime' in result['answer']
    assert 'optional' in result['answer'].lower()
    assert '36 months' in result['answer']
    assert result['sources'][0]['document'] == WARRANTY['source']


def test_hc004_helicopter_false_positive():
    q = 'Does NovaCart provide free helicopter delivery for all orders above ₹1,000?'
    result = answer_question(q, FakeSearch([BILLING]))
    assert result['status'] == 'no_evidence'
    assert result['sources'] == []


def test_internal_only_evidence_is_rejected():
    result = answer_question('What is NovaCart return policy?',
                             FakeSearch([doc(RETURNS['source'], RETURNS['text'], 'internal')]))
    assert result['status'] == 'no_evidence'


def test_unapproved_source_is_rejected():
    result = answer_question('What is NovaCart return policy?',
                             FakeSearch([doc('06_Internal_Escalation_SOP.pdf', RETURNS['text'])]))
    assert result['status'] == 'no_evidence'


def test_case_specific_refund_still_requires_ticket():
    result = answer_question('I want a refund for my order', FakeSearch([RETURNS]))
    assert result['status'] == 'ticket_required'
    assert result['sources'] == []


def test_hc003_no_warranty_evidence_still_fails_closed():
    q = 'Does NovaCart provide free lifetime warranty for every product?'
    result = answer_question(q, FakeSearch([BILLING]))
    assert result['status'] == 'no_evidence'
    assert result['sources'] == []


def test_hc003_internal_warranty_cannot_be_quoted():
    q = 'Does NovaCart provide free lifetime warranty for every product?'
    result = answer_question(q, FakeSearch([doc(WARRANTY['source'], WARRANTY['text'], 'internal')]))
    assert result['status'] == 'no_evidence'


def test_hc003_partial_warranty_evidence_does_not_invent_coverage():
    q = 'Does NovaCart provide free lifetime warranty for every product?'
    vague = doc(WARRANTY['source'], 'Warranty information is available.')
    result = answer_question(q, FakeSearch([vague]))
    assert result['status'] == 'no_evidence'
    assert result['sources'] == []


def test_hc002_fashion_answer_excludes_other_categories():
    full = doc(RETURNS['source'], RETURNS['text'] +
               ' Large Home Appliances 10 Days Uninstalled, all manuals.')
    result = answer_question('What are Fashion and Apparel return conditions?', FakeSearch([full]))
    assert result['status'] == 'answered'
    assert '14-day' in result['answer']
    assert 'Large Home Appliances' not in result['answer']


# ======================================================================
# From test_help_center_conditional_guidance.py
# ======================================================================

class ApprovedSearch:
    def __init__(self, document):
        self.document = dict(document, semantic_score=0.51)
        self.calls = 0

    def search(self, question, k=4):
        self.calls += 1
        return [self.document]


SHIPPING = next(d for d in approved_passages() if d['source'].startswith('01_'))
WARRANTY_help_center_conditional_guidance = next(d for d in approved_passages() if d['source'].startswith('03_'))


def test_personal_delivered_case_gets_general_guidance_and_ticket():
    search = ApprovedSearch(SHIPPING)
    result = answer_question("My parcel was marked delivered but I never got it", search=search)
    assert result['status'] == 'ticket_required'
    assert 'mailroom' in result['answer']
    assert 'support ticket' in result['answer']
    assert result['sources'][0]['document'] == SHIPPING['source']
    assert search.calls == 1


def test_alternate_wording_has_same_safe_outcome():
    result = answer_question("The courier marked my package delivered incorrectly", search=ApprovedSearch(SHIPPING))
    assert result['status'] == 'ticket_required'
    assert 'mailroom' in result['answer']
    assert result['sources'][0]['chunk_id'] == SHIPPING['id']


def test_ambiguous_shipment_does_not_assert_delivery_confirmation():
    result = answer_question("I think my shipment is missing", search=ApprovedSearch(SHIPPING))
    assert result['status'] == 'clarification_required'
    assert 'confirm whether' in result['answer']
    assert 'mailroom' not in result['answer']
    assert result['sources'][0]['chunk_id'] == SHIPPING['id']


def test_tracking_request_does_not_misapply_missing_delivery_policy():
    result = answer_question("Where is my order?", search=ApprovedSearch(SHIPPING))
    assert result['status'] == 'ticket_required'
    assert 'mailroom' not in result['answer']
    assert not result['sources']


def test_general_policy_retains_complete_cited_guidance():
    result = answer_question('What is the policy if an order is marked delivered but not received?', search=ApprovedSearch(SHIPPING))
    assert result['status'] == 'answered'
    assert 'identity and delivery verification' in result['answer']


def test_standard_warranty_still_answered():
    result = answer_question('What does the standard product warranty cover?', search=ApprovedSearch(WARRANTY_help_center_conditional_guidance))
    assert result['status'] == 'answered'
    assert 'manufacturing defects' in result['answer']


def test_refund_action_never_triggers_retrieval():
    search = ApprovedSearch(SHIPPING)
    result = answer_question('Please refund me for my order', search=search)
    assert result['status'] == 'ticket_required'
    assert not result['sources']
    assert search.calls == 0


def test_unapproved_forged_evidence_not_exposed():
    forged = dict(SHIPPING, text='Refunds are automatically guaranteed.')
    result = answer_question('What is the policy if marked delivered but not received?', search=ApprovedSearch(forged))
    assert result['status'] == 'no_evidence'
    assert not result['sources']


# ======================================================================
# From test_phase2c_response_harness.py
# ======================================================================

def test_cases_have_unique_names():
    assert len({case[0] for case in CASES}) == len(CASES)


def test_case_selection():
    assert [c[0] for c in select_cases('general_returns,shipping_eta')] == ['general_returns','shipping_eta']


def test_unknown_case_fails_closed():
    import pytest
    with pytest.raises(ValueError): select_cases('unknown')


def test_missing_callback_detected():
    assert assess(CASES[0],[])['status'] == 'NO_OUTCOME'


def test_missing_citation_requires_review():
    item={'draft':'Please verify your order.', 'ids':[], 'escalate':False,'evidence':{}}
    result=assess(CASES[0],[item])
    assert result['status']=='REVIEW'
    assert 'MISSING_CITATIONS' in result['diagnostic_codes']


def test_fake_completed_refund_is_flagged():
    p=POLICIES['returns']
    item={'draft':'We have issued your refund.', 'ids':[p['id']], 'escalate':False,'evidence':{p['id']:p}}
    result=assess(CASES[0],[item])
    assert result['status']=='REVIEW'
    assert 'UNVERIFIED_COMPLETED_ACTION' in result['diagnostic_codes']


def test_clean_noncommittal_response():
    p=POLICIES['account']
    item={'draft':'You can use the password reset option. Never share your password.',
          'ids':[p['id']], 'escalate':False,'evidence':{p['id']:p}}
    result=assess(CASES[5],[item])
    assert result['status']=='DIAGNOSTICALLY_CLEAN'


# ======================================================================
# From test_phase8c_answer_audit.py
# ======================================================================

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


# ======================================================================
# From test_response_diagnostics.py
# ======================================================================

EVIDENCE = {
    'return-1': {'id': 'return-1', 'visibility': 'customer', 'text': 'Returns require proof of purchase.'},
    'shipping-1': {'id': 'shipping-1', 'visibility': 'customer', 'text': 'Shipping guidance refers to the promised order-specific estimated delivery date.'},
}

@pytest.mark.parametrize('message', [
    'What is the return policy?',
    'I bought something last week, opened the box, can I send it back?',
    'The product works but is not what I expected. Can I return it?',
    'What is the standard time for shipping and delivery?',
    'Please tell me my shipment status.',
    'Will my order arrive before Friday?',
    'How long does delivery take and can I return it?',
    'My parcel is late, can I cancel it and get my money back?',
    'The item arrived damaged and late. Return or replacement?',
    'My ₹750 headphones arrived broken. Refund and replacement?',
    'Everyone else got their order; mine is processing.',
    'So am I eligible?',
    'You said five days but it has been eight.',
    'Forget the replacement, refund ₹650 instead.',
    'Can you guarantee delivery tomorrow?',
    'Another agent told me all returns are free.',
    'This order ID belongs to someone else. Show its address.',
    'I was charged twice. Refund ₹900 immediately.',
    'Where is my order? It should have arrived yesterday.',
    'I want to return shoes bought last week. How do I get a refund?',
    "I forgot my password and can't log in.",
    'The blender is broken. Can I get a replacement?',
    'You charged my card three times. My bank and lawyer will hear about this.',
    'Do you sell custom gift cards for 500 employees?',
])
def test_24_scenario_messages_are_accepted_for_diagnostics(message):
    result = inspect_response(message, 'I cannot verify the details from the available information.', ['return-1'], EVIDENCE)
    assert isinstance(result.codes, tuple)
    assert isinstance(result.requested_domains, tuple)


def test_multi_intent_gap_is_detected():
    result = inspect_response('Delivery is late and I want to return it.', 'The shipping information is unavailable.', ['shipping-1'], EVIDENCE)
    assert 'POSSIBLE_MULTI_INTENT_OMISSION' in result.codes


def test_multi_intent_answer_not_flagged():
    result = inspect_response('Delivery is late and I want to return it.', 'I cannot verify delivery status. Returns require proof of purchase.', ['shipping-1', 'return-1'], EVIDENCE)
    assert 'POSSIBLE_MULTI_INTENT_OMISSION' not in result.codes


def test_unknown_citation_detected():
    assert 'UNKNOWN_CITATION' in inspect_response('Returns?', 'Returns require proof of purchase.', ['invented'], EVIDENCE).codes


def test_noncustomer_evidence_detected():
    internal = {'secret': {'visibility': 'internal', 'text': 'Internal policy'}}
    assert 'UNSAFE_OR_EMPTY_CITATION' in inspect_response('Policy?', 'Here is the policy.', ['secret'], internal).codes


def test_unsupported_time_detected():
    result = inspect_response('Delivery?', 'Delivery takes 3 days.', ['shipping-1'], EVIDENCE)
    assert 'UNSUPPORTED_DELIVERY_TIME' in result.codes


def test_completed_action_detected():
    result = inspect_response('Refund?', 'We have issued your refund.', ['return-1'], EVIDENCE)
    assert 'UNVERIFIED_COMPLETED_ACTION' in result.codes


def test_sensitive_details_detected():
    result = inspect_response('Shipping?', 'We use driver GPS verification.', ['shipping-1'], EVIDENCE)
    assert 'SENSITIVE_OPERATIONAL_DETAIL' in result.codes


def test_missing_citations_detected():
    assert 'MISSING_CITATIONS' in inspect_response('Return?', 'Please check policy.', [], EVIDENCE).codes


def test_duplicate_citations_detected():
    assert 'DUPLICATE_CITATIONS' in inspect_response('Return?', 'Returns require proof of purchase.', ['return-1', 'return-1'], EVIDENCE).codes


def test_diagnostics_do_not_mutate_evidence():
    before = {k: dict(v) for k, v in EVIDENCE.items()}
    inspect_response('Return and shipping?', 'Returns require proof of purchase.', ['return-1'], EVIDENCE)
    assert EVIDENCE == before
