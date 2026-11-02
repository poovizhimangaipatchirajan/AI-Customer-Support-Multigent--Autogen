from backend.services.risk import screen

def test_boundary():
 assert 'REFUND_GE_500' not in screen('I want a refund',499)
 assert 'REFUND_GE_500' in screen('I want a refund',500)
 assert 'REFUND_GE_500' in screen('I want a refund',501)
def test_fraud_always_review():assert 'FRAUD' in screen('This is fraud, refund me',50)
def test_currency_ambiguous():assert 'REFUND_CURRENCY_UNCERTAIN' in screen('Please refund $20',20)
def test_general_policy():assert not screen('What is the general refund policy?',10000)
