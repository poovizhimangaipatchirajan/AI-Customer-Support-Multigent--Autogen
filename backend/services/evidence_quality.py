"""Conservative read-only policy source/domain checks.

This is diagnostic metadata, NOT an authorization decision or semantic verifier.
"""
import re

SOURCE_DOMAINS = {
    '01_Shipping_and_Delivery_SOP.pdf': frozenset({'shipping'}),
    '02_Returns_and_Refunds_SOP.pdf': frozenset({'returns', 'refunds'}),
    '03_Product_and_Warranty_SOP.pdf': frozenset({'product'}),
    '04_Account_and_Login_SOP.pdf': frozenset({'account'}),
    # Phase 2C explicitly labelled synthetic test fixtures (never real policy).
    'FICTIONAL_SHIPPING_POLICY.pdf': frozenset({'shipping'}),
    'FICTIONAL_RETURNS_POLICY.pdf': frozenset({'returns', 'refunds'}),
    'FICTIONAL_PRODUCT_POLICY.pdf': frozenset({'product'}),
    'FICTIONAL_ACCOUNT_POLICY.pdf': frozenset({'account'}),
    'FICTIONAL_BILLING_POLICY.pdf': frozenset({'refunds'}),
}

# Only assert source-domain requirements for explicit, unambiguous customer intents.
EXPLICIT_INTENTS = {
    'shipping': re.compile(r'\b(?:deliver(?:y|ed)?|shipping|shipment|tracking|edd|arriv(?:e|al))\b', re.I),
    'returns': re.compile(r'\b(?:return|send it back|exchange)\b', re.I),
    'refunds': re.compile(r'\b(?:refund|money back|reimburse)\b', re.I),
    'product': re.compile(r'\b(?:broken|defect(?:ive)?|damaged|replacement|warranty)\b', re.I),
    'account': re.compile(r'\b(?:password|log ?in|sign ?in|account access)\b', re.I),
}

# A narrowly scoped refusal of a remedy is not a request for that policy.
# Remove only the refusal phrase, never the whole sentence: another explicit
# refund question in the same message must still require refund citations.
_DECLINED_REFUND = re.compile(
    r"\b(?:i\s+)?(?:(?:am|was)\s+)?(?:not\s+(?:requesting|asking\s+for|seeking|wanting)|"
    r"(?:do\s+not|don't)\s+(?:want|need|request)|"
    r"(?:am\s+)?not\s+looking\s+for)\s+"
    r"(?:a\s+|an\s+|any\s+)?(?:refund|reimbursement|money\s+back)\b"
    r"|\bno\s+refund\s+(?:needed|requested|wanted)\b",
    re.I,
)


def _policy_intent_text(customer_message):
    """Exclude explicit refund refusals without dropping independent intents."""
    return _DECLINED_REFUND.sub(' ', customer_message)


# Damage can describe the reason for a return/refund without asking for
# separate product/warranty policy. Explicit product remedies remain independent.
_PRODUCT_SPECIFIC_REQUEST = re.compile(
    r'\b(?:replacement|replace|warranty|repair|troubleshoot(?:ing)?|'
    r'technician|diagnos(?:is|e)|technical support)\b', re.I,
)
_PRODUCT_POLICY_CLAIM = re.compile(
    r'\b(?:warranty|technician|diagnos(?:is|e)|repair|troubleshoot(?:ing)?)\b', re.I,
)

# A replacement size/colour is an exchange attribute, not by itself a
# separate product/warranty remedy. Strip only that phrase so other product
# requests in the same message remain visible to the domain checker.
_EXCHANGE_ATTRIBUTE = re.compile(
    r"\b(?:replacement\s+(?:size|colou?r)|"
    r"(?:size|colou?r)\s+replacement)\b", re.I,
)

# In an exchange request, a bare "replacement" usually names the substitute
# item ("if the replacement is unavailable"), not a defect remedy. It stays a
# product/warranty request whenever the message also carries a fault or
# product-policy signal. The signal list is deliberately broader than the
# 'product' intent pattern so ambiguous wording keeps requiring product
# evidence rather than silently dropping it.
_EXCHANGE_CONTEXT = re.compile(
    r"\b(?:exchang(?:e|es|ed|ing)|swap(?:ped|ping)?|"
    r"(?:wrong|different|another|other)\s+(?:size|colou?r))\b", re.I,
)
_FAULT_SIGNAL = re.compile(
    r"\b(?:broken|defect(?:ive)?|damaged|faulty|malfunction(?:s|ing|ed)?|"
    r"not\s+working|stopped\s+working|(?:does(?:\s*not|n'?t)|do(?:\s*not|n'?t))\s+work|"
    r"dead(?:\s+on\s+arrival)?|doa|warranty|repair|troubleshoot(?:ing)?|"
    r"technician|diagnos(?:is|e)|technical\s+support)\b", re.I,
)
_BARE_REPLACEMENT = re.compile(r"\breplacements?\b", re.I)

ABSTENTION = re.compile(r'\b(?:cannot (?:verify|confirm|find)|unable to (?:verify|confirm|find)|no (?:reliable|relevant|verified) (?:policy|evidence)|human review|specialist review)\b', re.I)


def _claim_stem(term):
    term = term.lower()
    for stem in ('diagnos', 'troubleshoot'):
        if term.startswith(stem):
            return stem
    return term


def _unsupported_product_claims(response, cited_passages):
    """Product/warranty claim terms in the answer that no cited passage contains.

    A term quoted from a cited approved passage is supported by that passage and
    needs no second product-domain citation. Passages without text (or with no
    matching term) leave the claim unsupported, preserving the previous
    behaviour. This is a provenance check, not a semantic entailment check.
    """
    claimed = {_claim_stem(m.group(0)) for m in _PRODUCT_POLICY_CLAIM.finditer(response)}
    if not claimed:
        return set()
    supported = set()
    for passage in cited_passages:
        text = passage.get('text') if isinstance(passage, dict) else None
        if isinstance(text, str):
            supported.update(_claim_stem(m.group(0)) for m in _PRODUCT_POLICY_CLAIM.finditer(text))
    return claimed - supported


def missing_citation_domains(customer_message, response, cited_passages):
    """Identify requested domains not represented by an approved cited SOP.

    Source provenance is only a coarse proxy; it does not prove that an
    individual claim is supported by the cited passage.
    """
    if ABSTENTION.search(response):
        return ()
    intent_text = _policy_intent_text(customer_message)
    intent_text = _EXCHANGE_ATTRIBUTE.sub(' ', intent_text)
    if _EXCHANGE_CONTEXT.search(intent_text) and not _FAULT_SIGNAL.search(intent_text):
        intent_text = _BARE_REPLACEMENT.sub(' ', intent_text)
    requested = {name for name, pattern in EXPLICIT_INTENTS.items()
                 if pattern.search(intent_text)}
    # Independent technical/warranty claims in the answer need product evidence.
    unsupported_claims = _unsupported_product_claims(response, cited_passages)
    if unsupported_claims:
        requested.add('product')
    # A damaged/broken/defective item is context, not a second policy
    # request, when the customer only seeks return/refund guidance.
    # Do not suppress a genuine replacement/warranty/technical request or
    # product-policy claims in the proposed answer.
    if ('product' in requested and {'returns', 'refunds'} & requested
            and not _PRODUCT_SPECIFIC_REQUEST.search(intent_text)
            and not unsupported_claims):
        requested.discard('product')
    cited = set()
    for passage in cited_passages:
        cited.update(SOURCE_DOMAINS.get(passage.get('source'), ()))
    return tuple(sorted(requested - cited))
