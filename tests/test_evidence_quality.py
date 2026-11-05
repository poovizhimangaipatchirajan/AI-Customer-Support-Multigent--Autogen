"""Consolidated evidence quality tests.

Merged without behaviour changes from:
- test_evidence_quality_cited_text_support.py: Answer-side product/warranty claims are supported when the cited passage says them.
- test_evidence_quality_exchange.py: regression tests
- test_evidence_quality_exchange_replacement.py: Exchange wording: a bare 'replacement' is the substitute item, not a defect remedy.
- test_evidence_quality_refund_negation.py: regression tests
- test_phase2d2_evidence_quality.py: regression tests
- test_phase2d2_synthetic_source_regression.py: regression tests
- test_evidence_fallback.py: regression tests
- test_evidence_rejection_diagnostics.py: Offline diagnostic regression; no live API calls or production DB.
- test_claim_verifier.py: Offline contract tests: no network calls, no paid embeddings.
- test_tc07_domain_citations.py: regression tests
"""
import importlib.util
from pathlib import Path
from backend.services.evidence_quality import missing_citation_domains
from backend.services.grounding_guard import customer_safe_passages
from backend.services.response_diagnostics import inspect_response
from backend.services.evidence_fallback import extractive_policy_fallback
import pytest
from backend.db import store
from backend.worker import main as worker
import time
import uuid
import asyncio
import json
from backend.services.claim_verifier import verify_claims


# ======================================================================
# From test_evidence_quality_cited_text_support.py
# ======================================================================

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _load('eq_text_support', 'backend/services/evidence_quality.py').missing_citation_domains
fallback = _load('ef_text_support', 'backend/services/evidence_fallback.py').extractive_policy_fallback

SRC = '02_Returns_and_Refunds_SOP.pdf'
ELECTRONICS = {
    'id': 'e613368ccb42a62f', 'source': SRC,
    'text': ('Consumer electronics have a 7-day return window. The original box, serial numbers '
             'matching the invoice, and all accessories are required. A technical replacement or '
             'refund requires technician diagnosis and verification.')}
INITIATION = {
    'id': 'ca91ca28b962d2b7', 'source': SRC,
    'text': ('Customers can initiate a return through the orders dashboard. Photographs are required '
             'for damaged or defective products. Submission is not approval; eligibility and order '
             'ownership must be verified.')}
TC08 = ('I ordered headphones from NovaCart last week, but they arrived in the wrong size. I want to '
        'exchange them for a different colour. If the replacement is unavailable, can I get a refund '
        'instead? Please explain the eligibility conditions and what I need to do next.')


def test_verbatim_extract_of_cited_passage_needs_no_extra_product_citation():
    candidate = fallback(TC08, [ELECTRONICS, INITIATION])
    assert candidate and 'technician diagnosis' in candidate
    assert check(TC08, candidate, [ELECTRONICS, INITIATION]) == ()


def test_same_claim_without_passage_text_still_requires_product_source():
    candidate = 'A technical replacement requires technician diagnosis and verification.'
    bare = [{'source': SRC}]
    assert check(TC08, candidate, bare) == ('product',)


def test_claim_absent_from_cited_text_still_requires_product_source():
    assert check(TC08, 'Warranty repair is available.', [ELECTRONICS, INITIATION]) == ('product',)


def test_partially_supported_claims_still_require_product_source():
    resp = 'A technician diagnosis is needed and the warranty may also apply.'
    assert check(TC08, resp, [ELECTRONICS]) == ('product',)


def test_inflected_term_matches_cited_stem():
    assert check(TC08, 'A technician must diagnose the device.', [ELECTRONICS]) == ()


def test_customer_warranty_request_still_requires_product_source():
    msg = 'Is my headphone return covered by warranty?'
    assert check(msg, 'A technician diagnosis is required.', [ELECTRONICS]) == ('product',)


def test_refund_and_returns_citation_still_required():
    shipping = [{'source': '01_Shipping_and_Delivery_SOP.pdf', 'text': 'Delivery SOP text.'}]
    assert check(TC08, 'Refunds need verification.', shipping) == ('refunds', 'returns')


# ======================================================================
# From test_evidence_quality_exchange.py
# ======================================================================

spec = importlib.util.spec_from_file_location(
    'patched_evidence_quality',
    Path(__file__).resolve().parents[1] / 'backend/services/evidence_quality.py',
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
check_evidence_quality_exchange = module.missing_citation_domains
RETURNS = [{'source': '02_Returns_and_Refunds_SOP.pdf'}]
PRODUCT = [{'source': '03_Product_and_Warranty_SOP.pdf'}]


def test_wrong_size_exchange_and_conditional_refund():
    msg = ('My shoes arrived in the wrong size. I want an exchange. '
           'If the replacement size is unavailable, can I get a refund?')
    assert check_evidence_quality_exchange(msg, 'An exchange or refund is subject to verification.', RETURNS) == ()


def test_colour_replacement_as_exchange_attribute():
    msg = 'Can I exchange this for another colour? Is a replacement colour available?'
    assert check_evidence_quality_exchange(msg, 'Colour exchanges are subject to verification.', RETURNS) == ()


def test_genuine_product_replacement_still_requires_product_source():
    msg = 'My device is defective. I want a replacement and a refund.'
    assert check_evidence_quality_exchange(msg, 'Replacement eligibility requires verification.', RETURNS) == ('product',)


def test_mixed_exchange_and_warranty_still_requires_product_source():
    msg = 'Can I exchange for a replacement size and also claim warranty repair?'
    assert check_evidence_quality_exchange(msg, 'Warranty eligibility requires verification.', RETURNS) == ('product',)


def test_answer_product_policy_claim_requires_product_source():
    msg = 'Can I exchange for a replacement size?'
    assert check_evidence_quality_exchange(msg, 'A technician must diagnose the issue.', RETURNS) == ('product',)


def test_refund_citation_still_required():
    msg = 'I want an exchange. If the replacement size is unavailable, can I get a refund?'
    assert check_evidence_quality_exchange(msg, 'A refund may be possible.', PRODUCT) == ('refunds', 'returns')


def test_damage_as_refund_context_remains_suppressed():
    msg = 'My damaged item needs a refund.'
    assert check_evidence_quality_exchange(msg, 'Refunds require verification.', RETURNS) == ()


def test_declined_refund_does_not_add_refunds_domain():
    msg = 'I want to exchange for a replacement size. I do not want a refund.'
    assert check_evidence_quality_exchange(msg, 'An exchange is subject to verification.', RETURNS) == ()


# ======================================================================
# From test_evidence_quality_exchange_replacement.py
# ======================================================================

spec_evidence_quality_exchange_replacement = importlib.util.spec_from_file_location(
    'eq_exchange_replacement',
    Path(__file__).resolve().parents[1] / 'backend/services/evidence_quality.py',
)
module_evidence_quality_exchange_replacement = importlib.util.module_from_spec(spec_evidence_quality_exchange_replacement)
spec_evidence_quality_exchange_replacement.loader.exec_module(module_evidence_quality_exchange_replacement)
check_evidence_quality_exchange_replacement = module_evidence_quality_exchange_replacement.missing_citation_domains
RETURNS_evidence_quality_exchange_replacement = [{'source': '02_Returns_and_Refunds_SOP.pdf'}]
PRODUCT_evidence_quality_exchange_replacement = [{'source': '03_Product_and_Warranty_SOP.pdf'}]
SHIPPING = [{'source': '01_Shipping_and_Delivery_SOP.pdf'}]
ANSWER = 'An exchange or refund is subject to verification.'

TC08_evidence_quality_exchange_replacement = ('I ordered headphones from NovaCart last week, but they arrived in the wrong size. '
        'I want to exchange them for a different colour. If the replacement is unavailable, '
        'can I get a refund instead? Please explain the eligibility conditions and what I need to do next.')


def test_tc08_wording_needs_only_returns_and_refunds_evidence():
    assert check_evidence_quality_exchange_replacement(TC08_evidence_quality_exchange_replacement, ANSWER, RETURNS_evidence_quality_exchange_replacement) == ()


def test_tc08_wording_with_shipping_only_still_rejected_without_product_demand():
    assert check_evidence_quality_exchange_replacement(TC08_evidence_quality_exchange_replacement, ANSWER, SHIPPING) == ('refunds', 'returns')


def test_bare_replacement_in_exchange_with_different_item_noun():
    msg = 'I want to swap these for another size. If the replacement is out of stock, refund me.'
    assert check_evidence_quality_exchange_replacement(msg, ANSWER, RETURNS_evidence_quality_exchange_replacement) == ()


def test_exchange_plus_fault_signal_still_requires_product_source():
    msg = 'I want to exchange this for a replacement. It stopped working after two days.'
    assert check_evidence_quality_exchange_replacement(msg, ANSWER, RETURNS_evidence_quality_exchange_replacement) == ('product',)


def test_exchange_plus_not_working_phrase_still_requires_product_source():
    msg = 'Please exchange it, the replacement should be a new unit as it is not working.'
    assert check_evidence_quality_exchange_replacement(msg, ANSWER, RETURNS_evidence_quality_exchange_replacement) == ('product',)


def test_exchange_plus_warranty_still_requires_product_source():
    msg = 'Can I exchange it, or get a replacement under warranty?'
    assert check_evidence_quality_exchange_replacement(msg, ANSWER, RETURNS_evidence_quality_exchange_replacement) == ('product',)


def test_replacement_without_exchange_context_still_requires_product_source():
    assert check_evidence_quality_exchange_replacement('I want a replacement for my headphones.', ANSWER, RETURNS_evidence_quality_exchange_replacement) == ('product',)


def test_product_claim_in_answer_still_requires_product_source():
    assert check_evidence_quality_exchange_replacement(TC08_evidence_quality_exchange_replacement, 'A technician must diagnose the issue.', RETURNS_evidence_quality_exchange_replacement) == ('product',)


def test_product_source_satisfies_genuine_replacement_request():
    msg = 'My headphones are defective, I want a replacement.'
    assert check_evidence_quality_exchange_replacement(msg, 'Replacement eligibility requires verification.', PRODUCT_evidence_quality_exchange_replacement) == ()


# ======================================================================
# From test_evidence_quality_refund_negation.py
# ======================================================================

SHIPPING_evidence_quality_refund_negation = [{"source": "01_Shipping_and_Delivery_SOP.pdf"}]


def missing(message, evidence=SHIPPING_evidence_quality_refund_negation):
    return missing_citation_domains(message, "Contact support for investigation.", evidence)


def test_original_customer_ticket_not_refund_request():
    message = ("My package shows delivered, but I have not received it. "
               "I checked the mailroom, front desk, and household members. "
               "Please explain the next steps for investigation. "
               "I am not requesting a refund at this stage.")
    assert missing(message) == ()


def test_actual_refund_request_still_requires_refund_evidence():
    assert missing("My parcel is missing. I want a refund.") == ("refunds",)


def test_refund_refusal_and_separate_policy_question_still_require_evidence():
    assert missing("I am not requesting a refund. What is your refund policy?") == ("refunds",)


def test_refusal_variants():
    for phrase in ("I don't want a refund", "I do not need a refund", "No refund needed", "I am not asking for a refund"):
        assert missing("My parcel was delivered incorrectly. " + phrase) == (), phrase


def test_multidomain_request_still_requires_missing_domain():
    assert missing("My order is missing. What is your warranty policy?") == ("product",)


def test_cited_refund_policy_is_accepted_for_actual_refund_request():
    evidence = SHIPPING_evidence_quality_refund_negation + [{"source": "02_Returns_and_Refunds_SOP.pdf"}]
    assert missing("My parcel is missing and I request a refund", evidence) == ()


# ======================================================================
# From test_phase2d2_evidence_quality.py
# ======================================================================

def p(id, source, page, text):
    return dict(id=id, source=source, page=page, visibility='customer', text=text)


SHIPPING_phase2d2_evidence_quality = p('s1', '01_Shipping_and_Delivery_SOP.pdf', 1,
             'Delivery EDD depends on order details and delivery location.')
RETURNS_phase2d2_evidence_quality = p('r1', '02_Returns_and_Refunds_SOP.pdf', 1,
            'Fashion & Apparel 14 Days. Return requests may be initiated in orders dashboard.')


def test_shipping_and_returns_requires_both_sources():
    result = inspect_response('How long does delivery take and can I return it?',
        'Delivery depends on order details. Returns depend on category.', ['r1'], {'r1': RETURNS_phase2d2_evidence_quality})
    assert 'MISSING_DOMAIN_CITATION' in result.codes


def test_both_sources_clear_domain_gap():
    result = inspect_response('How long does delivery take and can I return it?',
        'Delivery depends on order details. Returns depend on category.',
        ['s1', 'r1'], {'s1': SHIPPING_phase2d2_evidence_quality, 'r1': RETURNS_phase2d2_evidence_quality})
    assert 'MISSING_DOMAIN_CITATION' not in result.codes


def test_general_return_policy_only_needs_return_source():
    result = inspect_response('What is your return policy?',
        'Fashion and apparel returns are available subject to eligibility.', ['r1'], {'r1': RETURNS_phase2d2_evidence_quality})
    assert 'MISSING_DOMAIN_CITATION' not in result.codes


def test_safe_abstention_not_forced_to_cite_every_domain():
    result = inspect_response('How long is shipping and what is the return policy?',
        'I cannot verify shipping information. Returns depend on category.', ['r1'], {'r1': RETURNS_phase2d2_evidence_quality})
    assert 'MISSING_DOMAIN_CITATION' not in result.codes


def test_addendum_not_customer_policy_evidence():
    addendum = p('a', '02_Returns_and_Refunds_SOP.pdf', 5,
        'FIXFLOW AI | IMPLEMENTATION GUIDANCE ADDENDUM | Not a policy amendment. '
        'Expected customer-facing answer: fashion returns in 14 days.')
    assert customer_safe_passages([addendum, RETURNS_phase2d2_evidence_quality]) == [RETURNS_phase2d2_evidence_quality]
    result = inspect_response('What is the return policy?', 'Return windows vary.', ['a'], {'a': addendum})
    assert 'UNSAFE_OR_EMPTY_CITATION' in result.codes


def test_existing_internal_pages_still_blocked():
    internal = p('internal', '03_Product_and_Warranty_SOP.pdf', 4,
        'We have dispatched a replacement already.')
    assert customer_safe_passages([internal]) == []


def test_no_mutation_of_original_evidence():
    original = RETURNS_phase2d2_evidence_quality.copy()
    customer_safe_passages([RETURNS_phase2d2_evidence_quality])
    assert RETURNS_phase2d2_evidence_quality == original


# ======================================================================
# From test_phase2d2_synthetic_source_regression.py
# ======================================================================

def test_synthetic_account_fixture_has_correct_domain_citation():
    p = {'id': 'test-account', 'source': 'FICTIONAL_ACCOUNT_POLICY.pdf',
         'page': 1, 'visibility': 'customer',
         'text': 'Customers who cannot log in may use the password reset option.'}
    result = inspect_response(
        'I forgot my password and cannot log in.',
        'You can use the password reset option. Never share your password.',
        [p['id']], {p['id']: p})
    assert 'MISSING_DOMAIN_CITATION' not in result.codes
    assert result.codes == ()


def test_unknown_policy_source_does_not_satisfy_domain_requirement():
    p = {'id': 'unknown', 'source': 'UNVERIFIED_POLICY.pdf',
         'page': 1, 'visibility': 'customer',
         'text': 'Password reset option.'}
    result = inspect_response('I forgot my password.',
        'Use the password reset option.', [p['id']], {p['id']: p})
    assert 'MISSING_DOMAIN_CITATION' in result.codes


# ======================================================================
# From test_evidence_fallback.py
# ======================================================================

FASHION = {"id": "p1", "text": "Fashion and apparel have a 14-day return window if unworn and unwashed, with brand tags intact and original packaging. Eligible remedies include refund to source, wallet refund, or size/color exchange, subject to verification."}


def test_extracts_complete_original_policy_without_purchase_anchor():
    draft = extractive_policy_fallback("I want to return the shoes I bought last week. How do I get a refund?", [FASHION])
    assert FASHION["text"] in draft
    assert "of purchase" not in draft
    assert "tags intact" in draft
    assert "subject to verification" in draft


def test_no_evidence_means_no_fallback():
    assert extractive_policy_fallback("return my shoes", []) is None


def test_no_query_overlap_means_no_fallback():
    assert extractive_policy_fallback("astronomy nebula", [FASHION]) is None


def test_does_not_truncate_policy_conditions():
    assert extractive_policy_fallback("return", [{"text": "return " + "x" * 2000}]) is None


# ======================================================================
# From test_evidence_rejection_diagnostics.py
# ======================================================================

def seed_ticket():
    uid, tid, jid = (str(uuid.uuid4()) for _ in range(3))
    now = time.time()
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid, f'{uid}@example.test', 'test-hash', 'customer', 1))
        c.execute('INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at,version) VALUES (?,?,?,?,?,?,?,?,?)',
                  (tid, uid, 'Shipping policy', 'Shipping question', None, 'QUEUED', '', now, 0))
        c.execute('INSERT INTO jobs(id,ticket_id,status,created_at,input_version,attempts) VALUES (?,?,?,?,?,?)',
                  (jid, tid, 'QUEUED', now, 0, 0))
    return tid, jid

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'diagnostics.sqlite3'))
    store.init()

@pytest.mark.parametrize('ids,evidence,expected', [
    ([], {}, 'MISSING_CITATIONS'),
    (['fake'], {}, 'INVALID_CITATIONS'),
    (['x'], {'x': {'id': 'x', 'source': '06_Escalations_and_Soft_Skills_SOP.pdf', 'visibility': 'internal'}}, 'NON_CUSTOMER_EVIDENCE'),
])
def test_rejection_reason_codes(isolated_db, capsys, ids, evidence, expected):
    tid, jid = seed_ticket()
    row = worker.claim()
    worker.persist(jid, tid, 'summary', 'A safe generic reply.', ids, False, evidence)
    assert expected in capsys.readouterr().out
    with store.connect() as c:
        result = c.execute('SELECT citations, requires_review FROM ai_results WHERE job_id=?', (jid,)).fetchone()
    assert result['citations'] == '[]'
    assert result['requires_review'] == 1

def test_valid_evidence_keeps_citations(isolated_db, capsys):
    tid, jid = seed_ticket()
    worker.claim()
    evidence = {'x': {'id': 'x', 'source': '01_Shipping_and_Delivery_SOP.pdf', 'visibility': 'customer', 'text': 'The customer can contact support about delivery.'}}
    worker.persist(jid, tid, 'summary', 'The customer can contact support about delivery.', ['x'], False, evidence)
    assert 'EVIDENCE_VALIDATION: ACCEPTED' in capsys.readouterr().out
    with store.connect() as c:
        result = c.execute('SELECT citations, requires_review FROM ai_results WHERE job_id=?', (jid,)).fetchone()
    assert result['citations'] == '["x"]'
    assert result['requires_review'] == 0

def test_sensitive_draft_logs_reason_and_fails_closed(isolated_db, capsys):
    tid, jid = seed_ticket()
    worker.claim()
    evidence = {'x': {'id': 'x', 'source': '01_Shipping_and_Delivery_SOP.pdf', 'visibility': 'customer', 'text': 'Shipping support available.'}}
    worker.persist(jid, tid, 'summary', 'We conduct driver GPS verification.', ['x'], False, evidence)
    assert 'SENSITIVE_OPERATIONAL_DETAIL' in capsys.readouterr().out
    with store.connect() as c:
        result = c.execute('SELECT citations, requires_review FROM ai_results WHERE job_id=?', (jid,)).fetchone()
    assert result['citations'] == '[]'
    assert result['requires_review'] == 1


# ======================================================================
# From test_claim_verifier.py
# ======================================================================

def run(draft, evidence, answer, customer_message="How does this policy work?"):
    async def evaluator(system, payload):
        assert "DIRECTLY entail" in system
        assert json.loads(payload)["cited_policy_passages"] == evidence
        return json.dumps(answer)
    return asyncio.run(verify_claims(customer_message, draft, evidence, evaluator))


def test_supported_claim_with_valid_provenance():
    passages = [{"id": "policy-a", "text": "Category A has a return window of N days."}]
    result = run("Category A has a return window of N days.", passages,
                 {"complete": True, "claims": [{"claim": "Category A has a return window of N days", "verdict": "supported", "evidence_ids": ["policy-a"]}]})
    assert result.accepted


def test_unsupported_anchor_rejected_regardless_of_wording():
    evidence = [{"id": "a", "text": "Category A has a return window of N days."}]
    for draft in ("Returns must be requested N days after purchase.",
                  "The return window begins on the purchase date and lasts N days."):
        result = run(draft, evidence, {"complete": True, "claims": [{"claim": draft, "verdict": "unsupported", "evidence_ids": []}]})
        assert not result.accepted and result.reason == "CLAIM_NOT_ENTAILED"


def test_uncertainty_and_invalid_citations_fail_closed():
    evidence = [{"id": "a", "text": "Policy statement."}]
    for verdict, ids, expected in [("uncertain", [], "CLAIM_NOT_ENTAILED"),
                                   ("supported", ["fabricated"], "INVALID_EVIDENCE_REFERENCE"),
                                   ("supported", [], "UNATTRIBUTED_CLAIM")]:
        result = run("A factual claim", evidence, {"complete": True, "claims": [{"claim": "A factual claim", "verdict": verdict, "evidence_ids": ids}]})
        assert not result.accepted and result.reason == expected


def test_incomplete_output_fails_closed():
    assert not run("Claim", [{"id": "a", "text": "Evidence"}], {"complete": False, "claims": []}).accepted


def test_verifier_exception_fails_closed():
    async def broken(system, payload):
        raise RuntimeError("network unavailable")
    result = asyncio.run(verify_claims("question", "draft", [{"id": "a", "text": "evidence"}], broken))
    assert not result.accepted and result.reason == "VERIFIER_UNAVAILABLE"


# ======================================================================
# From test_tc07_domain_citations.py
# ======================================================================

RETURNS_tc07_domain_citations = [{'source': '02_Returns_and_Refunds_SOP.pdf'}]
PRODUCT_tc07_domain_citations = [{'source': '03_Product_and_Warranty_SOP.pdf'}]


def check_tc07_domain_citations(message, response, sources, expected):
    assert missing_citation_domains(message, response, sources) == expected


def test_damaged_refund_return_sop_sufficient():
    check_tc07_domain_citations('I received a damaged USB-C cable and want a refund of ₹299.',
          'Initiate a return via the orders dashboard and submit photos.', RETURNS_tc07_domain_citations, ())


def test_broken_return_sop_sufficient():
    check_tc07_domain_citations('My blender is broken. How can I return it?',
          'Use the returns process with photos.', RETURNS_tc07_domain_citations, ())


def test_defective_refund_sop_sufficient():
    check_tc07_domain_citations('Defective headphones, please refund ₹300.',
          'Refund eligibility requires verification.', RETURNS_tc07_domain_citations, ())


def test_replacement_requires_product_sop():
    check_tc07_domain_citations('My blender is broken. Can I get a replacement?',
          'You may request a replacement.', RETURNS_tc07_domain_citations, ('product',))


def test_warranty_requires_product_sop():
    check_tc07_domain_citations('The headphones are damaged. What is the warranty and refund policy?',
          'Return eligibility needs verification.', RETURNS_tc07_domain_citations, ('product',))


def test_technical_diagnosis_claim_requires_product_sop():
    check_tc07_domain_citations('Damaged headphones, want a refund.',
          'A technician diagnosis is mandatory.', RETURNS_tc07_domain_citations, ('product',))


def test_independent_shipping_domain_preserved():
    check_tc07_domain_citations('Damaged cable: how do I get a refund and track delivery?',
          'Submit photos and check your tracking.', RETURNS_tc07_domain_citations, ('shipping',))


def test_product_only_remains_product():
    check_tc07_domain_citations('My blender is broken.', 'Please contact support.', RETURNS_tc07_domain_citations, ('product',))


def test_both_sources_cover_both_domains():
    check_tc07_domain_citations('Damaged headphones: can I get a replacement and refund?',
          'Eligibility needs verification.', RETURNS_tc07_domain_citations + PRODUCT_tc07_domain_citations, ())


def test_missing_refund_domain_still_rejected():
    check_tc07_domain_citations('Damaged item, I want a refund.',
          'Please request a return.', PRODUCT_tc07_domain_citations, ('refunds',))


def test_explicit_refund_refusal_preserved():
    check_tc07_domain_citations('I do not want a refund. My blender is broken.',
          'Please contact support.', RETURNS_tc07_domain_citations, ('product',))
