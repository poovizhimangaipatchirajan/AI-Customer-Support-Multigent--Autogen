from backend.services.response_diagnostics import inspect_response


def test_synthetic_account_fixture_has_correct_domain_citation():
    p = {'id': 'test-account', 'source': 'FICTIONAL_ACCOUNT_POLICY.pdf',
         'page': 1, 'visibility': 'customer',
         'text': 'Customers who cannot log in may use the password reset option.'}
    result = inspect_response(
        'I forgot my password and cannot log in.',
        'You can use the password reset option. Never share your password.',
        [p['id']], {p['id']: p})
    assert 'MISSING_DOMAIN_CITATION' not in result.codes
    assert result.codes == ()


def test_unknown_policy_source_does_not_satisfy_domain_requirement():
    p = {'id': 'unknown', 'source': 'UNVERIFIED_POLICY.pdf',
         'page': 1, 'visibility': 'customer',
         'text': 'Password reset option.'}
    result = inspect_response('I forgot my password.',
        'Use the password reset option.', [p['id']], {p['id']: p})
    assert 'MISSING_DOMAIN_CITATION' in result.codes
