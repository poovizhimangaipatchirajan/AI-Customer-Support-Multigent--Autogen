"""Focused offline regression tests; no DB, API, AutoGen or OpenAI required."""
import pytest
from backend.services.ticket_classification import classify_ticket


@pytest.mark.parametrize('subject,description,expected', [
    ('Order status', 'Where is my order? It was supposed to arrive yesterday.', ('Shipping & delivery', 'Medium', 'Neutral')),
    ('Return shoes', 'I want to return the shoes I bought last week. How do I get a refund?', ('Returns & refunds', 'Low', 'Neutral')),
    ('Password reset', "I forgot my password and can't log in.", ('Account & login', 'Medium', 'Neutral')),
    ('Broken blender', 'The blender I received is broken. Can I get a replacement?', ('Product & warranty', 'Medium', 'Negative')),
    ('Card charged', "You charged my card three times! Fix this now or I'm contacting my bank and a lawyer.", ('Payments & billing', 'High', 'Negative')),
    ('Corporate gift cards', 'Do you sell gift cards for a company of 500 employees with custom branding?', ('Other / unclassified', 'Low', 'Neutral')),
])
def test_official_scenarios(subject, description, expected):
    assert classify_ticket(subject, description) == expected


def test_risk_flag_always_high():
    assert classify_ticket('General question', 'Please help.', 'LEGAL_THREAT')[1] == 'High'


def test_unknown_request_is_not_invented_category():
    assert classify_ticket('Hello', 'Can you assist?')[0] == 'Other / unclassified'


def test_payment_dispute_category_precedes_refund_keyword():
    assert classify_ticket('Refund disputed charge', 'My card was charged twice; I need a refund.')[0] == 'Payments & billing'
