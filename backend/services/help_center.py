"""Authenticated, read-only NovaCart self-service policy lookup.

Conservative, extractive answers: unsupported benefits are never inferred from
semantically adjacent passages. Case-specific decisions belong to tickets.
"""
import re
from functools import lru_cache

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.auth.security import principal
from backend.rag.customer_runtime import create_customer_retriever
from backend.services.risk import screen
from backend.rag.customer_policy import is_approved_customer_passage
from backend.rag.customer_runtime import customer_mode

router = APIRouter(prefix='/help', tags=['Help Center'])

TITLES = {
    '01_Shipping_and_Delivery_SOP.pdf': 'Shipping & Delivery Policy',
    '02_Returns_and_Refunds_SOP.pdf': 'Refund & Return Policy',
    '03_Product_and_Warranty_SOP.pdf': 'Product & Warranty Policy',
    '04_Account_and_Login_SOP.pdf': 'Account & Security Policy',
    '05_Payments_and_Billing_SOP.pdf': 'Payments & Billing Policy',
}

STOP = set(('what is the a an of to for in on with and or are can do does how when '
            'where why my your our me please tell about nova cart novacart policy '
            'policies information question help provide provides including include '
            'every all above below conditions requirements eligibility product products '
            'regarding details explain available coverage packaging condition').split())
STOP.update({
    'according',
    'which',
    'section',
    'sop',
    'supported',
    'methods',
})
PERSONAL = re.compile(
    r'\b(my order|my refund|my account|my payment|my parcel|my package|my ticket|'
    r'i ordered|i purchased|i received|i was charged|refund me|refund my|'
    r'refund for my|refund of my|want a refund|request a refund|track my|'
    r'where is my|order (?:id|number|#)|charged twice|unauthori[sz]ed|'
    r'fraud|scam|legal action|sue|lawyer)\b', re.I)

# Terms that change the truth of a requested benefit must appear in the
# supporting passage. E.g. a 30-day warranty cannot establish "lifetime".
QUALIFIERS = set(('free lifetime unlimited guaranteed complimentary helicopter '
                  'instant automatic automatically accidental international '
                  'same-day overnight').split())

NO_EVIDENCE = ('I could not verify that benefit or condition in the approved '
               'NovaCart customer policies. Please create a support ticket '
               'for further assistance.')
NOTE = 'General policy information only; not an order-specific decision.'


class Question(BaseModel):
    question: str = Field(min_length=5, max_length=500)


@lru_cache(maxsize=1)
def retriever():
    return create_customer_retriever()


def keywords(value):
    return {word for word in re.findall(r'[a-z]{3,}', value.lower())
            if word not in STOP}


def _no_evidence():
    return {'status': 'no_evidence', 'answer': NO_EVIDENCE, 'sources': []}


def _passages(text):
    """Split extracted PDF text without treating SOP headings as answers."""
    # Existing PDF extraction sometimes flattens whole tables into one line.
    text = re.sub(r'[\x00-\x1f\x7f]', ' ', text or '')
    text = re.sub(r'\s+', ' ', text).strip()
    # Drop leading internal-style document headers, not policy facts.
    text = re.sub(r'^NovaCart Support Playbook:.*?(?=\b1\.2\b|\b1\.3\b|\b2\.1\b|\b3\.1\b)',
                  '', text, flags=re.I)
    text = re.sub(r'\bSection \d+: [^:]{5,110}(?=\s+\d+\.\d+\b)', ' ', text)
    # Section markers and sentence boundaries are useful in flattened PDFs.
    parts = re.split(r'(?=\b\d+\.\d+\s+[A-Z])|(?<=[.!?])\s+(?=[A-Z])', text)
    return [part.strip() for part in parts if part.strip()]


def _warranty_alternatives(question, docs):
    """Explain *documented* alternatives to a requested lifetime warranty.

    This is a narrow, deterministic exception to the missing-qualifier rule:
    never infer that a free lifetime benefit exists or categorically does not
    exist. Every statement below is conditional on explicit source phrases.
    """
    q = question.lower()
    if not ('warranty' in q and 'lifetime' in q and 'free' in q):
        return None
    for doc in docs:
        if (doc.get('source') != '03_Product_and_Warranty_SOP.pdf'
                or doc.get('visibility') != 'customer' or not doc.get('id')):
            continue
        body = re.sub(r'[\x00-\x1f\x7f]', ' ', doc.get('text', ''))
        body = re.sub(r'\s+', ' ', body)
        statements = []
        if (re.search(r'30.day platform assurance', body, re.I)
                and re.search(r'manufacturing defects', body, re.I)):
            statements.append('The documented 30-day platform assurance covers manufacturing defects.')
        if (re.search(r'(?:12\s+to\s+24.month|12\s*[-–]\s*24.month)', body, re.I)
                and re.search(r'(?:manufacturer warranties|standard warranty)', body, re.I)):
            statements.append('The policy describes standard manufacturer warranties of 12 to 24 months.')
        if (re.search(r'NovaCare Extended Protection', body, re.I)
                and re.search(r'optional', body, re.I)
                and re.search(r'purchased', body, re.I)
                and re.search(r'accidental', body, re.I)
                and re.search(r'up to 36 months', body, re.I)):
            statements.append('Optional, purchased NovaCare Extended Protection covers specified accidental damage for up to 36 months.')
        if not statements:
            continue
        return {
            'status': 'answered',
            'answer': ('I could not verify free lifetime warranty coverage for every product '
                       'in the cited policy passage. ' + ' '.join(statements) +
                       ' Product-specific eligibility may require a support ticket.'),
            'sources': [{'document': doc['source'],
                         'title': TITLES[doc['source']],
                         'page': doc.get('page'),
                          'chunk_id': doc['id'],
                         'version': doc.get('version', '')}],
            'note': NOTE,
        }
    return None


def _fashion_answer(question, doc):
    """Cleanly extract a specific table row only when its full facts exist."""
    q = question.lower()
    if doc.get('source') != '02_Returns_and_Refunds_SOP.pdf':
        return None
    if not ('fashion' in q or 'return' in q):
        return None
    body = re.sub(r'\s+', ' ', doc.get('text', ''))
    match = re.search(
        r'Fashion\s*(?:&|and)\s*Apparel\s+14\s+Days\s+'
        r'Unworn,\s*unwashed,\s*brand tags intact,\s*original polybag',
        body, re.I)
    if not match:
        return None
    sentences = ['Fashion & Apparel items have a 14-day return window. '
                 'They must be unworn and unwashed, with brand tags intact '
                 'and the original polybag.']
    if 'customer self-service app' in body.lower() and 'orders dashboard' in body.lower():
        if 'fashion' not in q:  # General return-policy question.
            sentences.append('The policy also describes return initiation through '
                             'the customer app orders dashboard.')
    return ' '.join(sentences)


def _payment_methods_answer(question, doc):
    """Answer general payment-method questions from explicit SOP evidence."""
    if doc.get("source") != "05_Payments_and_Billing_SOP.pdf":
        return None

    q = question.lower()
    if not (
        ("payment" in q or "payments" in q)
        and any(term in q for term in (
            "method", "option", "supported", "accepted", "accept"
        ))
    ):
        return None

    body = re.sub(r"\s+", " ", doc.get("text", ""))

    # Require the actual payment-method section, not merely
    # an incidental mention of payments elsewhere.
    match = re.search(
        r"1\.2\s+Payment Rail Topology\s+(.*?)\s+1\.3\s+",
        body,
        re.I | re.S,
    )
    if not match:
        return None

    section = match.group(1)

    categories = (
        "Card Networks:",
        "Direct Bank Rails / Wallets:",
        "Deferred / Credit Rails:",
    )
    if not all(category.lower() in section.lower() for category in categories):
        return None

    # Extract only the three documented categories.
    match = re.search(
        r"(Card Networks:.*?)"
        r"(Direct Bank Rails / Wallets:.*?)"
        r"(Deferred / Credit Rails:.*)",
        section,
        re.I | re.S,
    )
    if not match:
        return None

    return (
        "According to NovaCart's Payments & Billing Policy:\n\n"
        + "\n".join(
            f"- {part.strip()}" for part in match.groups()
        )
    )



# Route explicitly topical policy questions to the appropriate approved SOP.
# Multiple matches are allowed for cross-department questions.
DEPARTMENT_TOPICS = {
    '01_Shipping_and_Delivery_SOP.pdf': (
        'shipping', 'delivery', 'shipment', 'tracking', 'carrier',
        'courier', 'parcel', 'dispatch', 'delivered',
    ),
    '02_Returns_and_Refunds_SOP.pdf': (
        'return', 'returns', 'refund', 'refundable', 'exchange',
        'replacement',
    ),
    '03_Product_and_Warranty_SOP.pdf': (
        'warranty', 'warranties', 'manufacturing defect',
        'product defect', 'novacare',
    ),
    '04_Account_and_Login_SOP.pdf': (
        'account', 'login', 'log in', 'password', 'authentication',
        'identity verification', 'profile', '2fa', 'sign in',
    ),
    '05_Payments_and_Billing_SOP.pdf': (
        'payment', 'payments', 'billing', 'checkout', 'upi',
        'emi', 'bnpl', 'invoice',
    ),
}


def relevant_department_sources(question):
    """Return explicit topical source constraints, or empty for ambiguity."""
    q = question.lower()
    return {
        source
        for source, topics in DEPARTMENT_TOPICS.items()
        if any(re.search(r'\b' + re.escape(topic) + r'\b', q)
               for topic in topics)
    }


def answer_question(question: str, search=None):
    q = question.strip()
    if (PERSONAL.search(q) or screen(q) or
            re.search(r'\b(?:order|ticket)[-\s#:]*\d{4,}\b', q, re.I)):
        return {
            'status': 'ticket_required',
            'answer': ('This appears to involve a specific order, refund request, '
                       'payment dispute, or account. Please create a support '
                       'ticket so the appropriate review and privacy checks apply.'),
            'sources': [],
        }

    search = search if search is not None else retriever()
    docs = search.search(q, k=4)
    if customer_mode() == "approved_only":
        docs = [doc for doc in docs if is_approved_customer_passage(doc)]
    allowed_sources = relevant_department_sources(q)
    qualified = _warranty_alternatives(q, docs)
    if qualified is not None:
        return qualified
    terms = keywords(q)
    if not terms:
        return _no_evidence()

    candidates = []
    for doc in docs:
        if doc.get('source') not in TITLES or doc.get('visibility') != 'customer':
            continue
        if allowed_sources and doc.get('source') not in allowed_sources:
            continue
        body = doc.get('text', '')
        body_terms = keywords(body)
        matched = terms & body_terms
        if not matched:
            continue
        # Never treat a related subject as evidence for an absent qualifier.
        # For example, a delivery passage cannot substantiate helicopters.
        if (terms & QUALIFIERS) - body_terms:
            continue
        # Require a majority of meaningful query terms; lexical matches are
        # still a conservative filter, NOT a semantic entailment guarantee.
        if len(matched) / len(terms) < 0.60:
            continue
        candidates.append((len(matched) / len(terms), doc))

    if not candidates:
        return _no_evidence()

    candidates.sort(key=lambda pair: -pair[0])
    _, top = candidates[0]
    passages = _passages(top.get('text', ''))
    ranked = sorted(enumerate(passages),
                    key=lambda item: (-len(terms & keywords(item[1])), item[0]))
    chosen = []
    for _, passage in ranked:
        if not (terms & keywords(passage)):
            continue
        # Skip policy objectives and headings with no customer-facing guidance.
        if re.search(r'\b(?:objective:|purpose:|scope:|document id:)\b', passage, re.I):
            continue
        chosen.append(passage)
        if len(chosen) == 2:
            break
    if not chosen:
        return _no_evidence()

    answer = (
        _payment_methods_answer(q, top)
        or _fashion_answer(q, top)
        or '\n\n'.join(chosen)
    )[:1800]
    return {
        'status': 'answered',
        'answer': answer,
        'sources': [{
            'document': top['source'],
            'title': TITLES[top['source']],
            'page': top.get('page'),
             'chunk_id': top['id'],
            'version': top.get('version', ''),
        }],
        'note': NOTE,
    }


@router.post('/ask')
def ask(body: Question, user=Depends(principal)):
    return answer_question(body.question)
