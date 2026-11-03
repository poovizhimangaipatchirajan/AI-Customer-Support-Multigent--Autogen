"""Regression tests: later damage allegations and changed refund amounts fail closed."""
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.main import app
from backend.auth.security import hash_password, create_session


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'refund_followup.sqlite3'))
    with TestClient(app) as client:
        yield client


def actor(role):
    uid = str(uuid.uuid4())
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid, uid + '@example.test', hash_password('Testing123!Secure'), role, 1))
    return uid, {'Authorization': 'Bearer ' + create_session(uid)}


def setup(env, amount=500):
    cid, customer = actor('customer')
    _, admin = actor('admin')
    result = env.post('/tickets', headers=customer, json={
        'subject': 'Shipping delay', 'description': 'Please refund me for delayed shipping',
        'claimed_amount': amount})
    assert result.status_code == 201, result.text
    tid = result.json()['id']
    order_code = 'ORDER-' + uuid.uuid4().hex[:12]
    with store.connect() as c:
        c.execute('INSERT INTO demo_orders(id,order_code,customer_id,placed_at,status,total_amount) VALUES (?,?,?,?,?,?)',
                  (str(uuid.uuid4()), order_code, cid, '2026-11-02', 'delivered', amount))
        c.execute('INSERT INTO ticket_order_links(ticket_id,order_code,customer_id) VALUES (?,?,?)',
                  (tid, order_code, cid))
    return tid, customer, admin


def assess_other(env, tid, admin, version=0):
    return env.post(f'/admin/refund-reviews/{tid}/assessment', headers=admin, json={
        'claim_type': 'OTHER', 'evidence_status': 'NOT_REQUIRED',
        'reason': 'Human reviewed non-damage claim', 'version': version})


def approve(env, tid, admin, version=0):
    return env.post(f'/admin/refund-reviews/{tid}/decision', headers=admin, json={
        'decision': 'APPROVED', 'reason': 'Human reviewed order and claim', 'version': version})


def test_damage_introduced_in_followup_requires_image(env):
    tid, customer, admin = setup(env)
    assert env.post(f'/tickets/{tid}/reply', headers=customer,
                    json={'content': 'The item arrived broken and cracked.'}).status_code == 200
    assert assess_other(env, tid, admin).status_code == 422
    assert approve(env, tid, admin).status_code == 409


def test_new_damage_after_assessment_invalidates_it(env):
    tid, customer, admin = setup(env)
    assert assess_other(env, tid, admin).status_code == 200
    assert env.post(f'/tickets/{tid}/reply', headers=customer,
                    json={'content': 'Actually it is damaged and broken.'}).status_code == 200
    assert approve(env, tid, admin).status_code == 409


@pytest.mark.parametrize('initial,updated', [(500, 800), (800, 500)])
def test_amount_conflict_requires_reconciliation_and_new_assessment(env, initial, updated):
    tid, customer, admin = setup(env, initial)
    assert assess_other(env, tid, admin).status_code == 200
    followup = env.post(f'/tickets/{tid}/reply', headers=customer,
                        json={'content': f'Please refund {updated} rupees instead.'})
    assert followup.status_code == 200, followup.text
    assert approve(env, tid, admin).status_code == 409
    review = env.get(f'/admin/refund-reviews/{tid}', headers=admin).json()
    assert float(review['amount']) == initial  # never silently rewrite an amount
    reconciliation = env.post(f'/admin/refund-reviews/{tid}/reconcile-amount',
                              headers=admin, json={'amount': updated,
                              'reason': 'Verified the changed customer claim', 'version': 0})
    assert reconciliation.status_code == 200, reconciliation.text
    assert approve(env, tid, admin, version=1).status_code == 409  # re-assessment mandatory
    assert assess_other(env, tid, admin, version=1).status_code == 200
    approved = approve(env, tid, admin, version=1)
    assert approved.status_code == 200, approved.text
    assert approved.json()['execution_status'] == 'NOT_CONFIGURED'


def test_reconciliation_requires_admin_and_pending_review(env):
    tid, customer, admin = setup(env)
    url = f'/admin/refund-reviews/{tid}/reconcile-amount'
    body = {'amount': 800, 'reason': 'Verified claim amount', 'version': 0}
    assert env.post(url, headers=customer, json=body).status_code == 403
    assert env.post(url, headers=admin, json={**body, 'version': 2}).status_code == 409


def test_admin_message_does_not_create_damage_claim(env):
    tid, _, admin = setup(env)
    assert env.post(f'/tickets/{tid}/reply', headers=admin,
                    json={'content': 'Please tell us if the product is broken.'}).status_code == 200
    assert assess_other(env, tid, admin).status_code == 200
    assert approve(env, tid, admin).status_code == 200


def test_followup_crossing_500_creates_pending_review(env):
    tid, customer, admin = setup(env, 100)
    assert env.get(f'/admin/refund-reviews/{tid}', headers=admin).status_code == 404
    response = env.post(f'/tickets/{tid}/reply', headers=customer,
                        json={'content': 'Please refund 800 rupees instead.'})
    assert response.status_code == 200, response.text
    review = env.get(f'/admin/refund-reviews/{tid}', headers=admin)
    assert review.status_code == 200, review.text
    assert review.json()['status'] == 'PENDING'
    with store.connect() as c:
        risk = c.execute('SELECT risk FROM tickets WHERE id=?', (tid,)).fetchone()['risk']
    assert 'REFUND_GE_500' in risk
    assert 'REFUND_AMOUNT_UNVERIFIED' in risk
    assert approve(env, tid, admin).status_code == 409


def test_initial_conflicting_high_value_claim_creates_review(env):
    _, customer = actor('customer')
    _, admin = actor('admin')
    response = env.post('/tickets', headers=customer, json={
        'subject': 'Refund request',
        'description': 'Please refund 800 rupees for this order.',
        'claimed_amount': 100})
    assert response.status_code == 201, response.text
    tid = response.json()['id']
    review = env.get(f'/admin/refund-reviews/{tid}', headers=admin)
    assert review.status_code == 200, review.text
    assert review.json()['status'] == 'PENDING'
    assert 'REFUND_AMOUNT_UNVERIFIED' in response.json()['risk_flags']
    assert approve(env, tid, admin).status_code == 409
