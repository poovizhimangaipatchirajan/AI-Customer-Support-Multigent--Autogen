
"""Conservative checks for explicit numeric policy claims.

Not a semantic entailment model. Case amounts and authorization
requirements must be verified separately.
"""
import re

_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
_VALUE = re.compile(
    r"(?<!\w)(?:₹\s*|INR\s*|Rs\.?\s*)\d[\d,]*(?:\.\d+)?\b"
    r"|(?<!\w)\d+(?:\.\d+)?\s*%",
    re.I,
)
_POLICY = re.compile(
    r"\b(?:fee|charge|cost|limit|cap|threshold|discount|refunds?|"
    r"return|warranty|shipping|delivery|replacement|penalty|"
    r"tax|cashback|commission|deduction|eligible|eligibility)\b",
    re.I,
)
_NEGATED = re.compile(
    r"\b(?:not|never|cannot|can't|doesn't|isn't|no)\b", re.I
)


def _normalized_value(value):
    cleaned = re.sub(r"[\s,]", "", value.lower())
    return re.sub(r"^(?:₹|inr|rs\.?)", "inr", cleaned)


# Application safety rule: INR 500 inclusive requires human review.
_REVIEW_GATE = re.compile(
    r"\b(?:refund\s+)?(?:requests?|refunds?)\s+"
    r"(?:of|for|worth|at|above|over)\s*"
    r"(?:₹\s*|INR\s*|Rs\.?\s*)500(?:\.00)?\s*"
    r"(?:or\s+more|and\s+above|or\s+above|\+)?\s*"
    r"(?:will\s+|must\s+|shall\s+)?"
    r"(?:require|requires|need|needs|undergo)\s+"
    r"(?:a\s+)?(?:human|manual|manager|supervisor)\s+"
    r"(?:review|approval)\b",
    re.I,
)

_CUSTOMER_AMOUNT = re.compile(
    r"\b(?:your|the customer's|customer-reported)\s+"
    r"(?:order\s+)?(?:amount|price|value|total)\s+"
    r"(?:is|was|of|is\s+for|was\s+for)\s*"
    r"(?:₹\s*|INR\s*|Rs\.?\s*)\d[\d,]*(?:\.\d+)?\b",
    re.I,
)

# A restated customer amount ("your order total was ₹X", "your product cost
# ₹X", "you paid ₹X"). Exempt only when the same value appears in the
# customer's own message (checked by the callers), never as policy.
_CASE_AMOUNT = re.compile(
    r"(?:\b(?:your|the customer.s|customer-reported)\s+"
    r"(?:(?:requested\s+)?(?:order|refund)\s+)?"
    r"(?:amount|price|value|total|request)\s+"
    r"(?:is|was|of|is\s+for|was\s+for)\s*"
    r"|\b(?:your|the)\s+(?:product|item|order|purchase)(?:'s|’s)?\s+"
    r"(?:cost|costs|price|total|value|amount|was\s+priced\s+at|is\s+priced\s+at|priced\s+at|worth)"
    r"(?:\s+(?:is|was|of))?\s*"
    r"|\byou\s+(?:paid|spent)\s*)"
    r"(?:₹\s*|INR\s*|Rs\.?\s*)\d[\d,]*(?:\.\d+)?\b",
    re.I,
)


def _verified_case_amount_sub(sentence, customer_message):
    if not isinstance(customer_message, str) or not customer_message.strip():
        return sentence

    supplied = {
        _normalized_value(m.group())
        for m in _VALUE.finditer(customer_message)
        if "%" not in m.group()
    }

    def replace(match):
        amounts = {
            _normalized_value(m.group())
            for m in _VALUE.finditer(match.group())
        }
        return " " if amounts and amounts <= supplied else match.group()

    return _CASE_AMOUNT.sub(replace, sentence)


# Accept the exact INR 500 comparison only when the same sentence
# contains a verified customer amount of at least INR 500.
_REVIEW_COMPARISON = re.compile(
    r"\bwhich\s+is\s+"
    r"(?:above|over|at\s+least|equal\s+to\s+or\s+above)\s+"
    r"(?:₹\s*|INR\s*|Rs\.?\s*)500(?:\.00)?\s*,?\s*"
    r"(?:it|this\s+case|the\s+request)\s+"
    r"(?:will\s+|must\s+|shall\s+)?"
    r"(?:require|requires|need|needs)\s+"
    r"(?:a\s+)?(?:human|manual|manager|supervisor)\s+"
    r"(?:review|approval)\b",
    re.I,
)


def _remove_verified_review_comparison(sentence, customer_message):
    if not isinstance(customer_message, str):
        return sentence

    supplied = {
        _normalized_value(m.group())
        for m in _VALUE.finditer(customer_message)
        if "%" not in m.group()
    }

    case_values = [
        _normalized_value(m.group())
        for match in _CASE_AMOUNT.finditer(sentence)
        for m in _VALUE.finditer(match.group())
    ]

    valid = any(
        value in supplied
        and value.startswith("inr")
        and float(value[3:]) >= 500
        for value in case_values
    )

    if valid:
        return _REVIEW_COMPARISON.sub(" ", sentence)
    return sentence


def unsupported_quantitative_claim(
    draft, evidence_texts, customer_message=None
):
    """Flag unsupported monetary or percentage policy claims.

    Customer amounts are exempt only when matched against the
    supplied customer message. This does not authorize refunds.
    """
    for sentence in _SENTENCES.split(draft):
        sentence = _REVIEW_GATE.sub(" ", sentence)
        sentence = _remove_verified_review_comparison(
            sentence, customer_message
        )
        sentence = _verified_case_amount_sub(
            sentence, customer_message
        )

        values = {
            _normalized_value(m.group())
            for m in _VALUE.finditer(sentence)
        }
        terms = {
            m.group().lower()
            for m in _POLICY.finditer(sentence)
        }

        if not values or not terms or _NEGATED.search(sentence):
            continue

        for value in values:
            supported = False

            for text in evidence_texts:
                for fragment in _SENTENCES.split(text):
                    evidence_values = {
                        _normalized_value(m.group())
                        for m in _VALUE.finditer(fragment)
                    }
                    evidence_terms = {
                        m.group().lower()
                        for m in _POLICY.finditer(fragment)
                    }

                    if (
                        value in evidence_values
                        and terms.intersection(evidence_terms)
                    ):
                        supported = True
                        break

                if supported:
                    break

            if not supported:
                return True

    return False
