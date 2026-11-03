"""Approved-only, offline, no DB or network; diagnostics never authorize action."""
import pytest
from backend.services.response_diagnostics import inspect_response

@pytest.fixture(autouse=True)
def approved_mode(monkeypatch):
    monkeypatch.setenv('FIXFLOW_CUSTOMER_INDEX_MODE', 'approved_only')


def test_fabricated_citation_cannot_hide_claimed_refund():
    evidence = {'fake': {'id': 'fake', 'visibility': 'customer', 'text': 'Refunds are available.'}}
    result = inspect_response('Refund?', 'We have issued your refund.', ['fake'], evidence)
    assert 'UNSAFE_OR_EMPTY_CITATION' in result.codes
    assert 'UNVERIFIED_COMPLETED_ACTION' in result.codes


def test_fabricated_citation_cannot_hide_delivery_promise():
    evidence = {'fake': {'id': 'fake', 'visibility': 'customer', 'text': 'Shipping details.'}}
    result = inspect_response('Delivery?', 'Delivery takes 3 days.', ['fake'], evidence)
    assert 'UNSAFE_OR_EMPTY_CITATION' in result.codes
    assert 'UNSUPPORTED_DELIVERY_TIME' in result.codes


def test_invalid_citation_never_counts_as_approved():
    result = inspect_response('Refund?', 'Please request human review.', ['invented'], {})
    assert 'UNKNOWN_CITATION' in result.codes
    assert 'UNVERIFIED_COMPLETED_ACTION' not in result.codes
