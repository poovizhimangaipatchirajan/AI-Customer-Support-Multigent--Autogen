"""Small deterministic smoke suite; no LLM, network, or DB access."""
import pytest
from backend.services.grounding_guard import (
    check_response_grounding, check_customer_visibility, customer_safe_passages,
)

SHIPPING = {"id": "shipping-1", "source": "01_Shipping_and_Delivery_SOP.pdf", "page": 1,
            "visibility": "customer", "text": "An order has a promised Estimated Delivery Date (EDD). Merchant dispatch manifests are generated within 48 business hours."}
RETURNS = {"id": "return-1", "source": "02_Returns_and_Refunds_SOP.pdf", "page": 1,
           "visibility": "customer", "text": "Returns are subject to eligibility checks."}
OTHER = [
    ("03_Product_and_Warranty_SOP.pdf", "Product warranty terms apply."),
    ("04_Account_and_Login_SOP.pdf", "Account verification is required."),
    ("05_Payments_and_Billing_SOP.pdf", "Payment disputes need investigation."),
]

@pytest.mark.parametrize("draft", [
    "NovaCart's standard delivery timeframe varies by fulfillment channel: FBN and DMF.",
    "Your order will arrive within 48 business hours.",
])
def test_shipping_misrepresentation_blocked(draft):
    assert check_response_grounding(draft, [SHIPPING]) is not None

@pytest.mark.parametrize("draft", [
    "The SOP refers to a promised Estimated Delivery Date but does not specify a universal delivery duration.",
    "The 48-business-hour dispatch-manifest requirement is not a delivery timeframe.",
    "The policy does not provide a location-based delivery schedule.",
])
def test_accurate_shipping_limitations_not_blocked(draft):
    assert check_response_grounding(draft, [SHIPPING]) is None

@pytest.mark.parametrize("source,body", OTHER)
def test_unrelated_customer_policy_not_removed(source, body):
    p = {"id": source, "source": source, "page": 1, "visibility": "customer", "text": body}
    assert customer_safe_passages([p]) == [p]

def test_returns_evidence_unchanged():
    assert customer_safe_passages([RETURNS]) == [RETURNS]
    assert check_response_grounding("Returns are subject to eligibility checks.", [RETURNS]) is None

def test_internal_shipping_page_four_excluded():
    p = {**SHIPPING, "page": 4, "text": "Compensation and replacement resolution matrix."}
    assert customer_safe_passages([p]) == []

def test_noncustomer_passages_excluded():
    assert customer_safe_passages([{**RETURNS, "visibility": "internal"}]) == []

def test_unverified_completed_refund_still_blocked():
    assert check_response_grounding("We have issued your refund.", [RETURNS]) == "UNVERIFIED_COMPLETED_ACTION"

def test_internal_ops_still_blocked():
    assert check_customer_visibility("We ran a geofence audit.") == "SENSITIVE_OPERATIONAL_DETAIL"
