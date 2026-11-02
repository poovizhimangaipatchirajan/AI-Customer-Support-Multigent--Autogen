"""Run with: python -m pytest tests/test_help_center_regression.py -q"""
from backend.services.help_center import answer_question


class FakeSearch:
    def __init__(self, docs):
        self.docs = docs

    def search(self, question, k=4):
        return self.docs[:k]


def doc(source, text, visibility='customer', chunk_id='test-chunk'):
    return {'id': chunk_id, 'source': source, 'text': text,
            'visibility': visibility, 'version': 'pdf-sop-v1'}


RETURNS = doc('02_Returns_and_Refunds_SOP.pdf',
              '1.2 Category-Specific Return Windows & Constraints '
              'Fashion & Apparel 14 Days Unworn, unwashed, brand tags intact, '
              'original polybag. Return initiation through the customer app.')
WARRANTY = doc('03_Product_and_Warranty_SOP.pdf',
               '1.2 Multi-Tiered Warranty Structure NovaCart 30-Day Platform '
               'Assurance covers manufacturing defects. NovaCare Extended '
               'Protection is optional purchased accidental damage coverage '
               'for up to 36 months.')
BILLING = doc('05_Payments_and_Billing_SOP.pdf',
              '3.3 Cash on Delivery (COD) Discrepancy Protocols: '
              'Overcharging by driver above invoice total. '
              'Audit courier branch cash vault; confirm delivery status.')


def test_hc001_general_returns_has_real_citation():
    result = answer_question('What is NovaCart return policy?', FakeSearch([RETURNS]))
    assert result['status'] == 'answered'
    assert result['sources'][0]['chunk_id'] == 'test-chunk'
    assert '14-day' in result['answer']


def test_hc002_fashion_requirements():
    q = ('What are the return eligibility conditions for Fashion & Apparel '
         'products, including the return window, product condition, and '
         'packaging requirements?')
    result = answer_question(q, FakeSearch([RETURNS]))
    assert result['status'] == 'answered'
    assert 'original polybag' in result['answer']


def test_hc003_no_unsubstantiated_lifetime_warranty():
    q = ('Does NovaCart provide free lifetime warranty coverage for every '
         'product, including accidental damage?')
    result = answer_question(q, FakeSearch([WARRANTY]))
    assert result['status'] == 'answered'
    assert 'could not verify free lifetime' in result['answer']
    assert 'optional' in result['answer'].lower()
    assert '36 months' in result['answer']
    assert result['sources'][0]['document'] == WARRANTY['source']


def test_hc004_helicopter_false_positive():
    q = 'Does NovaCart provide free helicopter delivery for all orders above ₹1,000?'
    result = answer_question(q, FakeSearch([BILLING]))
    assert result['status'] == 'no_evidence'
    assert result['sources'] == []


def test_internal_only_evidence_is_rejected():
    result = answer_question('What is NovaCart return policy?',
                             FakeSearch([doc(RETURNS['source'], RETURNS['text'], 'internal')]))
    assert result['status'] == 'no_evidence'


def test_unapproved_source_is_rejected():
    result = answer_question('What is NovaCart return policy?',
                             FakeSearch([doc('06_Internal_Escalation_SOP.pdf', RETURNS['text'])]))
    assert result['status'] == 'no_evidence'


def test_case_specific_refund_still_requires_ticket():
    result = answer_question('I want a refund for my order', FakeSearch([RETURNS]))
    assert result['status'] == 'ticket_required'
    assert result['sources'] == []


def test_hc003_no_warranty_evidence_still_fails_closed():
    q = 'Does NovaCart provide free lifetime warranty for every product?'
    result = answer_question(q, FakeSearch([BILLING]))
    assert result['status'] == 'no_evidence'
    assert result['sources'] == []


def test_hc003_internal_warranty_cannot_be_quoted():
    q = 'Does NovaCart provide free lifetime warranty for every product?'
    result = answer_question(q, FakeSearch([doc(WARRANTY['source'], WARRANTY['text'], 'internal')]))
    assert result['status'] == 'no_evidence'


def test_hc003_partial_warranty_evidence_does_not_invent_coverage():
    q = 'Does NovaCart provide free lifetime warranty for every product?'
    vague = doc(WARRANTY['source'], 'Warranty information is available.')
    result = answer_question(q, FakeSearch([vague]))
    assert result['status'] == 'no_evidence'
    assert result['sources'] == []


def test_hc002_fashion_answer_excludes_other_categories():
    full = doc(RETURNS['source'], RETURNS['text'] +
               ' Large Home Appliances 10 Days Uninstalled, all manuals.')
    result = answer_question('What are Fashion and Apparel return conditions?', FakeSearch([full]))
    assert result['status'] == 'answered'
    assert '14-day' in result['answer']
    assert 'Large Home Appliances' not in result['answer']
