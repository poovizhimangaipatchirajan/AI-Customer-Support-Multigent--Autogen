"""Conservative deterministic checks for high-impact unsupported claims.

Not a semantic entailment verifier. Never interprets SOP text as authorization to
perform refunds or other customer-account actions.
"""
import re
import os

# Numeric timelines are high-impact commitments. Check both the duration and
# its subject in a single cited passage, rather than matching an arbitrary
# phrase elsewhere in the retrieved evidence.
_TIME = re.compile(
    r"\b(?:within|in|by|takes?|arriv(?:es|ing)|deliver(?:ed|y)?)\s+"
    r"(?:about\s+|approximately\s+)?(\d+)\s*"
    r"(business|working|calendar)?\s*(days?|hours?|weeks?)\b", re.I
)
_ACTION = re.compile(
    r"\b(?:we|i|our team)\s+(?:have\s+|has\s+)?(?:already\s+)?"
    r"(?:issued|processed|approved|completed|sent)\s+"
    r"(?:your\s+|the\s+|a\s+)?(?:refund|reimbursement|payment|replacement)\b", re.I
)
_NEGATION = re.compile(r"\b(?:cannot|can\s*not|not|never|unable|haven.t|hasn.t|didn.t|no)\b", re.I)
_SUBJECTS = {
    "refund": re.compile(r"\brefund|reimburse|credit\b", re.I),
    "replacement": re.compile(r"\breplac|exchange|dispatch\b", re.I),
    "delivery": re.compile(r"\bdeliver|arriv|ship|transit|order\b", re.I),
    "return": re.compile(r"\breturn|pickup|collect\b", re.I),
}


def _subject(sentence):
    # Prefer the most specific business process. An unclassified duration must
    # not borrow a duration from an unrelated policy passage.
    for name in ("refund", "replacement", "return", "delivery"):
        if _SUBJECTS[name].search(sentence):
            return name
    return None


def _supported_time(match, sentence, texts):
    subject = _subject(sentence)
    if subject is None:
        return False
    value, qualifier, unit = match.groups()
    # 'working' and 'business' are equivalent; calendar and unspecified are not.
    qualifier = (qualifier or "").lower()
    if qualifier == "working":
        qualifier = "business"
    unit = unit.lower().rstrip("s")
    for text in texts:
        for fragment in re.split(r"(?<=[.!?])\s+|\n+", text):
            if _subject(fragment) != subject:
                continue
            for candidate in _TIME.finditer(fragment):
                v, q, u = candidate.groups()
                q = (q or "").lower()
                if q == "working":
                    q = "business"
                if v == value and q == qualifier and u.lower().rstrip("s") == unit:
                    return True
    return False


def _unsupported_category_return_window(clause, texts):
    """Validate category-specific return windows against the *same* category.

    A 14-day Fashion window cannot support a 14-day Electronics claim.
    Return None if no explicit category/window assertion is present; absence of
    recognizable category evidence fails closed for such assertions.
    """
    claim = re.search(
        r"\b(?:for\s+)?(fashion\s*(?:&|and)\s*apparel|consumer\s+electronics|"
        r"large\s+home\s+appliances)\b[^.!?;]*?"
        r"\b(?:within|in)\s+(\d+)\s+days?\b", clause, re.I)
    if not claim or not re.search(r"\b(?:return|refund|replacement|exchange)s?\b", clause, re.I):
        return None
    category = claim.group(1).lower()
    if category.startswith('fashion'):
        category_pattern = r"fashion\s*(?:&|and)\s*apparel"
    elif category.startswith('consumer'):
        category_pattern = r"consumer\s+electronics"
    else:
        category_pattern = r"large\s+home\s+appliances"
    for text in texts:
        for match in re.finditer(category_pattern + r"\s+(\d+)\s+days?\b", text, re.I):
            if match.group(1) == claim.group(2):
                return None
    return 'UNSUPPORTED_DELIVERY_TIME'


def check_response_grounding(draft, passages, customer_message=None):
    """Reject obvious unsupported completed actions and numeric time claims.

    Passing this narrow guard does not certify semantic entailment; backend
    citation, authorization, risk, and visibility checks are still required.
    """
    if not isinstance(draft, str) or not draft.strip():
        return 'EMPTY_DRAFT'
    texts = [p.get('text', '') for p in passages if isinstance(p, dict)]
    if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
        # Preserve legacy fixture behavior; do not interpret this as evidence.
        return None
    # These statements require live, authenticated order or payment evidence,
    # which the policy-only worker does not possess.
    _unverified_status = re.compile(
        r"\b(?:your|the)\s+(?:package|parcel|order|shipment)\s+"
        r"(?:is|has been)\s+(?:secure|safe|on (?:its|the) way|in transit|"
        r"moving forward|dispatched|shipped|delivered)\b", re.I)
    _unauthorized_promise = re.compile(
        r"\b(?:we|i|our team)\s+(?:will|shall|are going to|can now)\s+"
        r"(?:immediately\s+|right away\s+|promptly\s+)?"
        r"(?:dispatch|ship|send|issue|process|approve|arrange|initiate)\s+"
        r"(?:a|the|your|an)?\s*(?:replacement|refund|payment|pickup|return)\b", re.I)
    _false_threshold = re.compile(
        r"\b(?:refunds?|amounts?|requests?)\s+(?:of|for|under|below|worth)\s+"
        r"(?:rs\.?\s*|inr\s*|₹\s*)?(?:[1-4]\d{0,2})(?:\.\d{1,2})?\s*"
        r"(?:rs\.?|rupees?)?\s+(?:require|requires|must undergo|need|needs)\s+"
        r"(?:human|manual|manager|supervisor)\s+(?:approval|review)", re.I)
    if _unverified_status.search(draft):
        return 'UNVERIFIED_ORDER_STATUS'
    if _unauthorized_promise.search(draft):
        return 'UNAUTHORIZED_ACTION_PROMISE'
    if _false_threshold.search(draft):
        return 'MISSTATED_REFUND_THRESHOLD'
    # Restore policy amount/percentage checks lost in the 2D.4 merge.
    # The Phase 2D.3 quantitative checker exempts only the exact INR 500
    # human-review rule and explicitly attributed customer amounts.
    from backend.services.quantitative_grounding import unsupported_quantitative_claim
    if unsupported_quantitative_claim(draft, texts, customer_message=customer_message):
        return 'UNSUPPORTED_QUANTITATIVE_POLICY_CLAIM'

    # Dispatch SLA != delivery ETA. Evaluate affirmative clauses only.
    for shipping_clause in re.split(r"[.!?\n;]+", draft):
        clause = shipping_clause.strip()
        if not clause:
            continue
        if (re.search(r"\b(?:standard|usual|typical)\s+delivery\s+time(?:frame|line|s)?\s+(?:is|are|varies?|depends?)\b", clause, re.I)
                and re.search(r"\b(?:fulfillment\s+channel|FBN|DMF|HBN|dispatch\s+manifest)\b", clause, re.I)):
            return 'DISPATCH_MISREPRESENTED_AS_DELIVERY'
        if (re.search(r"\b(?:deliver(?:y|ed)|arriv(?:al|e))\s+(?:within|in|by)\s+48\s+business\s+hours\b", clause, re.I)
                and not re.search(r"\b(?:not|no|never|isn't|is not|doesn't|does not)\b", clause, re.I)):
            return 'DISPATCH_MISREPRESENTED_AS_DELIVERY'

    for sentence in re.split(r"(?<=[.!?])\s+|\n+", draft):
        # Check category and duration together before comma-separated clauses
        # split them apart (e.g. "For Consumer Electronics, returns ... 14 days").
        category_window_issue = _unsupported_category_return_window(sentence, texts)
        if category_window_issue:
            return category_window_issue
        # Do not skip an entire sentence simply because it contains 'not'.
        for clause in re.split(r"[;,]\s*|\s+but\s+|\s+however\s+", sentence, flags=re.I):
            if _ACTION.search(clause) and not _NEGATION.search(clause):
                return 'UNVERIFIED_COMPLETED_ACTION'
            category_window_issue = _unsupported_category_return_window(clause, texts)
            if category_window_issue:
                return category_window_issue
            for match in _TIME.finditer(clause):
                # Category return/replacement eligibility windows refer to the
                # period allowed to request a remedy, not an action SLA.
                # "allows returns within 7 days of delivery" is a return
                # eligibility window, not a promise to deliver in 7 days.
                # Keep the category/window check above active for wrong values.
                return_eligibility = (
                    re.search(r"\b(?:return|replacement|refund|exchange)s?\b", clause, re.I)
                    and (
                        re.search(r"\b(?:accepted|eligible|eligibility|request|requested|window|period)\b", clause, re.I)
                        or re.search(r"\b(?:allows?|permits?|accepts?)\s+returns?\s+(?:within|in)\s+\d+\s+days?\b", clause, re.I)
                        or re.search(r"\breturns?\s+(?:are\s+)?(?:allowed|permitted)\s+(?:within|in)\s+\d+\s+days?\b", clause, re.I)
                    )
                    and not re.search(r"\b(?:delivered|arrive|arrives|ship|dispatch|processed|issued|completed)\s+(?:within|in|by)\b", clause, re.I)
                )
                if return_eligibility:
                    continue
                # Negated timing promises ('cannot promise delivery in 3 days')
                # aren't commitments; other claims in the sentence still count.
                prefix = clause[:match.start()]
                if _NEGATION.search(prefix) and re.search(
                    r"\b(?:promise|guarantee|confirm|estimate|commit)\b", prefix, re.I
                ):
                    continue
                if not _supported_time(match, clause, texts):
                    return 'UNSUPPORTED_DELIVERY_TIME'
    return None


# Defense-in-depth: operational investigation mechanics should not appear in
# customer replies, even when an indexed SOP happens to mention them.
# This is a narrow denylist, NOT a complete document-classification solution.
_SENSITIVE_OPERATIONS = re.compile(
    r'\b(?:geofence(?:\s+audits?)?|driver\s+GPS\s+verification|\bGPS\s+audit|\bautomated\s+fulfillment\s+cent(?:er|re)s?|'
    r'tracking\s+updates?\s+every\s+\d+\s+minutes?)\b', re.I
)

def check_customer_visibility(draft):
    """Return a reason code for known internal operational details."""
    return 'SENSITIVE_OPERATIONAL_DETAIL' if _SENSITIVE_OPERATIONS.search(draft or '') else None


def customer_safe_passages(passages):
    """Expose only non-sensitive customer-visible sentences to agents.

    Keep original IDs and provenance; remove unsupported/internal-only excerpts.
    Return a new list, never mutate the retriever's cached passage dictionaries.
    This is defense-in-depth, not a replacement for document-level access control.
    """
    if not isinstance(passages, list):
        raise ValueError("Retriever must return a list")
    safe = []
    for passage in passages:
        if not isinstance(passage, dict) or not isinstance(passage.get("id"), str):
            raise ValueError("Invalid retrieved policy passage")
        # Curated source-verified excerpts must remain byte-for-byte intact.
        # In particular the approved electrical safety passage is on page 4.
        from backend.rag.customer_policy import is_approved_customer_passage
        if is_approved_customer_passage(passage):
            safe.append(dict(passage))
            continue
        from backend.rag.customer_runtime import customer_mode
        if customer_mode() == "approved_only":
            continue
        if passage.get("visibility") != "customer":
            continue
        source = passage.get("source", "")
        if source == "06_Escalations_and_Soft_Skills_SOP.pdf":
            continue
        # These are implementation notes or known internal operational pages,
        # not customer-facing policy, even if ingestion labeled them customer.
        if (source == "01_Shipping_and_Delivery_SOP.pdf" and passage.get("page") in (3, 4)) or (source == "02_Returns_and_Refunds_SOP.pdf" and passage.get("page") == 3) or (source == "03_Product_and_Warranty_SOP.pdf" and passage.get("page") == 4):
            continue
        original = passage.get("text")
        if not isinstance(original, str) or not original.strip():
            continue
        if ("FIXFLOW AI | IMPLEMENTATION GUIDANCE ADDENDUM" in original.upper()
                or "FIXFLOW AI - CUSTOMER ANSWER GUIDANCE" in original.upper()):
            continue
        # Strip complete sentences containing known sensitive mechanics rather
        # than replacing words inside them (which can invert their meaning).
        sentences = re.split(r"(?<=[.!?])\s+|\n+", original)
        retained = [part.strip() for part in sentences
                    if part.strip() and not _SENSITIVE_OPERATIONS.search(part)]
        text = " ".join(retained)
        if not text or _SENSITIVE_OPERATIONS.search(text):
            continue
        safe.append({**passage, "text": text})
    return safe
