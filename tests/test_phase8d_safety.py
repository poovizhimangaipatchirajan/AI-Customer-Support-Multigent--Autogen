"""Additive Phase 8D API safety tests. Uses temporary SQLite; no live services.
Run: python -m pytest tests/test_phase8d_safety.py -v
"""
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.auth.security import hash_password, create_session
from backend.main import app


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'phase8d.sqlite3'))
    with TestClient(app) as client:
        yield client


def user(role='customer'):
    uid = str(uuid.uuid4())
    with store.connect() as db:
        db.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                   (uid, uid + '@example.test', hash_password('Test-Only-Password!123'), role, 1))
    return {'Authorization': 'Bearer ' + create_session(uid)}


def ticket(client, headers, amount, description='Please refund my purchase'):
    result = client.post('/tickets', headers=headers, json={
        'subject': 'Refund request', 'description': description, 'claimed_amount': amount})
    assert result.status_code == 201, result.text
    return result.json()


@pytest.mark.parametrize('amount,status,jobs', [
    (499, 'QUEUED', 1), (500, 'ESCALATED', 0), (501, 'ESCALATED', 0),
])
def test_backend_refund_boundary_is_persisted(env, amount, status, jobs):
    customer = user()
    result = ticket(env, customer, amount)
    assert result['status'] == status
    with store.connect() as db:
        persisted = db.execute('SELECT status FROM tickets WHERE id=?', (result['id'],)).fetchone()
        job_count = db.execute('SELECT COUNT(*) FROM jobs WHERE ticket_id=?', (result['id'],)).fetchone()[0]
    assert persisted['status'] == status
    assert job_count == jobs


@pytest.mark.parametrize('description,expected_flag', [
    ('I suspect fraud and request a refund', 'FRAUD'),
    ('I will contact a lawyer, please refund me', 'LEGAL'),
    ('I was charged twice, please refund me', 'PAYMENT_DISPUTE'),
])
def test_low_value_sensitive_refund_escalates(env, description, expected_flag):
    customer = user()
    result = ticket(env, customer, 20, description)
    assert result['status'] == 'ESCALATED'
    assert expected_flag in result['risk_flags']
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs WHERE ticket_id=?', (result['id'],)).fetchone()[0] == 0


def test_high_value_ticket_survives_new_client_and_remains_unpublished(env):
    customer = user()
    admin = user('admin')
    result = ticket(env, customer, 500)
    tid = result['id']
    # New TestClient instance, same temporary database: tests durable SQLite state,
    # not an operating-system process restart.
    with TestClient(app) as second_client:
        customer_view = second_client.get(f'/tickets/{tid}', headers=customer)
        assert customer_view.status_code == 200
        detail = customer_view.json()
        assert detail['status'] == 'ESCALATED'
        assert not any(m['role'] == 'assistant' for m in detail['messages'])
        queue = second_client.get('/admin/queue', headers=admin)
        assert queue.status_code == 200
        assert any(item['id'] == tid for item in queue.json())


def test_admin_review_response_does_not_claim_refund_executed(env):
    customer = user()
    admin = user('admin')
    result = ticket(env, customer, 501)
    tid = result['id']
    version = env.get(f'/tickets/{tid}', headers=admin).json()['version']
    text = 'Your case was reviewed; no refund has been issued.'
    publish = env.post(f'/admin/tickets/{tid}/publish', headers=admin,
                       json={'response': text, 'version': version})
    assert publish.status_code == 200, publish.text
    duplicate = env.post(f'/admin/tickets/{tid}/publish', headers=admin,
                         json={'response': 'Duplicate review', 'version': version})
    assert duplicate.status_code == 409
    detail = env.get(f'/tickets/{tid}', headers=customer).json()
    assert detail['status'] == 'ANSWERED'
    assert sum(m['content'] == text for m in detail['messages']) == 1
