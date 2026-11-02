import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('guard_patch', ROOT/'backend/services/grounding_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
PASSAGES = [{'text':'Customers can request assistance with a return under the applicable policy.'}]

def test_unsupported_tracking_status():
    assert guard.check_response_grounding('Your package is secure and moving forward.', PASSAGES) == 'UNVERIFIED_ORDER_STATUS'

def test_replacement_promise():
    assert guard.check_response_grounding('We will dispatch a replacement immediately at no extra cost.', PASSAGES) == 'UNAUTHORIZED_ACTION_PROMISE'

def test_wrong_threshold():
    assert guard.check_response_grounding('Refunds of Rs. 499 require human approval.', PASSAGES) == 'MISSTATED_REFUND_THRESHOLD'

def test_neutral_policy_guidance():
    assert guard.check_response_grounding('You may request a return, subject to verification.', PASSAGES) is None
