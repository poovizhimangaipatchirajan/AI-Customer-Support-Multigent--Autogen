"""Deterministic labels for admin rubric reporting, not authorization decisions.

These heuristic labels do not replace QueryAgent output or backend risk checks.
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

HIGH_RISK = re.compile(
    r'\b(?:unauthori[sz]ed|fraud|stolen|legal|lawyer|attorney|lawsuit|police|scam|'
    r'charged?\s+(?:me\s+|my\s+card\s+)?(?:twice|three times|multiple times)|'
    r'(?:double|triple)[ -]?charg(?:e|ed)|contacting my bank)\b'
)
HIGH_URGENCY = re.compile(r'\b(?:urgent|immediately|asap|critical|emergency|several days|multiple times|fix this now)\b')
MEDIUM_URGENCY = re.compile(r'\b(?:delayed|late|missing|failed|broken|damaged|cannot|can.t|not working|issue|problem|yesterday|forgot)\b')
NEGATIVE = re.compile(r'\b(?:angry|frustrat\w*|terrible|disappoint\w*|unacceptable|upset|bad|worst|annoyed|fix this now|broken|charged?\s+(?:me\s+|my\s+card\s+)?(?:twice|three times))\b')
POSITIVE = re.compile(r'\b(?:happy|great|excellent|thank you|satisfied|love)\b')


def classify_ticket(subject, description, risk=''):
    """Return (category, urgency, sentiment) for export display only."""
    content = f'{subject or ""} {description or ""}'.lower()
    category = next((label for label, pattern in CATEGORIES if re.search(pattern, content)), 'Other / unclassified')
    urgency = ('High' if risk or HIGH_RISK.search(content) or HIGH_URGENCY.search(content)
               else 'Medium' if MEDIUM_URGENCY.search(content) else 'Low')
    sentiment = ('Negative' if NEGATIVE.search(content)
                 else 'Positive' if POSITIVE.search(content) else 'Neutral')
    return category, urgency, sentiment
