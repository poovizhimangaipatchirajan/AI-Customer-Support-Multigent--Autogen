"""Offline safety tests; no API, model or Docker required."""
import pytest
from backend.services.answer_repair import repair_isolated_time_claim
from backend.services.grounding_guard import check_response_grounding

EVIDENCE = [{'id':'returns-1','text':'Returns require an eligible item and proof of purchase.','visibility':'customer'}]

@pytest.mark.parametrize('draft', [
    'Delivery takes 3 days.',
    'Your order is in transit. Returns require an eligible item and proof of purchase.',
    'We will issue your refund. Returns require an eligible item and proof of purchase.',
    'Delivery takes 3 days. Delivery takes 4 days. Returns require proof of purchase.',
])
def test_no_unsafe_repair(draft):
    result, reason = repair_isolated_time_claim(draft, EVIDENCE)
    assert result == draft and reason is None


def test_isolated_unsupported_timing_removed_and_rechecked():
    draft = 'Returns require an eligible item and proof of purchase. Delivery takes 3 days.'
    result, reason = repair_isolated_time_claim(draft, EVIDENCE)
    assert reason == 'UNSUPPORTED_DELIVERY_TIME'
    assert result == 'Returns require an eligible item and proof of purchase.'
    assert check_response_grounding(result, EVIDENCE) is None


def test_dangling_reference_prevents_repair():
    draft = 'Delivery takes 3 days. Therefore you can plan your return.'
    assert repair_isolated_time_claim(draft, EVIDENCE) == (draft, None)


def test_empty_evidence_does_not_trigger_repair():
    draft = 'Returns require proof of purchase. Delivery takes 3 days.'
    assert repair_isolated_time_claim(draft, []) == (draft, None)
