import pytest
from eval.phase2d_real_sop import load_cases, select_cases, assess


def test_24_unique_cases():
    cases = load_cases()
    assert len(cases) == 24
    assert {c['id'] for c in cases} == {f'S{i:02}' for i in range(1, 25)}


def test_default_selection_order():
    assert [x['id'] for x in select_cases(load_cases(), 'S10,S01,S07')] == ['S01','S07','S10']


def test_all_selection():
    assert len(select_cases(load_cases(), 'all')) == 24


@pytest.mark.parametrize('value', ['', 'S99', 'S01,S99'])
def test_invalid_selection(value):
    with pytest.raises(ValueError):
        select_cases(load_cases(), value)


def test_missing_outcome_is_not_clean():
    result = assess(load_cases()[0], [], [], False)
    assert result['status'] == 'NO_OUTCOME'


def test_missing_prior_context_requires_review():
    case = next(x for x in load_cases() if x['id'] == 'S12')
    captured = [{'draft':'Please clarify which order you mean.', 'ids':['p'],
                 'escalate':False, 'evidence':{'p':{'id':'p','visibility':'customer','text':'Eligibility requires order verification.','source':'POLICY.pdf'}}}]
    result = assess(case, captured, [], True)
    assert result['status'] == 'REVIEW'
    assert 'MISSING_PRIOR_CONVERSATION_CONTEXT' in result['diagnostic_codes']


def test_payment_dispute_without_escalation_is_flagged():
    case = next(x for x in load_cases() if x['id'] == 'S18')
    captured = [{'draft':'Please contact support for the disputed charge.', 'ids':['p'],
                 'escalate':False, 'evidence':{'p':{'id':'p','visibility':'customer','text':'Payment disputes need human review.','source':'POLICY.pdf'}}}]
    result = assess(case, captured, [], True)
    assert 'MANDATORY_HUMAN_REVIEW_NOT_REQUESTED' in result['diagnostic_codes']


def test_no_citation_is_not_clean():
    case = load_cases()[0]
    captured = [{'draft':'You may return items.', 'ids':[], 'escalate':False, 'evidence':{}}]
    result = assess(case, captured, [], True)
    assert 'MISSING_CITATIONS' in result['diagnostic_codes']
