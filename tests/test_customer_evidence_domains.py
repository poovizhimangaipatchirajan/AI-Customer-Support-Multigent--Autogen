"""Consolidated customer evidence domains tests.

Merged without behaviour changes from:
- test_cross_category_smoke.py: Small deterministic smoke suite; no LLM, network, or DB access.
- test_legacy_shipping_warranty_gate.py: Offline, targeted checks for customer-only legacy search gates.
- test_shipping_warranty_customer_evidence.py: Offline targeted regression tests for curated shipping/warranty evidence.
- test_mfa_customer_evidence.py: Focused regression tests for MFA evidence isolation in both retrievers.
- test_password_recovery_approved_evidence.py: Account-recovery guidance is safe, cited, and available in both retrieval modes.
- test_order_tracking_intent_regression.py: Focused offline shipping-intent regressions; no FAISS/LLM required.
- test_phase2d_real_sop.py: regression tests
"""
import pytest
from backend.services.grounding_guard import check_response_grounding, check_customer_visibility, customer_safe_passages
from backend.rag.hybrid import HybridRetriever
from backend.rag.customer_policy import is_approved_customer_passage
from backend.rag.customer_policy import approved_topic_guidance, is_approved_customer_passage, approved_mfa_guidance
from backend.rag.customer_policy import approved_mfa_guidance, is_approved_customer_passage
from backend.rag.customer_index import CustomerHybridRetriever
from backend.services.help_center import answer_question, _unverified_order_tracking
from eval.phase2d_real_sop import load_cases, select_cases, assess


# ======================================================================
# From test_cross_category_smoke.py
# ======================================================================

SHIPPING = {"id": "shipping-1", "source": "01_Shipping_and_Delivery_SOP.pdf", "page": 1,
            "visibility": "customer", "text": "An order has a promised Estimated Delivery Date (EDD). Merchant dispatch manifests are generated within 48 business hours."}
RETURNS = {"id": "return-1", "source": "02_Returns_and_Refunds_SOP.pdf", "page": 1,
           "visibility": "customer", "text": "Returns are subject to eligibility checks."}
OTHER = [
    ("03_Product_and_Warranty_SOP.pdf", "Product warranty terms apply."),
    ("04_Account_and_Login_SOP.pdf", "Account verification is required."),
    ("05_Payments_and_Billing_SOP.pdf", "Payment disputes need investigation."),
]

@pytest.mark.parametrize("draft", [
    "NovaCart's standard delivery timeframe varies by fulfillment channel: FBN and DMF.",
    "Your order will arrive within 48 business hours.",
])
def test_shipping_misrepresentation_blocked(draft):
    assert check_response_grounding(draft, [SHIPPING]) is not None

@pytest.mark.parametrize("draft", [
    "The SOP refers to a promised Estimated Delivery Date but does not specify a universal delivery duration.",
    "The 48-business-hour dispatch-manifest requirement is not a delivery timeframe.",
    "The policy does not provide a location-based delivery schedule.",
])
def test_accurate_shipping_limitations_not_blocked(draft):
    assert check_response_grounding(draft, [SHIPPING]) is None

@pytest.mark.parametrize("source,body", OTHER)
def test_unrelated_customer_policy_not_removed(source, body):
    p = {"id": source, "source": source, "page": 1, "visibility": "customer", "text": body}
    assert customer_safe_passages([p]) == [p]

def test_returns_evidence_unchanged():
    assert customer_safe_passages([RETURNS]) == [RETURNS]
    assert check_response_grounding("Returns are subject to eligibility checks.", [RETURNS]) is None

def test_internal_shipping_page_four_excluded():
    p = {**SHIPPING, "page": 4, "text": "Compensation and replacement resolution matrix."}
    assert customer_safe_passages([p]) == []

def test_noncustomer_passages_excluded():
    assert customer_safe_passages([{**RETURNS, "visibility": "internal"}]) == []

def test_unverified_completed_refund_still_blocked():
    assert check_response_grounding("We have issued your refund.", [RETURNS]) == "UNVERIFIED_COMPLETED_ACTION"

def test_internal_ops_still_blocked():
    assert check_customer_visibility("We ran a geofence audit.") == "SENSITIVE_OPERATIONAL_DETAIL"


# ======================================================================
# From test_legacy_shipping_warranty_gate.py
# ======================================================================

@pytest.fixture
def retriever():
    # Exercise the customer-safe topic gate without constructing indexes or
    # contacting an embeddings provider. Semantic ranking is tested separately.
    from backend.rag.customer_policy import approved_topic_guidance
    from backend.rag.hybrid import citation_metadata
    retriever = object.__new__(HybridRetriever)

    def offline_approved_search(query, k):
        passages = approved_topic_guidance(query)
        if passages is None:
            return []
        return [dict(p, **citation_metadata(p)) for p in passages[:k]
                if is_approved_customer_passage(p)]

    retriever._approved_semantic_search = offline_approved_search
    return retriever


@pytest.mark.parametrize('question,source,count', [
    ('What is the standard delivery timeframe?', None, 0),
    ('What is NovaCart\'s general policy for packages marked as delivered but not received?',
     '01_Shipping_and_Delivery_SOP.pdf', 1),
    ('What does the standard product warranty cover?',
     '03_Product_and_Warranty_SOP.pdf', 1),
    ('What is MFA Reset Authorization Hierarchy?', None, 0),
    ('How can a customer reset MFA?', '04_Account_and_Login_SOP.pdf', 1),
])
def test_customer_safe_gates(retriever, question, source, count):
    result = retriever.search(question, k=5)
    assert len(result) == count
    for passage in result:
        assert is_approved_customer_passage(passage)
        assert passage['source'] == source
        assert passage['citation_label'] == f"{source}, page {passage['page']}"
        assert passage['department']


def test_shipping_no_automatic_refund(retriever):
    result = retriever.search('My package says delivered but I have not received it')
    assert len(result) == 1
    assert 'not automatically guaranteed' in result[0]['text']


def test_standard_warranty_excludes_novacare(retriever):
    result = retriever.search('What does the standard product warranty cover?')
    assert len(result) == 1
    assert '30-day platform assurance' in result[0]['text']
    assert 'NovaCare' not in result[0]['text']


def test_internal_access_still_blocked(retriever):
    with pytest.raises(PermissionError):
        retriever.search('What does warranty cover?', include_internal=True)


def test_empty_and_invalid_queries(retriever):
    assert retriever.search('') == []
    assert retriever.search('What is the standard delivery timeframe?', k=0) == []


# ======================================================================
# From test_shipping_warranty_customer_evidence.py
# ======================================================================

@pytest.mark.parametrize("query", [
    "What is the standard delivery timeframe?",
    "How long does shipping take?",
    "What is the standard delivery time?",
])
def test_no_invented_delivery_sla(query):
    assert approved_topic_guidance(query) == []

@pytest.mark.parametrize("query", [
    "What is NovaCart's general policy for packages marked as delivered but not received?",
    "My order is marked delivered but I have not received it",
])
def test_missing_delivery_guidance_is_customer_safe(query):
    evidence = approved_topic_guidance(query)
    assert len(evidence) == 1
    assert evidence[0]["source"] == "01_Shipping_and_Delivery_SOP.pdf"
    assert evidence[0]["page"] == 3
    assert all(is_approved_customer_passage(p) for p in evidence)
    text = evidence[0]["text"].lower()
    assert "front desk" in text and "verification" in text
    assert "not automatically guaranteed" in text
    assert "geofence" not in text and "gps" not in text

@pytest.mark.parametrize("query", [
    "What does the standard product warranty cover?",
    "What is included in the standard warranty?",
])
def test_standard_warranty_excludes_optional_cover(query):
    evidence = approved_topic_guidance(query)
    assert len(evidence) == 1
    assert evidence[0]["source"] == "03_Product_and_Warranty_SOP.pdf"
    assert "manufacturing defects" in evidence[0]["text"]
    assert "NovaCare" not in evidence[0]["text"]
    assert is_approved_customer_passage(evidence[0])

def test_mfa_still_protected():
    assert approved_mfa_guidance("What is MFA Reset Authorization Hierarchy?") == []
    assert len(approved_mfa_guidance("How can a customer reset MFA?")) == 1

def test_unrelated_query_is_not_forced_into_topic():
    assert approved_topic_guidance("How can I request a return?") is None

def test_approval_identity_cannot_be_forged():
    evidence = approved_topic_guidance("Marked delivered but not received")
    assert evidence
    tampered = dict(evidence[0], text="A guaranteed refund is available")
    assert not is_approved_customer_passage(tampered)


# ======================================================================
# From test_mfa_customer_evidence.py
# ======================================================================

@pytest.mark.parametrize("query", [
    "What is MFA Reset Authorization Hierarchy?",
    "Describe the MFA approval chain",
])
def test_internal_hierarchy_has_no_customer_evidence(query):
    assert approved_mfa_guidance(query) == []

@pytest.mark.parametrize("query", [
    "How can a customer reset MFA?",
    "What should I do if I lose access to my authenticator?",
])
def test_customer_recovery_uses_only_approved_identity_guidance(query):
    passages = approved_mfa_guidance(query)
    assert len(passages) == 1
    assert all(is_approved_customer_passage(p) for p in passages)
    assert all(p['source'] == '04_Account_and_Login_SOP.pdf' for p in passages)
    assert 'identity verification' in passages[0]['text'].lower()
    assert 'reset steps' not in passages[0]['text'].lower()

def test_unrelated_questions_continue_to_normal_retrieval():
    assert approved_mfa_guidance('What is the warranty coverage?') is None

@pytest.mark.parametrize('retriever_cls', [HybridRetriever, CustomerHybridRetriever])
@pytest.mark.parametrize('query,expected', [
    ('What is MFA Reset Authorization Hierarchy?', 0),
    ('How can a customer reset MFA?', 1),
    ('What should I do if I lose access to my authenticator?', 1),
])
def test_both_modes_handle_mfa_without_embeddings(retriever_cls, query, expected):
    # The MFA evidence gate runs before FAISS/embedding calls in both modes.
    retriever = object.__new__(retriever_cls)
    results = retriever.search(query, k=5)
    assert len(results) == expected
    assert all(is_approved_customer_passage(p) for p in results)

@pytest.mark.parametrize('retriever_cls', [HybridRetriever, CustomerHybridRetriever])
def test_internal_flag_is_still_rejected(retriever_cls):
    retriever = object.__new__(retriever_cls)
    with pytest.raises(PermissionError):
        retriever.search('How do I reset MFA?', include_internal=True)


# ======================================================================
# From test_password_recovery_approved_evidence.py
# ======================================================================

@pytest.mark.parametrize('query', [
    "I forgot my password and can't log in.",
    "I forgot my password and can't login.",
    "I need to reset my password",
])
@pytest.mark.parametrize('cls', [HybridRetriever, CustomerHybridRetriever])
def test_account_recovery_uses_approved_identity_guidance_without_embeddings(query, cls):
    retriever = object.__new__(cls)
    passages = retriever.search(query, k=4)
    assert len(passages) == 1
    assert is_approved_customer_passage(passages[0])
    assert passages[0]['source'] == '04_Account_and_Login_SOP.pdf'
    assert 'identity verification' in passages[0]['text'].lower()

@pytest.mark.parametrize('query', [
    'Tell me a joke about a spaceship',
    'What is the warranty coverage?',
])
def test_unrelated_queries_not_account_shortcut(query):
    assert approved_mfa_guidance(query) is None

def test_internal_mfa_hierarchy_stays_blocked():
    assert approved_mfa_guidance('What is MFA Reset Authorization Hierarchy?') == []


# ======================================================================
# From test_order_tracking_intent_regression.py
# ======================================================================

class PoisonRetriever:
    def search(self, query, k=4):
        raise AssertionError("Unverified personal tracking must not use irrelevant RAG")


def test_overdue_order_does_not_claim_delivered_or_cite_irrelevant_policy():
    result = answer_question("Where is my order? It was supposed to arrive yesterday.", search=PoisonRetriever())
    assert result["status"] == "ticket_required"
    assert result["sources"] == []
    assert "order ID" in result["answer"]
    assert "verify" in result["answer"]
    assert "marked delivered" not in result["answer"].lower()


def test_tracking_request_is_not_treated_as_delivered():
    result = answer_question("Track my package; it is delayed", search=PoisonRetriever())
    assert result["status"] == "ticket_required"
    assert result["sources"] == []


def test_explicit_delivered_but_missing_keeps_existing_policy_path():
    assert not _unverified_order_tracking("My package says delivered but I haven't received it")
    assert not _unverified_order_tracking("My order was marked delivered but it never arrived")


def test_general_shipping_policy_not_mistaken_for_personal_tracking():
    assert not _unverified_order_tracking("What is NovaCart's shipping policy?")
    assert not _unverified_order_tracking("What is the standard delivery timeframe?")


def test_unrelated_account_and_replacement_not_intercepted():
    assert not _unverified_order_tracking("I forgot my password and can't log in")
    assert not _unverified_order_tracking("My blender is broken, can I get a replacement?")


# ======================================================================
# From test_phase2d_real_sop.py
# ======================================================================

def test_24_unique_cases():
    cases = load_cases()
    assert len(cases) == 24
    assert {c['id'] for c in cases} == {f'S{i:02}' for i in range(1, 25)}


def test_default_selection_order():
    assert [x['id'] for x in select_cases(load_cases(), 'S10,S01,S07')] == ['S01','S07','S10']


def test_all_selection():
    assert len(select_cases(load_cases(), 'all')) == 24


@pytest.mark.parametrize('value', ['', 'S99', 'S01,S99'])
def test_invalid_selection(value):
    with pytest.raises(ValueError):
        select_cases(load_cases(), value)


def test_missing_outcome_is_not_clean():
    result = assess(load_cases()[0], [], [], False)
    assert result['status'] == 'NO_OUTCOME'


def test_missing_prior_context_requires_review():
    case = next(x for x in load_cases() if x['id'] == 'S12')
    captured = [{'draft':'Please clarify which order you mean.', 'ids':['p'],
                 'escalate':False, 'evidence':{'p':{'id':'p','visibility':'customer','text':'Eligibility requires order verification.','source':'POLICY.pdf'}}}]
    result = assess(case, captured, [], True)
    assert result['status'] == 'REVIEW'
    assert 'MISSING_PRIOR_CONVERSATION_CONTEXT' in result['diagnostic_codes']


def test_payment_dispute_without_escalation_is_flagged():
    case = next(x for x in load_cases() if x['id'] == 'S18')
    captured = [{'draft':'Please contact support for the disputed charge.', 'ids':['p'],
                 'escalate':False, 'evidence':{'p':{'id':'p','visibility':'customer','text':'Payment disputes need human review.','source':'POLICY.pdf'}}}]
    result = assess(case, captured, [], True)
    assert 'MANDATORY_HUMAN_REVIEW_NOT_REQUESTED' in result['diagnostic_codes']


def test_no_citation_is_not_clean():
    case = load_cases()[0]
    captured = [{'draft':'You may return items.', 'ids':[], 'escalate':False, 'evidence':{}}]
    result = assess(case, captured, [], True)
    assert 'MISSING_CITATIONS' in result['diagnostic_codes']
