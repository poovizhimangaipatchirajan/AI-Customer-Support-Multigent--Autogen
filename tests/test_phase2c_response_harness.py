from eval.phase2c_live_responses import CASES, POLICIES, assess, select_cases


def test_cases_have_unique_names():
    assert len({case[0] for case in CASES}) == len(CASES)


def test_case_selection():
    assert [c[0] for c in select_cases('general_returns,shipping_eta')] == ['general_returns','shipping_eta']


def test_unknown_case_fails_closed():
    import pytest
    with pytest.raises(ValueError): select_cases('unknown')


def test_missing_callback_detected():
    assert assess(CASES[0],[])['status'] == 'NO_OUTCOME'


def test_missing_citation_requires_review():
    item={'draft':'Please verify your order.', 'ids':[], 'escalate':False,'evidence':{}}
    result=assess(CASES[0],[item])
    assert result['status']=='REVIEW'
    assert 'MISSING_CITATIONS' in result['diagnostic_codes']


def test_fake_completed_refund_is_flagged():
    p=POLICIES['returns']
    item={'draft':'We have issued your refund.', 'ids':[p['id']], 'escalate':False,'evidence':{p['id']:p}}
    result=assess(CASES[0],[item])
    assert result['status']=='REVIEW'
    assert 'UNVERIFIED_COMPLETED_ACTION' in result['diagnostic_codes']


def test_clean_noncommittal_response():
    p=POLICIES['account']
    item={'draft':'You can use the password reset option. Never share your password.',
          'ids':[p['id']], 'escalate':False,'evidence':{p['id']:p}}
    result=assess(CASES[5],[item])
    assert result['status']=='DIAGNOSTICALLY_CLEAN'
