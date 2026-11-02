"""Deterministic, conservative complaint risk screening (no refund execution)."""
import re
from decimal import Decimal, InvalidOperation

FLAGS = {
    'FRAUD': r'\bfraud(?:s|ulent|ulently)?\b|\bscam(?:med|ming|s)?\b|identity theft|phishing|ஏமாற்ற|மோசடி|mosadi|emath',
    'LEGAL': r'\blegal\b|\blawyer\b|\bsue\b|\bcourt\b|வழக்கு|நீதிமன்ற|vazhakku',
    'PAYMENT_DISPUTE': r'charged twice|triple charg|double charg|unauthori[sz]ed|unrecogni[sz]ed charge|payment dispute|chargeback|debited twice|deducted twice|charged two times|debited two times|double debit|duplicate (?:payment|charge|debit)|பணம் இரண்டு முறை|இரண்டு முறை பிடித்த|ரெண்டு தடவை|rendu (?:thadava|thadavai|mur[aie])|debit aachu twice',
}
REFUND = re.compile(r'\b(?:i (?:need|would like|require|am requesting|am asking for) (?:a |my )?refund|need (?:a |my )?refund|refund me|refund my|want (?:a |my )?refund|request (?:a |my )?refund|refund for my|refund of|money back|please refund|return my money|give my money back|(?:need|want) my (?:rs\.?\s*|inr\s*|₹\s*)?[\d,]+(?:\.\d+)?\s*(?:rupees?|rs\.?)?\s*back|(?:refund|money)\s+venum|(?:panam|kaasu|kasu)\s+(?:thiruppi|thirumbi|return|back)|thiruppi\s+(?:venum|kodu|kudunga))\b|திருப்பி\s*(?:வேண்டும்|கொடு|தர)|பணம்\s*திருப்பி|ரீஃபண்ட்\s*(?:வேண்டும்|கொடு)|ரீபண்ட்\s*(?:வேண்டும்|கொடு)', re.I)
SHORT_REFUND = re.compile(r'\brefund\s*(?:for\s*)?(?:₹\s*|rs\.?\s*|inr\s*)?(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rupees?)?\b', re.I)
GENERAL = re.compile(r'\b(?:what is|what are|explain|tell me|how does|how do|can you explain|policy|policies|eligib|rules|procedure|guidelines)\b|(?:கொள்கை|விதிமுறை|எப்படி|விளக்க)', re.I)
CASE = re.compile(r'\b(?:my|mine|me|i |enakku|ennoda|en|naan|na|venum|kudunga|kodu|return|back|request|please)\b|(?:எனக்கு|என்னுடைய|என்|வேண்டும்|தரவும்|கொடு)', re.I)
FOREIGN = re.compile(r'\b(?:usd|eur|gbp|dollars?|euros?|pounds?)\b|[$€£]', re.I)
AMOUNT = re.compile(r'(?<![\w])(?:₹\s*|rs\.?\s*|inr\s*)?(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rupees?|ரூபாய்|rupai|rooba|rs\.?)?(?![\w])', re.I)
CURRENCY_CONTEXT = re.compile(r'₹|\brs\.?\b|\binr\b|rupees?|ரூபாய்|rupai|rooba', re.I)

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

def screen(text: str, amount: float | None = None) -> list[str]:
    """Return backend escalation reasons. Ambiguous refund cases fail closed."""
    t = (text or '').casefold()
    flags = [label for label, pattern in FLAGS.items() if re.search(pattern, t, re.I)]
    refund_case = bool(REFUND.search(t) or SHORT_REFUND.search(t))
    # A plain policy question is not a customer-specific refund request.
    if refund_case and GENERAL.search(t) and not CASE.search(t):
        refund_case = False
    if not refund_case:
        return flags
    if FOREIGN.search(t):
        flags.append('REFUND_CURRENCY_UNCERTAIN')
        return flags
    supplied = _money(amount) if amount is not None else None
    short_amount = refund_amount_from_text(t)
    if amount is not None and supplied is None:
        flags.append('REFUND_AMOUNT_UNVERIFIED')
        return flags
    # Only parse unstructured amounts with an explicit monetary marker.
    # Never interpret an order number or date as a refund amount.
    extracted = []
    for match in AMOUNT.finditer(t):
        raw = match.group(0)
        if CURRENCY_CONTEXT.search(raw):
            parsed = _money(match.group(1))
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
