from backend.services.ticket_classification import classify_ticket
from backend.services.grounding_guard import check_customer_visibility

def test_shipping_inquiry_labels():
    assert classify_ticket('Shipping and delivery policy clarification', 'Please explain tracking and delays') == ('Shipping & delivery', 'Low', 'Neutral')

def test_risk_priority():
    assert classify_ticket('Unauthorized charge', 'I am frustrated about a payment', 'PAYMENT_DISPUTE') == ('Payments & billing', 'High', 'Negative')

def test_suspicious_details_blocked():
    assert check_customer_visibility('We conduct geofence audits and driver GPS verification.') == 'SENSITIVE_OPERATIONAL_DETAIL'
    assert check_customer_visibility('Tracking updates every 15 minutes.') == 'SENSITIVE_OPERATIONAL_DETAIL'
    assert check_customer_visibility('We provide delivery tracking after dispatch.') is None
