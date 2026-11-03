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

ABSTENTION = re.compile(r'\b(?:cannot (?:verify|confirm|find)|unable to (?:verify|confirm|find)|no (?:reliable|relevant|verified) (?:policy|evidence)|human review|specialist review)\b', re.I)


def missing_citation_domains(customer_message, response, cited_passages):
    """Identify requested domains not represented by an approved cited SOP.

    Source provenance is only a coarse proxy; it does not prove that an
    individual claim is supported by the cited passage.
    """
    if ABSTENTION.search(response):
        return ()
    requested = {name for name, pattern in EXPLICIT_INTENTS.items()
                 if pattern.search(customer_message)}
    cited = set()
    for passage in cited_passages:
        cited.update(SOURCE_DOMAINS.get(passage.get('source'), ()))
    return tuple(sorted(requested - cited))
