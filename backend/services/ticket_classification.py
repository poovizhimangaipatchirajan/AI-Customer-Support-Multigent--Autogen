"""Ticket labels for the complaint log and dashboard, never authorization decisions.

QueryAgent's own classification is preferred (parse_query_classification); the
deterministic keyword rules below are the fallback when the agent output is
missing or invalid. Emotion affects tone and reporting only, never escalation.
"""
import re

CATEGORIES = (
    # Billing wording only: a device that 'does not charge' or a 'charging cable'
    # is not a payment issue.
    ('Payments & billing', r'\b(?:payments?|billing|billed|charged|charges|overcharg\w*|charge\s+(?:on|to)\s+my|transaction|invoice|debited?|double[ -]?charg(?:e|ed)|triple[ -]?charg(?:e|ed)|bank|card)\b'),
    ('Returns & refunds', r'\b(?:refund|returns?|reimbursement|money back)\b'),
    ('Account & login', r'\b(?:password|log[ -]?in|account|otp|sign[ -]?in|reset password)\b'),
    ('Shipping & delivery', r'\b(?:ship(?:ping|ment)?|deliver(?:y|ed)?|tracking|parcel|courier|order delay|arriv(?:e|ed|al)|where is my order)\b'),
    ('Product & warranty', r'\b(?:product|warranty|damaged|defective|replacement|replace|broken|blender|faulty)\b'),
)
CATEGORY_LABELS = tuple(label for label, _ in CATEGORIES) + ('Other / unclassified',)
URGENCY_LABELS = ('Low', 'Medium', 'High')
EMOTION_LABELS = ('Angry', 'Frustrated', 'Sad', 'Worried', 'Confused', 'Happy', 'Neutral')

HIGH_RISK = re.compile(
    r'\b(?:unauthori[sz]ed|fraud|stolen|legal|lawyer|attorney|lawsuit|police|scam|'
    r'charged?\s+(?:me\s+|my\s+card\s+)?(?:twice|three times|multiple times)|'
    r'(?:double|triple)[ -]?charg(?:e|ed)|contacting my bank)\b'
)
HIGH_URGENCY = re.compile(r'\b(?:urgent|immediately|asap|critical|emergency|several days|multiple times|fix this now)\b')
MEDIUM_URGENCY = re.compile(r'\b(?:delayed|late|missing|failed|broken|damaged|cannot|can.t|not working|issue|problem|yesterday|forgot)\b')

# Fallback emotion cues, checked in this order (strongest first).
EMOTION_RULES = (
    ('Angry', re.compile(r"\b(?:angry|furious|outraged|unacceptable|ridiculous|worst|disgusting|"
                         r"fix this now|lawyer|sue|scam|cheat\w*)\b|!{2,}", re.I)),
    ('Frustrated', re.compile(r"\b(?:frustrat\w*|annoy\w*|fed up|again and again|still (?:not|no|haven.t)|"
                              r"keeps? (?:happening|failing)|multiple times|several times|nobody|no one (?:has|is))\b", re.I)),
    ('Sad', re.compile(r"\b(?:disappointed|disappointing|sad|upset|unhappy|let down|heartbroken)\b", re.I)),
    ('Worried', re.compile(r"\b(?:worried|concerned|afraid|anxious|scared|nervous|panicking)\b", re.I)),
    ('Confused', re.compile(r"\b(?:confused|confusing|don.t understand|do not understand|unclear|makes no sense)\b", re.I)),
    ('Happy', re.compile(r"\b(?:thank you|thanks|great|happy|love|satisfied|excellent|appreciate\w*)\b", re.I)),
)


def emotion_from_text(text):
    return next((label for label, pattern in EMOTION_RULES if pattern.search(text or '')), 'Neutral')


def classify_ticket(subject, description, risk=''):
    """Fallback (category, urgency, emotion) for export display only."""
    content = f'{subject or ""} {description or ""}'.lower()
    category = next((label for label, pattern in CATEGORIES if re.search(pattern, content)), 'Other / unclassified')
    urgency = ('High' if risk or HIGH_RISK.search(content) or HIGH_URGENCY.search(content)
               else 'Medium' if MEDIUM_URGENCY.search(content) else 'Low')
    return category, urgency, emotion_from_text(f'{subject or ""} {description or ""}')


def _field(text, name):
    match = re.search(r'(?im)^[\s*_-]*' + re.escape(name) + r'[\s*_]*:[\s*_]*([^\n]+)', text)
    return match.group(1).strip(' *_.') if match else None


def _pick(value, allowed):
    if not value:
        return None
    return next((label for label in allowed if label.lower() == value.strip().lower()), None)


def parse_query_classification(text):
    """Read QueryAgent's structured fields; keep only known labels.

    Returns {'category', 'urgency', 'emotion'} with None for anything missing
    or outside the allowed vocabulary, or None when nothing is usable.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    result = {
        'category': _pick(_field(text, 'Category'), CATEGORY_LABELS),
        'urgency': _pick(_field(text, 'Urgency'), URGENCY_LABELS),
        'emotion': _pick(_field(text, 'Emotion'), EMOTION_LABELS),
    }
    return result if any(result.values()) else None
