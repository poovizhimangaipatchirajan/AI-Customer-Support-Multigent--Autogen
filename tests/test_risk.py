from backend.services.risk import screen

def test_refund_boundary():
    assert 'REFUND_GE_500' not in screen('I want a refund',499)
    assert 'REFUND_GE_500' in screen('I want a refund',500)
def test_fraud():
    assert 'FRAUD' in screen('This is fraud',50)
def test_general_policy():
    assert not screen('What is the refund policy for ₹500?',500)
