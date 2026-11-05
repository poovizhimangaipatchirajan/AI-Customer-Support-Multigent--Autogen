"""Unsupported staff promises and app directions; Tamil risk words; app-help passage."""
import pytest

from backend.rag.customer_policy import approved_mfa_guidance, approved_passages
from backend.services.grounding_guard import repair_unsupported_support_claims
from backend.services.risk import screen

ACCOUNT = [p for p in approved_passages() if p['source'] == '04_Account_and_Login_SOP.pdf']
APP_HELP = [p for p in approved_passages() if p['source'] == 'NovaCart_App_Help']
RETURNS = [p for p in approved_passages() if 'orders dashboard' in p['text']]


def test_staff_contact_promises_are_removed():
    draft = ('We understand your concern about the multiple charges on your card. Payment disputes '
             'require specialist review. We have escalated your case to our billing and payments '
             'team for immediate investigation. They will contact you soon with an update.')
    repaired, changed = repair_unsupported_support_claims(draft, [])
    assert changed
    assert 'escalated your case' not in repaired and 'contact you' not in repaired
    assert repaired.startswith('We understand your concern') and 'specialist review' in repaired


@pytest.mark.parametrize('sentence', [
    'Our support team will reach out to you within 24 hours.',
    'A customer care executive will call you shortly.',
    "We'll email you once the investigation is complete.",
    'I have forwarded this to our logistics department.',
])
def test_other_promise_wordings(sentence):
    assert repair_unsupported_support_claims('Thanks for writing. ' + sentence, [])[1]


@pytest.mark.parametrize('sentence', [
    'Please contact NovaCart support with your order details.',  # customer action, from the passage
    'Eligibility is verified during the return process.',
    'Let us know if you need help with the return process.',
])
def test_customer_actions_and_policy_text_are_kept(sentence):
    assert repair_unsupported_support_claims(sentence, [])[1] is False


def test_app_direction_needs_a_passage_that_names_the_screen():
    draft = ('Account recovery requires identity verification. You can reset your password by '
             'using the "Forgot Password" option on the login page.')
    without, changed = repair_unsupported_support_claims(draft, ACCOUNT)
    assert changed and 'Forgot Password' not in without
    assert without == 'Account recovery requires identity verification.'
    kept, changed = repair_unsupported_support_claims(draft, ACCOUNT + APP_HELP)
    assert not changed and kept == draft


def test_documented_orders_dashboard_direction_is_kept():
    draft = 'Please go to your orders dashboard on our website or app and initiate the return request.'
    assert repair_unsupported_support_claims(draft, RETURNS) == (draft, False)
    invented = 'Open the Refund Centre tab in Settings to track your refund.'
    assert repair_unsupported_support_claims(invented, RETURNS)[1]


def test_paragraphs_survive_repair():
    draft = 'First paragraph stays.\n\nWe will contact you tomorrow. Second paragraph stays.'
    repaired, changed = repair_unsupported_support_claims(draft, [])
    assert changed and repaired == 'First paragraph stays.\n\nSecond paragraph stays.'


# ----------------------------------------------------------------- risk --
@pytest.mark.parametrize('text, flag', [
    ('என் கார்டில் மூன்று முறை பணம் எடுத்துவிட்டீர்கள்! வழக்கறிஞரை அணுகுவேன்.', 'LEGAL'),
    ('என் கார்டில் மூன்று முறை பணம் எடுத்துவிட்டீர்கள்!', 'PAYMENT_DISPUTE'),
    ('naan vakeel kitta poven', 'LEGAL'),
    ('card la moonu thadava charge pannitanga', 'PAYMENT_DISPUTE'),
    ('You have taken money from my card three times', 'PAYMENT_DISPUTE'),
    ('You charged my credit card three times!', 'PAYMENT_DISPUTE'),
])
def test_repeated_charge_and_lawyer_wording(text, flag):
    assert flag in screen(text)


@pytest.mark.parametrize('text', [
    'I asked support three times about my order',
    'மூன்று முறை கேட்டேன், பதில் இல்லை',          # "asked three times", no money word
    'It took three times longer than expected to arrive',
])
def test_repetition_without_money_is_not_a_dispute(text):
    assert 'PAYMENT_DISPUTE' not in screen(text)


# ------------------------------------------------------------- app help --
def test_reset_passage_describes_the_real_screen():
    (passage,) = APP_HELP
    for words in ('Forgot password?', 'Verify / Reset', '30 minutes', '12', 'Never share the reset code'):
        assert words in passage['text']


def test_forgotten_password_retrieves_reset_steps_but_mfa_does_not():
    sources = {p['source'] for p in approved_mfa_guidance('I forgot my password and cannot log in')}
    assert sources == {'04_Account_and_Login_SOP.pdf', 'NovaCart_App_Help'}
    mfa = {p['source'] for p in approved_mfa_guidance('I lost my 2FA authenticator, how do I reset it?')}
    assert mfa == {'04_Account_and_Login_SOP.pdf'}


def test_app_help_is_an_account_domain_citation():
    from backend.rag.hybrid import citation_metadata
    from backend.services.evidence_quality import SOURCE_DOMAINS
    assert SOURCE_DOMAINS['NovaCart_App_Help'] == {'account'}
    meta = citation_metadata(APP_HELP[0])
    assert meta['department'] == 'Accounts & Login' and meta['scheme'] == 'NovaCart App Help'


def test_login_screen_offers_forgot_password():
    import inspect
    from frontend import app
    source = inspect.getsource(app)
    assert 'st.expander("Forgot password?")' in source
    assert source.count('password_reset_form(') >= 3  # definition + login + tab
