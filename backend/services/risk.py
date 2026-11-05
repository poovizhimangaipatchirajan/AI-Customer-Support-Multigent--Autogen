"""Deterministic, conservative complaint risk screening (no refund execution)."""
import re
from decimal import Decimal, InvalidOperation

FLAGS = {
    'FRAUD': r'\bfraud(?:s|ulent|ulently)?\b|\bscam(?:med|ming|s)?\b|identity theft|phishing|ஏமாற்ற|மோசடி|mosadi|emath',
    # Tamil: வழக்கறிஞர் (lawyer), வக்கீல் (lawyer), சட்ட நடவடிக்கை (legal action).
    'LEGAL': r'\blegal\b|\blawyer\b|\bsue\b|\bcourt\b|வழக்கு|வழக்கறிஞ|வக்கீல்|சட்ட\s*நடவடிக்கை|நீதிமன்ற|vazhakku|\bvakk?eel\b|\bvakil\b',
    # Repeated-charge wording: "charged my card three times", "billed 3 times",
    # "multiple charges", "charged more than once".
    'PAYMENT_DISPUTE': r'\b(?:charged|debited|deducted|billed)\s+(?:me\s+|my\s+(?:card|account|bank(?:\s+account)?|upi|wallet)\s+)?(?:two|three|four|five|several|multiple|many|\d+)\s+times\b|\b(?:charged|debited|deducted|billed)\s+(?:me\s+|my\s+(?:card|account)\s+)?more than once\b|\b(?:two|three|four|five|several|multiple|duplicate|double|repeated)\s+(?:charges|debits|deductions|transactions)\b|charged twice|triple charg|double charg|unauthori[sz]ed|unrecogni[sz]ed charge|payment dispute|chargeback|debited twice|deducted twice|charged two times|debited two times|double debit|duplicate (?:payment|charge|debit)|பணம் இரண்டு முறை|இரண்டு முறை பிடித்த|ரெண்டு தடவை|rendu (?:thadava|thadavai|mur[aie])|debit aachu twice'
        # Gapped and translated wording: "charged my credit card three times",
        # "taken money from my card three times".
        r'|\b(?:charged|debited|deducted|billed)\b[^.!?]{0,40}?\b(?:two|three|four|five|several|multiple|many|\d+)\s+times\b'
        r'|\b(?:taken|took|withdrawn|withdrew|deducted|charged)\s+(?:the\s+|my\s+)?(?:money|payment|amount)\b[^.!?]{0,40}?\b(?:two|three|four|five|several|multiple|many|\d+)\s+times\b'
        # Tamil / Tanglish repeated charges, only next to a money word.
        r'|(?:பணம்|கட்டணம்|கார்டு|கார்டில்|டெபிட்|சார்ஜ்)[^.!?]{0,40}(?:இரண்டு|ரெண்டு|மூன்று|மூணு|நான்கு|பல)\s*(?:முறை|தடவை)'
        r'|(?:இரண்டு|ரெண்டு|மூன்று|மூணு|நான்கு|பல)\s*(?:முறை|தடவை)[^.!?]{0,40}(?:பணம்|கட்டணம்|எடுத்த|பிடித்த|டெபிட்|சார்ஜ்)'
        r'|(?:charge|panam|kaasu|debit|cut|card)\w*[^.!?]{0,40}\b(?:rendu|moonu|munu|naalu|pala)\s+(?:thadava\w*|murai|times?)\b'
        r'|\b(?:rendu|moonu|munu|naalu|pala)\s+(?:thadava\w*|murai|times?)\b[^.!?]{0,40}(?:charge|panam|kaasu|debit|cut)',
}
REFUND = re.compile(r'\b(?:i (?:need|would like|require|am requesting|am asking for) (?:a |my )?(?:full |complete |partial |total )?refund|need (?:a |my )?(?:full |complete |partial |total )?refund|refund me|refund my|want (?:a |my )?(?:full |complete |partial |total )?refund|request (?:a |my )?(?:full |complete |partial |total )?refund|refund for my|refund of|give me (?:a |an )?(?:full |complete |partial |total )?refund|money back|please refund|return my money|give my money back|(?:need|want) my (?:rs\.?\s*|inr\s*|₹\s*)?[\d,]+(?:\.\d+)?\s*(?:rupees?|rs\.?)?\s*back|(?:refund|money)\s+venum|(?:panam|kaasu|kasu)\s+(?:thiruppi|thirumbi|return|back)|thiruppi\s+(?:venum|kodu|kudunga))\b|திருப்பி\s*(?:வேண்டும்|கொடு|தர)|பணம்\s*திருப்பி|ரீஃபண்ட்\s*(?:வேண்டும்|கொடு)|ரீபண்ட்\s*(?:வேண்டும்|கொடு)', re.I)
SHORT_REFUND = re.compile(r'\brefund\s*(?:for\s*)?(?:₹\s*|rs\.?\s*|inr\s*)?(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rupees?)?\b', re.I)
GENERAL = re.compile(r'\b(?:what is|what are|explain|tell me|how does|how do|can you explain|policy|policies|eligib|rules|procedure|guidelines)\b|(?:கொள்கை|விதிமுறை|எப்படி|விளக்க)', re.I)
CASE = re.compile(r'\b(?:my|mine|me|i |enakku|ennoda|en|naan|na|venum|kudunga|kodu|return|back|request|please)\b|(?:எனக்கு|என்னுடைய|என்|வேண்டும்|தரவும்|கொடு)', re.I)
FOREIGN = re.compile(r'\b(?:usd|eur|gbp|dollars?|euros?|pounds?)\b|[$€£]', re.I)
AMOUNT = re.compile(r'(?<![\w])(?:₹\s*|rs\.?\s*|inr\s*)?(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rupees?|ரூபாய்|rupai|rooba|rs\.?)?(?![\w])', re.I)
CURRENCY_CONTEXT = re.compile(r'₹|\brs\.?\b|\binr\b|rupees?|ரூபாய்|rupai|rooba', re.I)

_CUR = r'(?:₹\s*|rs\.?\s*|inr\s*)'
_NUM = r'(\d[\d,]*(?:\.\d{1,2})?)'
# Amount must carry an explicit currency marker (prefix or rupee suffix).
_ATTRIBUTED_AMOUNT = re.compile(
    r'\brefund\s+(?:me|my)\s+' + _CUR + _NUM + r'(?![\w])'
    r'|\b(?:my|the)\s+(?:' + _CUR + _NUM + r'|(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rupees?|rs\.?))\s*(?:back|refund)\b'
    r'|' + _CUR + _NUM + r'\s*(?:back|refund)\b'
    r'|(?<![\w])(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rupees?|rs\.?)\s*(?:back|refund)\b'
    r'|(?<![\w])(\d[\d,]*(?:\.\d{1,2})?)\s*ரூபாய்\s*(?:திருப்பி|ரீஃபண்ட்|ரீபண்ட்)',
    re.I)

def _money(value):
    try:
        v = Decimal(str(value).replace(',', ''))
        return v if v.is_finite() and v >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None

def refund_amount_from_text(text: str):
    """Only amounts immediately following 'refund'; never infer unrelated order IDs."""
    matches = {_money(m.group(1)) for m in SHORT_REFUND.finditer(text or '')}
    matches.discard(None)
    return next(iter(matches)) if len(matches) == 1 else None

# Policy guidance is not authorization to perform a refund. Distinguish
# informational questions from direct refund instructions conservatively.
_INFORMATION = re.compile(
    r"\b(?:explain|describe|what(?:'s| is| are)|how (?:do|can|does|would)|"
    r"tell me about|information about|guidance on|steps to|policy|policies|"
    r"eligibility|eligible|return window|procedure|guidelines)\b", re.I)
_EXECUTION = re.compile(
    r"\b(?:refund (?:me|my|this|the)|please refund|issue (?:me )?(?:a |the )?refund|"
    r"process (?:my|the|this) refund|send (?:me )?(?:my|the) money|"
    r"give me (?:my money|(?:a |an )?(?:full |complete |partial |total )?refund)|i (?:demand|request|need|want|require|"
    r"would like|am requesting|am asking for) (?:an? |my )?(?:full |complete |partial |total )?refund|"
    r"need (?:a |my )?(?:full |complete |partial |total )?refund|want (?:a |my )?(?:full |complete |partial |total )?refund|"
    r"return my money|give my money back)\b", re.I)

def is_refund_information_request(text: str, amount=None) -> bool:
    """Only exempt clear guidance requests without a refund amount or execution demand."""
    t = text or ''
    t_execution = re.sub(r'\bhow\s+(?:do|can|would)\s+i\s+request\s+(?:a\s+)?refund\b', 'refund procedure', t, flags=re.I)
    return bool(_INFORMATION.search(t) and not _EXECUTION.search(t_execution)
                and amount is None and refund_amount_from_text(t) is None
                and not FOREIGN.search(t)
                and not any(CURRENCY_CONTEXT.search(m.group(0))
                            for m in AMOUNT.finditer(t)))

def screen(text: str, amount: float | None = None) -> list[str]:
    """Return backend escalation reasons. Ambiguous refund cases fail closed."""
    t = (text or '').casefold()
    flags = [label for label, pattern in FLAGS.items() if re.search(pattern, t, re.I)]
    refund_case = bool(REFUND.search(t) or SHORT_REFUND.search(t))
    if is_refund_information_request(t, amount):
        refund_case = False
    # A plain policy question is not a customer-specific refund request.
    if refund_case and GENERAL.search(t) and not CASE.search(t):
        refund_case = False
    if not refund_case:
        supplied = _money(amount) if amount is not None else None
        if supplied is not None and supplied >= Decimal('500'):
            flags.append('REFUND_GE_500')
        elif amount is not None and supplied is None:
            flags.append('REFUND_AMOUNT_UNVERIFIED')
        return flags
    if FOREIGN.search(t):
        flags.append('REFUND_CURRENCY_UNCERTAIN')
        return flags
    supplied = _money(amount) if amount is not None else None
    short_amount = refund_amount_from_text(t)
    if amount is not None and supplied is None:
        flags.append('REFUND_AMOUNT_UNVERIFIED')
        return flags
    # Only compare amounts explicitly attributed to the requested refund.
    # Product prices, order totals and unit prices are NOT refund requests.
    # A missing/ambiguous refund amount still fails closed below.
    extracted = []
    for match in re.finditer(
        r'\b(?:refund(?:\s+(?:amount|request))?\s*(?:of|for|is|:)?\s*|'
        r'requested\s+refund\s+amount\s*(?:is|of|:)?\s*|'
        r'(?:want|request|need)\s+(?:a\s+)?refund\s+(?:of|for)\s*)'
        r'((?:₹\s*|rs\.?\s*|inr\s*)\d[\d,]*(?:\.\d{1,2})?)', t, re.I):
        monetary = AMOUNT.search(match.group(1))
        if monetary:
            parsed = _money(monetary.group(1))
            if parsed is not None:
                extracted.append(parsed)
    # Currency-marked amounts attached directly to refund wording, in either order
    # ("refund me INR 500", "my 800 rupees back", "750 rupees refund"). A bare number
    # or an amount not next to refund wording is never treated as the refund amount.
    for match in _ATTRIBUTED_AMOUNT.finditer(t):
        parsed = _money(next((g for g in match.groups() if g), None))
        if parsed is not None:
            extracted.append(parsed)
    if short_amount is not None and short_amount not in extracted:
        extracted.append(short_amount)
    if supplied is not None and extracted and any(x != supplied for x in extracted):
        flags.append('REFUND_AMOUNT_UNVERIFIED')
    elif supplied is not None:
        if supplied >= Decimal('500'):
            flags.append('REFUND_GE_500')
    elif len(set(extracted)) == 1:
        if extracted[0] >= Decimal('500'):
            flags.append('REFUND_GE_500')
    else:
        flags.append('REFUND_AMOUNT_UNVERIFIED')
    return flags
