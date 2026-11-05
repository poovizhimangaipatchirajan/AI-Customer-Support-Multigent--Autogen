"""Conservative deterministic checks for high-impact unsupported claims.

Not a semantic entailment verifier. Never interprets SOP text as authorization to
perform refunds or other customer-account actions.
"""
import re

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


# DOA assessment eligibility is not a shipping ETA. Match the same failure
# condition and duration within a single approved passage; do not accept a
# delivery promise merely because a DOA passage happens to be cited.
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4,
                 "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "fourteen": 14, "thirty": 30}
_DOA_CONTEXT = re.compile(
    r"\b(?:dead[ -]on[ -]arrival|DOA|fail(?:ed|s|ure)?\s+to\s+power\s+on|"
    r"severe\s+functional\s+failure)\b", re.I)
_DOA_WINDOW = re.compile(
    r"\bwithin\s+(\d+|" + "|".join(_NUMBER_WORDS) +
    r")\s+(?:(business|working|calendar)\s+)?(days?|hours?|weeks?)\b", re.I)
_DELIVERY_COMMITMENT = re.compile(
    r"\b(?:deliver(?:ed|y)?|arriv(?:es|ing|al)?|ship(?:ped)?|dispatch(?:ed)?)"
    r"\s+(?:within|in|by)\s+", re.I)


def _duration_tuple(match):
    value, qualifier, unit = match.groups()
    amount = int(value) if value.isdigit() else _NUMBER_WORDS[value.lower()]
    q = (qualifier or "").lower()
    return amount, "business" if q == "working" else q, unit.lower().rstrip("s")


def _supported_doa_eligibility(clause, texts):
    """Allow supported DOA *assessment windows*, never shipment promises."""
    if not _DOA_CONTEXT.search(clause) or _DELIVERY_COMMITMENT.search(clause):
        return False
    if not re.search(r"\b(?:may|might|could|qualif|eligib|assessment)\b", clause, re.I):
        return False
    claim = _DOA_WINDOW.search(clause)
    if not claim:
        return False
    amount, qualifier, unit = _duration_tuple(claim)
    for text in texts:
        for fragment in re.split(r"(?<=[.!?])\s+|\n+", text):
            if not _DOA_CONTEXT.search(fragment):
                continue
            for evidence_match in _DOA_WINDOW.finditer(fragment):
                e_amount, e_qualifier, e_unit = _duration_tuple(evidence_match)
                # Unqualified 'days' may refer to calendar days in a DOA
                # eligibility statement; never equate business with calendar.
                qualifiers_compatible = (qualifier == e_qualifier or
                                         (qualifier == "" and e_qualifier == "calendar"))
                if (amount == e_amount and unit == e_unit and qualifiers_compatible):
                    return True
    return False


# Phrasings that assert purchase/delivery as the start of a return window.
# "within N days of X" is the original form; "N-day return window from X" and
# "within N days after X" assert the same unsupported start point.
# Any stated starting point for a return window (rule 25): purchase,
# delivery, receipt, arrival, dispatch/shipment or order date, as a noun
# ("14 days from receipt") or a verb ("14 days after you receive it").
_ANCHOR_NOUNS = r"(purchase|delivery|receipt|arrival|dispatch|shipment|order(?:\s+date)?)"
_ANCHOR_VERBS = (r"(receive[sd]?|receiving|arrives?|arrived|is\s+delivered|was\s+delivered|"
                 r"ships?|shipped|is\s+dispatched|was\s+dispatched|purchased|bought|ordered)")
_ANCHORED_WINDOW_PATTERNS = (
    re.compile(r"\b(?:within|in)\s+(\d+)\s+days?\s+of\s+(?:the\s+)?(?:date\s+of\s+)?" + _ANCHOR_NOUNS + r"\b", re.I),
    re.compile(r"\b(\d+)[\s-]*days?\b(?:\s+(?:return\s+)?(?:window|period))?"
               r"\s+(?:from|after|following)\s+(?:the\s+)?(?:date\s+of\s+|day\s+of\s+)?" + _ANCHOR_NOUNS + r"\b", re.I),
    re.compile(r"\b(\d+)[\s-]*days?\b[^.!?]{0,40}?\b(?:after|from|once|when|since|starting\s+when)\s+"
               r"(?:the\s+day\s+)?(?:you\s+|it\s+|they\s+|the\s+\w+\s+|your\s+\w+\s+)?" + _ANCHOR_VERBS + r"\b", re.I),
)
# Word family per anchor, used to look for the same starting point in evidence.
_ANCHOR_FAMILY = (
    ('purchas', 'purchase'), ('bought', 'purchase'), ('order', 'order'),
    ('deliver', 'delivery'), ('receipt', 'receipt'), ('receiv', 'receipt'),
    ('arriv', 'arrival'), ('dispatch', 'dispatch'), ('ship', 'dispatch'),
)
_FAMILY_PATTERN = {
    'purchase': r"purchas\w*|bought", 'order': r"order(?:ed|\s+date)?", 'delivery': r"deliver\w*",
    'receipt': r"receipt|receiv\w*", 'arrival': r"arriv\w*", 'dispatch': r"dispatch\w*|ship\w*",
}


def _anchor_family(word):
    word = re.sub(r"^(?:is|was)\s+", "", word.lower())
    return next(family for prefix, family in _ANCHOR_FAMILY if word.startswith(prefix))


def _return_window_anchor_issue(sentence, texts):
    """Do not assert purchase/delivery as the starting date without evidence."""
    match = next((m for m in (pat.search(sentence) for pat in _ANCHORED_WINDOW_PATTERNS) if m), None)
    if not match or not re.search(r"\breturn(?:s|ed|ing)?\b", sentence, re.I):
        return None
    anchor = _anchor_family(match.group(2))
    family = _FAMILY_PATTERN[anchor]
    for text in texts:
        # The same duration explicitly tied to the same starting point in the passage.
        if re.search(r"\b(?:within|in)\s+" + re.escape(match.group(1)) +
                     r"\s+(?:calendar\s+)?days?\s+of\s+(?:the\s+)?(?:date\s+of\s+)?(?:" + family + r")\b",
                     text, re.I):
            return None
        if re.search(r"\b" + re.escape(match.group(1)) + r"[\s-]*(?:calendar\s+)?days?\b[^.!?]{0,40}?\b"
                     r"(?:of|from|after|following|once|when|since)\s+[^.!?]{0,20}?\b(?:" + family + r")\b",
                     text, re.I):
            return None
    # Delivery-based DOA evidence can establish a delivery anchor for the
    # *same* electronics duration when the return SOP separately confirms it.
    # Never use this cross-reference for a purchase-date assertion.
    if anchor == 'delivery' and re.search(r"\bconsumer\s+electronic", sentence, re.I):
        return_window = any(re.search(r"\bconsumer\s+electronics?\b[^.!?]*?\b" +
                                      re.escape(match.group(1)) + r"[ -]day\b", t, re.I)
                            for t in texts)
        delivery_context = any(re.search(r"\b(?:DOA|dead[ -]on[ -]arrival|severe\s+functional\s+failure)\b", t, re.I)
                               and re.search(r"\b(?:" + re.escape(match.group(1)) + r"|" +
                                             next((w for w, n in _NUMBER_WORDS.items() if n == int(match.group(1))), "") +
                                             r")\s+calendar\s+days?\s+of\s+delivery\b", t, re.I)
                               for t in texts)
        if return_window and delivery_context:
            return None
    return 'UNSUPPORTED_RETURN_WINDOW_ANCHOR'


# Return categories as approved passages name them. Used only to detect a
# draft asserting that the customer's item belongs to one (rules 17, 26);
# no product is ever mapped to a category here.
_CATEGORY = (r"(?:fashion(?:\s*(?:&|and)\s*apparel)?|apparel|consumer\s+electronics?|"
             r"(?:large\s+)?home\s+appliances?|personal\s+care(?:\s*(?:&|and)\s*beauty)?)"
             r"(?:\s+(?:items?|products?|goods|devices?))?")
_CATEGORY_EXAMPLE = re.compile(
    r"(\b" + _CATEGORY + r")(\s*,\s*|\s+)(?:like|such\s+as|including|e\.g\.,?)\s+"
    r"((?:your|the|a|an|this)\s+)?([A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*){0,3}?)"
    r"(?:(\s*,)|(?=\s+(?:have|has|are|is|come|comes|carry|carries|must|need|needs|qualify|qualifies|"
    r"can|may|require|requires|there)\b|\s*[.;:!?]))", re.I)
# After ", including X," the clause must continue with a verb or end; otherwise
# X is the first item of a list ("including phones, laptops and tablets").
_EXAMPLE_CONTINUES = re.compile(
    r"\s+(?:have|has|are|is|come|comes|carry|carries|must|need|needs|qualify|qualifies|"
    r"can|may|require|requires|there)\b|\s*[.;:!?]|\s*$", re.I)
_MEMBERSHIP = (r"(?:(?:is|are)\s+(?:considered|classified\s+(?:as|under)|categori[sz]ed\s+(?:as|under|in)|"
               r"treated\s+as|counted\s+as|regarded\s+as)\s+(?:an?\s+|part\s+of\s+(?:the\s+)?)?"
               r"|(?:is|are)\s+an?\s+"  # plain "it is a consumer electronic item"
               r"|,?\s*being\s+(?:an?\s+)?|falls?\s+(?:under|into|within)\s+(?:the\s+)?"
               r"|belongs?\s+to\s+(?:the\s+)?)")
_CATEGORY_ASSERTION = re.compile(r"\b" + _MEMBERSHIP + r"(?P<cat>" + _CATEGORY + r")(?:\s+category)?", re.I)
# "<item> <membership> <category>[, which| and| ,] <rest>" -> "<Category> <rest>".
_CATEGORY_REWRITE = re.compile(
    r"^(?P<subject>(?:the|your|this|these|those|my)?\s*[\w\s-]{1,60}?)\s*" + _MEMBERSHIP +
    r"(?P<cat>" + _CATEGORY + r")(?:\s+category)?\s*(?:,\s*which\s+|\s+which\s+|\s+and\s+|,\s+|\s+that\s+)"
    r"(?:(?:it|they|this|these)\s+)?"
    r"(?P<rest>(?:has|have|is|are|come|comes|carry|carries|must|need|needs|require|requires|qualify|qualifies)\b.*)$",
    re.I)
_CANONICAL = (("electronic", "Consumer electronics"), ("appliance", "Large home appliances"),
              ("personal", "Personal care and beauty products"), ("fashion", "Fashion and apparel items"),
              ("apparel", "Fashion and apparel items"))
_PLURAL_VERB = {"has": "have", "is": "are", "comes": "come", "carries": "carry", "needs": "need",
                "requires": "require", "qualifies": "qualify"}


def _rewrite_membership(sentence):
    """Turn "<item> is considered <category>, which has W" into "<Category> have W"."""
    m = _CATEGORY_REWRITE.match(sentence.strip())
    if not m:
        return None
    canonical = next(name for key, name in _CANONICAL if key in m.group('cat').lower())
    verb, _, tail = m.group('rest').partition(' ')
    return f"{canonical} {_PLURAL_VERB.get(verb.lower(), verb)} {tail}".strip()


CATEGORY_LIMITATION = ("The approved policy does not say which return category your item "
                       "belongs to, so the applicable return category and window will be "
                       "confirmed during verification.")


def repair_unsupported_category_membership(draft, passages):
    """Remove claims that the customer's item belongs to a return category.

    "Consumer electronics like your cable have..." keeps the policy statement
    and drops the unsupported example; a sentence classifying the item
    ("your blender is considered a consumer electronic...") is replaced by one
    honest limitation. Conditional wording ("If your item is...") is kept, and
    nothing changes when a cited passage itself names the item. The caller
    must still revalidate the result. Returns (draft, changed).
    """
    if not isinstance(draft, str) or not draft.strip():
        return draft, False
    texts = ' '.join(p.get('text', '') for p in passages if isinstance(p, dict)).lower()
    changed = False

    def strip_example(m):
        nonlocal changed
        item = m.group(4)
        if re.search(r'\b' + re.escape(item.lower()) + r'\b', texts):
            return m.group(0)  # the passage itself names this item
        if m.group(2).strip() == ',' and not _EXAMPLE_CONTINUES.match(m.string, m.end()):
            return m.group(0)  # a list follows; leave the sentence for revalidation
        changed = True
        # ", including X," -> drop both commas; " like X," -> keep the clause comma.
        trailing = '' if m.group(2).strip() == ',' else (m.group(5) or '')
        return m.group(1) + trailing

    paragraphs = []
    limitation_added = False
    for paragraph in draft.split('\n'):
        paragraph = _CATEGORY_EXAMPLE.sub(strip_example, paragraph)
        kept = []
        for sentence in re.split(r'(?<=[.!?])\s+', paragraph):
            conditional = re.match(r'\s*(?:if|whether|should|in case)\b', sentence, re.I)
            if sentence and not conditional and _CATEGORY_ASSERTION.search(sentence):
                changed = True
                rewritten = _rewrite_membership(sentence)
                if rewritten:
                    kept.append(rewritten)  # keep the category policy, drop the item claim
                if not limitation_added:
                    kept.append(CATEGORY_LIMITATION)
                    limitation_added = True
                continue
            kept.append(sentence)
        paragraphs.append(' '.join(s for s in kept if s))
    return ('\n'.join(paragraphs), True) if changed else (draft, False)


def repair_unsupported_return_anchor(draft, passages):
    """Remove only an unsupported 'of purchase/delivery' anchor, never a window.

    Require a matching category and duration in the approved evidence. The
    caller must revalidate the complete candidate before publishing.
    """
    if not isinstance(draft, str) or not draft.strip():
        return draft
    texts = [p.get('text', '') for p in passages if isinstance(p, dict)]
    if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
        return draft
    sentences = re.split(r'(?<=[.!?])\s+|\n+', draft)
    changed = []
    count = 0
    for sentence in sentences:
        match = re.search(r"\b(within\s+(\d+)\s+days?)\s+of\s+(purchase|delivery)\b", sentence, re.I)
        if not match or not re.search(r"\breturn(?:s|ed|ing)?\b", sentence, re.I):
            changed.append(sentence)
            continue
        if _return_window_anchor_issue(sentence, texts) is None:
            changed.append(sentence)
            continue
        category = re.search(r"\b(consumer\s+electronics|fashion\s*(?:&|and)\s*apparel|large\s+home\s+appliances)\b", sentence, re.I)
        if not category:
            return draft
        category_text = category.group(1)
        if not any(re.search(re.escape(category_text) + r"\b[^.!?]*?\b" + re.escape(match.group(2)) + r"[ -]days?\b", t, re.I) for t in texts):
            return draft
        changed.append(sentence[:match.start()] + match.group(1) + sentence[match.end():])
        count += 1
    return ' '.join(changed) if count == 1 else draft


# Known return categories: how a draft names them, and how the approved
# passage names the same category. Used only to confirm a duration against the
# SAME category's passage; the guard never maps products to categories.
_RETURN_CATEGORIES = (
    (r"fashion(?:\s*(?:&|and)\s*apparel)?|apparel", r"fashion\s*(?:&|and)\s*apparel"),
    (r"consumer\s+electronics?", r"consumer\s+electronics"),
    (r"(?:large\s+)?home\s+appliances|large\s+appliances", r"large\s+home\s+appliances"),
)


def _category_window_supported(sentence, value, texts):
    """True when the sentence names exactly one known return category and an
    approved passage states the same N-day window for that same category.

    Wrong category, wrong duration, or a sentence naming several categories is
    never supported here (rule 26: no cross-category windows).
    """
    named = [passage_pat for draft_pat, passage_pat in _RETURN_CATEGORIES
             if re.search(r"\b(?:" + draft_pat + r")\b", sentence, re.I)]
    if len(named) != 1:
        return False
    return any(re.search(named[0] + r"\b[^.!?]*?\b" + re.escape(value) + r"[ -]days?\b", t, re.I)
               for t in texts)


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
    if not claim or not re.search(r"\b(?:returns?|returned|returning|refunds?|replacements?|exchanges?)\b", clause, re.I):
        return None
    category = claim.group(1).lower()
    if category.startswith('fashion'):
        category_pattern = r"fashion\s*(?:&|and)\s*apparel"
    elif category.startswith('consumer'):
        category_pattern = r"consumer\s+electronics"
    else:
        category_pattern = r"large\s+home\s+appliances"
    for text in texts:
        for match in re.finditer(category_pattern + r"\b[^.!?]*?\b(\d+)[ -]days?\b", text, re.I):
            if match.group(1) == claim.group(2):
                return None
    return 'UNSUPPORTED_DELIVERY_TIME'


# Narrow claim-to-evidence check for invoice/bill retrieval instructions.
# Source validity alone does not establish that a dashboard feature exists.
_INVOICE_TERM = re.compile(r"\b(?:invoices?|bills?|receipts?)\b", re.I)
_INVOICE_RETRIEVAL = re.compile(
    r"\b(?:download(?:able)?|view(?:ing)?|access|find|retrieve|"
    r"available|orders?\s+(?:dashboard|page|section|history)|dashboard)\b", re.I
)


def unsupported_invoice_guidance(draft, passages):
    """Reject unsupported invoice retrieval claims in customer-facing drafts.

    This is a narrow heuristic, not a general semantic entailment check.
    Evidence must contain an invoice/bill/receipt term and retrieval guidance
    within the same passage; unrelated payment text cannot support the claim.
    """
    if not isinstance(draft, str):
        return None
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", draft):
        if not (_INVOICE_TERM.search(sentence) and
                _INVOICE_RETRIEVAL.search(sentence)):
            continue
        # Do not treat a request to provide an invoice/order number as a
        # claim that an invoice-download feature exists.
        for passage in passages if isinstance(passages, (list, tuple)) else ():
            if not isinstance(passage, dict):
                continue
            text = passage.get("text", "")
            if isinstance(text, str) and _INVOICE_TERM.search(text) and _INVOICE_RETRIEVAL.search(text):
                break
        else:
            return "UNSUPPORTED_INVOICE_GUIDANCE"
    return None


# Only explicit, category-specific return-window questions trigger this check.
# These are recognition patterns, NOT hardcoded policy durations.
_RETURN_WINDOW_CATEGORIES = (
    (re.compile(r"\b(?:fashion(?:\s*(?:&|and)\s*apparel)?|apparel|clothing)\b", re.I),
     re.compile(r"\bfashion\s*(?:&|and)\s*apparel\s*[:\-]?\s*(\d+)\s*days?\b", re.I)),
    (re.compile(r"\b(?:consumer\s+electronics|electronics)\b", re.I),
     re.compile(r"\bconsumer\s+electronics\s*[:\-]?\s*(\d+)\s*days?\b", re.I)),
    (re.compile(r"\b(?:large\s+home\s+appliances|large\s+appliances)\b", re.I),
     re.compile(r"\blarge\s+home\s+appliances\s*[:\-]?\s*(\d+)\s*days?\b", re.I)),
)


def missing_requested_return_window(draft, passages, customer_message):
    """Reject an omitted explicitly requested category window, never invent one.

    Recognizes the approved SOP's category/day table format. If a relevant
    duration is absent or conflicting, require review rather than guessing.
    This narrow check is not a general completeness/entailment verifier.
    """
    if not isinstance(customer_message, str) or not isinstance(draft, str):
        return None
    query = customer_message
    if not re.search(r"\breturns?\b", query, re.I):
        return None
    if not re.search(
        r"\b(?:window|period|deadline|how\s+(?:many|long)|within\s+how\s+many|"
        r"days?|time\s+limit|timeframe|eligible\s+for\s+how\s+long)\b", query, re.I
    ):
        return None
    requested = [evidence_pattern for query_pattern, evidence_pattern
                 in _RETURN_WINDOW_CATEGORIES if query_pattern.search(query)]
    if len(requested) != 1:
        return None  # Ambiguous or multiple categories: do not infer one.
    if not isinstance(passages, (list, tuple)):
        return 'MISSING_RETURN_WINDOW_ANSWER'
    windows = set()
    # Approved excerpts express windows in prose ("Fashion and apparel have a
    # 14-day return window"), whereas the source SOP also has a category/day
    # table. Accept either evidence format, never a duration from another category.
    category_query_pattern = next(
        (query_pattern for query_pattern, evidence_pattern in _RETURN_WINDOW_CATEGORIES
         if evidence_pattern is requested[0]), None
    )
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        if passage.get('source') != '02_Returns_and_Refunds_SOP.pdf':
            continue
        policy_text = passage.get('text', '')
        if isinstance(policy_text, str):
            windows.update(int(m.group(1)) for m in requested[0].finditer(policy_text))
            # Curated approved-only excerpts: category precedes the duration,
            # including "14-day return window" and "within 14 days" forms.
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", policy_text):
                if not (category_query_pattern and category_query_pattern.search(sentence)
                        and re.search(r"\breturns?\b", sentence, re.I)):
                    continue
                for match in re.finditer(
                    r"\b(\d+)\s*[- ]\s*days?\b|\b(?:within|in)\s+(\d+)\s+days?\b",
                    sentence, re.I,
                ):
                    windows.add(int(match.group(1) or match.group(2)))
    if len(windows) != 1:
        return 'MISSING_RETURN_WINDOW_ANSWER'
    expected = next(iter(windows))
    # Require an affirmative return-period statement, not an unrelated number,
    # a refund-processing SLA, or a negated claim about the requested window.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", draft):
        if not re.search(r"\breturns?\b", sentence, re.I):
            continue
        if re.search(r"\b(?:not|no|unknown|unspecified|cannot|can't|don't|doesn't)\b", sentence, re.I):
            continue
        # Both "within 14 days" and "14-day return window" express an
        # eligibility period. Require the return context and policy duration.
        day_expression = (
            re.search(r"\b" + str(expected) + r"\s*(?:calendar\s+)?days?\b", sentence, re.I)
            or re.search(r"\b" + str(expected) + r"[ -]day\b", sentence, re.I)
        )
        if (day_expression
                and re.search(r"\b(?:within|window|period|eligible|eligibility|allowed|accepted|"
                              r"request|deadline|limit|can\s+return|may\s+return)\b", sentence, re.I)
                and not re.search(r"\b(?:processed|processing|refunded|refund\s+issued)\b", sentence, re.I)):
            return None
    return 'MISSING_RETURN_WINDOW_ANSWER'


def check_response_grounding(draft, passages, customer_message=None):
    """Reject obvious unsupported completed actions and numeric time claims.

    Passing this narrow guard does not certify semantic entailment; backend
    citation, authorization, risk, and visibility checks are still required.
    """
    if not isinstance(draft, str) or not draft.strip():
        return 'EMPTY_DRAFT'
    invoice_issue = unsupported_invoice_guidance(draft, passages)
    if invoice_issue:
        return invoice_issue
    texts = [p.get('text', '') for p in passages if isinstance(p, dict)]
    if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
        # Empty or malformed policy passages cannot ground a customer answer.
        return "MISSING_POLICY_TEXT"
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
    completeness_issue = missing_requested_return_window(draft, passages, customer_message)
    if completeness_issue:
        return completeness_issue

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
        anchor_issue = _return_window_anchor_issue(sentence, texts)
        if anchor_issue:
            return anchor_issue
        # Do not skip an entire sentence simply because it contains 'not'.
        for clause in re.split(r"[;,]\s*|\s+but\s+|\s+however\s+", sentence, flags=re.I):
            if _ACTION.search(clause) and not _NEGATION.search(clause):
                return 'UNVERIFIED_COMPLETED_ACTION'
            category_window_issue = _unsupported_category_return_window(clause, texts)
            if category_window_issue:
                return category_window_issue
            for match in _TIME.finditer(clause):
                # Commas can separate the return verb/category from its window.
                # Examine the whole sentence, but never exempt a clause that
                # itself promises delivery, dispatch, or refund processing.
                value, qualifier, unit = match.groups()
                sentence_return_window = (
                    not qualifier and unit.lower().rstrip('s') == 'day'
                    and re.search(r"\b(?:return|returns|returned|returning)\b", sentence, re.I)
                    and re.search(r"\b(?:can|may|eligible|eligibility|window|period|allowed)\b", sentence, re.I)
                    and not re.search(r"\b(?:deliver|arrive|ship|dispatch|process|issue|complete)\w*\s+(?:within|in|by)\b", clause, re.I)
                    and (
                        (re.search(r"\b(?:consumer\s+electronics?|headphones?|earbuds?)\b", sentence, re.I)
                         and any(re.search(r"\bconsumer\s+electronics\b[^.!?]*?\b" +
                                           re.escape(value) + r"[ -]days?\b", t, re.I)
                                 for t in texts))
                        or _category_window_supported(sentence, value, texts)
                    )
                )
                if sentence_return_window:
                    continue
                # Category return/replacement eligibility windows refer to the
                # period allowed to request a remedy, not an action SLA.
                # "allows returns within 7 days of delivery" is a return
                # eligibility window, not a promise to deliver in 7 days.
                # Keep the category/window check above active for wrong values.
                return_eligibility = (
                    re.search(r"\b(?:returns?|returned|returning|replacements?|refunds?|exchanges?)\b", clause, re.I)
                    and (
                        re.search(r"\b(?:accepted|eligible|eligibility|request|requested|window|period)\b", clause, re.I)
                        or re.search(r"\b(?:allows?|permits?|accepts?)\s+returns?\s+(?:within|in)\s+\d+\s+days?\b", clause, re.I)
                        or re.search(r"\breturns?\s+(?:are\s+)?(?:allowed|permitted)\s+(?:within|in)\s+\d+\s+days?\b", clause, re.I)
                        or re.search(r"\b(?:must|should|can|may)\s+be\s+returned\s+(?:within|in)\s+\d+\s+days?\b", clause, re.I)
                        or re.search(r"\b(?:can|may)\s+return\b[^.!?;]*?\bwithin\s+\d+\s+days?\b", clause, re.I)
                    )
                    and not re.search(r"\b(?:delivered|arrive|arrives|ship|dispatch|processed|issued|completed)\s+(?:within|in|by)\b", clause, re.I)
                )
                if return_eligibility:
                    value, qualifier, unit = match.groups()
                    # Do not confuse eligibility with delivery, and never waive
                    # evidence for a wrong or category-mismatched duration.
                    if (not qualifier and unit.lower().rstrip('s') == 'day'
                            and any(re.search(r"\bconsumer\s+electronics\b[^.!?]*?\b" +
                                              re.escape(value) + r"[ -]days?\b", t, re.I)
                                    for t in texts)
                            and re.search(r"\b(?:consumer\s+electronics?|headphones?|earbuds?)\b", sentence, re.I)):
                        continue
                    if (not qualifier and unit.lower().rstrip('s') == 'day'
                            and _category_window_supported(sentence, value, texts)):
                        continue
                    if _supported_time(match, clause, texts):
                        continue
                    return 'UNSUPPORTED_DELIVERY_TIME' 
                if _supported_doa_eligibility(sentence, texts):
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
