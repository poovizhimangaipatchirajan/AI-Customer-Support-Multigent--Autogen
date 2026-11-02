"""Focused regression tests for dispatch/delivery framing and SOP visibility."""
import importlib.util
from pathlib import Path

import pytest

# When copied to tests/, import the installed project module.
from backend.services.grounding_guard import (
    check_response_grounding,
    customer_safe_passages,
)

EVIDENCE = [{"id": "shipping-p1", "source": "01_Shipping_and_Delivery_SOP.pdf", "page": 1, "visibility": "customer", "text": "The order has a promised Estimated Delivery Date (EDD). DMF sellers generate dispatch manifests within 48 business hours."}]

@pytest.mark.parametrize("draft", [
    "NovaCart's standard delivery timeframe varies depending on the fulfillment channel: DMF sellers dispatch within 48 business hours.",
    "Standard delivery times depend on FBN, DMF and HBN.",
    "Your order will be delivered within 48 business hours.",
])
def test_dispatch_not_delivery(draft):
    assert check_response_grounding(draft, EVIDENCE) == "DISPATCH_MISREPRESENTED_AS_DELIVERY"


def test_honest_missing_timeframe_is_allowed():
    draft = "The available shipping SOP does not specify a universal delivery duration. It refers to a promised Estimated Delivery Date (EDD)."
    assert check_response_grounding(draft, EVIDENCE) is None


def test_shipping_page_four_is_not_customer_visible():
    passages = [*EVIDENCE, {"id": "shipping-p4", "source": "01_Shipping_and_Delivery_SOP.pdf", "page": 4, "visibility": "customer", "text": "Immediate replacement priority dispatch and discretionary goodwill credit."}]
    safe = customer_safe_passages(passages)
    assert [p["id"] for p in safe] == ["shipping-p1"]
