"""Fixed customer messages the backend sends itself, with reviewed translations.

These are system texts (like any app's string table), chosen by the detected
language of the customer's own message, never by what the question is about.
They never go through a model: no cost, and the wording cannot drift. Every
translation must keep the same placeholders and numbers as its English text
(checked in tests). Have a Tamil speaker review wording changes.
"""
import re

REFUND_CLARIFICATION = (
    'Please provide your order number/reference number and the refund amount in INR. '
    'If the product arrived damaged or defective, attach photographs and describe the issue. '
    'Your order ownership and refund eligibility must be verified. '
    'No refund or replacement has been approved or processed.'
)

AUTO_REFUND_ACCEPTED = (
    "We checked that order {order} belongs to your account and is marked delivered, "
    "and we received your photographs of the damaged or defective item. Your refund "
    "request of INR {amount:.2f} passed our automatic eligibility checks and is queued "
    "for refund processing. This is not a payment confirmation: no money has been "
    "refunded yet, and your photographs are kept for later checks. You will be "
    "notified when processing is complete."
)

TRANSLATIONS = {
    REFUND_CLARIFICATION: {
        'Tamil': (
            'தயவுசெய்து உங்கள் ஆர்டர் எண்/குறிப்பு எண்ணையும், திருப்பித் தர வேண்டிய தொகையையும் '
            '(INR-இல்) தெரிவிக்கவும். பொருள் சேதமடைந்தோ குறைபாட்டுடனோ வந்திருந்தால், '
            'புகைப்படங்களை இணைத்து பிரச்சனையை விவரிக்கவும். உங்கள் ஆர்டர் உரிமையும் '
            'பணம் திருப்பித் தரும் தகுதியும் சரிபார்க்கப்பட வேண்டும். இதுவரை எந்தப் பணத் '
            'திருப்பமோ மாற்றுப் பொருளோ அங்கீகரிக்கப்படவோ செயல்படுத்தப்படவோ இல்லை.'
        ),
        'Tanglish': (
            'Dayavu senju unga order number/reference number-um, refund aaga vendiya '
            'thogai-yum (INR-la) sollunga. Product damage-aa illa defect-oda vandhirundha, '
            'photos attach panni problem-a vivarichu sollunga. Unga order ownership-um refund '
            'eligibility-um verify pannanum. Innum endha refund-um replacement-um approve '
            'pannala, process-um pannala.'
        ),
    },
    AUTO_REFUND_ACCEPTED: {
        'Tamil': (
            'ஆர்டர் {order} உங்கள் கணக்கைச் சேர்ந்தது என்பதையும், அது டெலிவர் செய்யப்பட்டதாகக் '
            'குறிக்கப்பட்டுள்ளது என்பதையும் சரிபார்த்தோம்; சேதமடைந்த அல்லது குறைபாடுள்ள '
            'பொருளின் உங்கள் புகைப்படங்களும் கிடைத்தன. INR {amount}-க்கான உங்கள் பணம் '
            'திருப்பக் கோரிக்கை எங்கள் தானியங்கி தகுதிச் சோதனைகளில் தேறி, பணம் திருப்பும் '
            'செயலாக்கத்திற்கான வரிசையில் உள்ளது. இது பணம் செலுத்தியதற்கான உறுதிப்படுத்தல் '
            'அல்ல: இதுவரை எந்தப் பணமும் திருப்பித் தரப்படவில்லை; உங்கள் புகைப்படங்கள் பின்னர் '
            'சரிபார்க்க வைத்திருக்கப்படும். செயலாக்கம் முடிந்ததும் உங்களுக்குத் தெரிவிக்கப்படும்.'
        ),
        'Tanglish': (
            'Order {order} unga account-oda-dhu, adhu delivered-nu mark aagirukku-nu check '
            'pannitom; damage/defect aana product-oda unga photos-um kidaichiduchu. INR {amount}-ku '
            'unga refund request namma automatic eligibility checks-la pass aagi, refund '
            'processing-ku queue-la irukku. Idhu payment confirmation illa: innum endha panamum '
            'refund aagala, unga photos pinnaadi check panna vechirukkom. Processing mudinjadhum '
            'ungalukku theriyappaduthuvom.'
        ),
    },
}

_PLACEHOLDERS = {'order': r'(?P<order>[A-Za-z0-9-]+)', 'amount': r'(?P<amount>\d+\.\d{2})'}


def _pattern(template):
    """English template -> exact-match regex with named placeholder groups."""
    parts = re.split(r'\{(\w+)(?::[^}]*)?\}', template)
    return re.compile(''.join(
        re.escape(part) if i % 2 == 0 else _PLACEHOLDERS[part] for i, part in enumerate(parts)))


_PATTERNS = [(_pattern(english), english) for english in TRANSLATIONS]


def fixed_translation(text, language):
    """The reviewed translation of a known fixed message, or None."""
    for pattern, english in _PATTERNS:
        match = pattern.fullmatch((text or '').strip())
        if match and language in TRANSLATIONS[english]:
            return TRANSLATIONS[english][language].format(**match.groupdict())
    return None


def localized(text, language):
    """Customer text for a fixed message: translation first, English below."""
    from backend.services.language import bilingual
    translation = fixed_translation(text, language)
    return bilingual(translation, text) if translation else text
