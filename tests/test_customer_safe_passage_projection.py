"""Dependency-free tests for the customer-facing evidence projection."""
import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'backend/services/grounding_guard.py'
spec = importlib.util.spec_from_file_location('customer_guard_under_test', path)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def passage(text, visibility='customer', source='01_Shipping_and_Delivery_SOP.pdf'):
    return {'id': 'shipping-1', 'source': source, 'visibility': visibility, 'text': text}


def test_sensitive_sentence_removed_without_losing_safe_sentence():
    original = passage('Customers can contact support about delayed delivery. We conduct driver GPS verification for investigations.')
    result = guard.customer_safe_passages([original])
    assert len(result) == 1
    assert result[0]['id'] == 'shipping-1'
    assert result[0]['text'] == 'Customers can contact support about delayed delivery.'
    assert 'GPS' in original['text']  # no mutation of cached RAG passage


def test_only_sensitive_text_is_not_exposed():
    assert guard.customer_safe_passages([passage('We conduct geofence audits.')]) == []


def test_internal_source_excluded_even_when_visibility_is_wrong():
    assert guard.customer_safe_passages([passage('Internal escalation instructions.', source='06_Escalations_and_Soft_Skills_SOP.pdf')]) == []


def test_non_customer_evidence_excluded():
    assert guard.customer_safe_passages([passage('Internal account procedure.', visibility='internal')]) == []


def test_safe_text_keeps_citation_id():
    result = guard.customer_safe_passages([passage('Customers may contact support for delivery questions.')])
    assert result[0]['id'] == 'shipping-1'
    assert guard.check_customer_visibility(result[0]['text']) is None


def test_rejects_invalid_retriever_data():
    import pytest
    with pytest.raises(ValueError):
        guard.customer_safe_passages([{'text': 'Missing ID'}])
