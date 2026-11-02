import pytest
from backend.services.risk import screen

@pytest.mark.parametrize('message,amount,expected', [
    ('Refund me for my damaged item',499,False),
    ('Refund me for my damaged item',500,True),
    ('Refund me for my damaged item',5000,True),
    ('I want my ₹750 back',None,True),
    ('I need my 800 rupees back',None,True),
    ('Enakku 750 rupees refund venum',None,True),
    ('எனக்கு 750 ரூபாய் திருப்பி வேண்டும்',None,True),
    ('Refund me ₹750',499,False), # mismatch must still escalate, but not as confirmed >=500
])
def test_refund_cases(message,amount,expected):
    flags=screen(message,amount)
    if 'Refund me ₹750' in message:
        assert 'REFUND_AMOUNT_UNVERIFIED' in flags
    else:
        assert ('REFUND_GE_500' in flags) == expected

def test_unknown_amount_escalates():
    assert 'REFUND_AMOUNT_UNVERIFIED' in screen('Please refund my order')

def test_foreign_currency_escalates():
    assert 'REFUND_CURRENCY_UNCERTAIN' in screen('Refund me $20',20)

@pytest.mark.parametrize('message', [
    'My card was debited twice', 'I was charged two times',
    'என் பணம் இரண்டு முறை பிடித்தார்கள்', 'rendu thadava debit aachu',
])
def test_payment_disputes(message):
    assert 'PAYMENT_DISPUTE' in screen(message)

def test_fraud_and_legal():
    assert 'FRAUD' in screen('This is a scam')
    assert 'LEGAL' in screen('I will take legal action')

def test_general_policy_question():
    assert screen('What is the refund policy?') == []

def test_large_or_invalid_amounts():
    assert 'REFUND_AMOUNT_UNVERIFIED' in screen('Refund me', float('nan'))
    assert 'REFUND_GE_500' in screen('Refund me', 10**20)
