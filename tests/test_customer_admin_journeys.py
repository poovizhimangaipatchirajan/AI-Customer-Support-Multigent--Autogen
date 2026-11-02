"""Isolated API journeys: no real OpenAI calls and no production DB writes."""
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.auth.security import hash_password
from backend.main import app

PASSWORD = 'GoodPassword!234'

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'journey.sqlite3'))
    monkeypatch.setenv('FIXFLOW_LOCAL_DEMO', '1')
    with TestClient(app) as client:
        yield client


def account(client, email, role='customer'):
    payload = {'email': email, 'password': PASSWORD, 'full_name': 'Test Customer', 'phone': '9876543210'}
    if role == 'customer':
        response = client.post('/auth/register', json=payload)
        assert response.status_code == 201, response.text
        with store.connect() as db:
            db.execute('UPDATE users SET verified=1 WHERE email=?', (email,))
    else:
        with store.connect() as db:
            db.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                       (str(uuid.uuid4()), email, hash_password(PASSWORD), 'admin', 1))
    login = client.post('/auth/login', json={'email': email, 'password': PASSWORD})
    assert login.status_code == 200, login.text
    return {'Authorization': 'Bearer ' + login.json()['access_token']}


def create_ticket(client, headers, description='Where is my shipment?', amount=None):
    response = client.post('/tickets', headers=headers, json={
        'subject': 'Order assistance', 'description': description, 'claimed_amount': amount})
    assert response.status_code == 201, response.text
    return response.json()


def test_customer_isolation_and_admin_visibility(env):
    alice = account(env, 'alice@example.com')
    bob = account(env, 'bob@example.com')
    admin = account(env, 'admin@example.com', 'admin')
    ticket = create_ticket(env, alice)
    tid = ticket['id']
    assert env.get(f'/tickets/{tid}', headers=alice).status_code == 200
    assert env.get(f'/tickets/{tid}', headers=bob).status_code == 404
    assert env.post(f'/tickets/{tid}/reply', headers=bob, json={'content': 'Unauthorized follow-up'}).status_code == 404
    assert all(row['id'] != tid for row in env.get('/tickets', headers=bob).json())
    assert env.get(f'/tickets/{tid}', headers=admin).status_code == 200
    assert env.get('/admin/queue', headers=alice).status_code == 403


def test_customer_followup_requeues_and_supersedes_old_job(env):
    customer = account(env, 'followup@example.com')
    ticket = create_ticket(env, customer)
    tid = ticket['id']
    assert ticket['status'] == 'QUEUED'
    response = env.post(f'/tickets/{tid}/reply', headers=customer,
                        json={'content': 'Could you explain the shipping policy?'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'QUEUED'
    with store.connect() as db:
        jobs = db.execute('SELECT status FROM jobs WHERE ticket_id=? ORDER BY created_at', (tid,)).fetchall()
        assert sorted(j['status'] for j in jobs) == ['QUEUED', 'SUPERSEDED']
    detail = env.get(f'/tickets/{tid}', headers=customer).json()
    assert any('shipping policy' in m['content'] for m in detail['messages'])


@pytest.mark.parametrize('amount,expected', [(499, 'QUEUED'), (500, 'ESCALATED'), (501, 'ESCALATED')])
def test_refund_boundary_end_to_end(env, amount, expected):
    customer = account(env, f'refund{amount}@example.com')
    result = create_ticket(env, customer, 'Please refund my purchase', amount)
    assert result['status'] == expected
    with store.connect() as db:
        jobs = db.execute('SELECT status FROM jobs WHERE ticket_id=?', (result['id'],)).fetchall()
    assert len(jobs) == (1 if expected == 'QUEUED' else 0)


def test_admin_review_version_guard_and_customer_response(env):
    customer = account(env, 'reviewer-customer@example.com')
    admin = account(env, 'reviewer-admin@example.com', 'admin')
    ticket = create_ticket(env, customer, 'Please refund my order', 750)
    tid = ticket['id']
    assert ticket['status'] == 'ESCALATED'
    queue = env.get('/admin/queue', headers=admin)
    assert queue.status_code == 200
    assert any(row['id'] == tid for row in queue.json())
    detail = env.get(f'/tickets/{tid}', headers=admin).json()
    stale = env.post(f'/admin/tickets/{tid}/publish', headers=admin,
                     json={'response': 'Review completed.', 'version': detail['version'] + 1})
    assert stale.status_code == 409
    published = env.post(f'/admin/tickets/{tid}/publish', headers=admin,
                         json={'response': 'Your case was reviewed by support.', 'version': detail['version']})
    assert published.status_code == 200, published.text
    customer_detail = env.get(f'/tickets/{tid}', headers=customer).json()
    assert customer_detail['status'] == 'ANSWERED'
    assert any(m['role'] == 'admin' and 'reviewed' in m['content'] for m in customer_detail['messages'])
    audit = env.get(f'/admin/audit/{tid}', headers=admin)
    assert audit.status_code == 200
    assert any('ADMIN_PUBLISHED' in item['event'] for item in audit.json())
    assert env.get(f'/admin/audit/{tid}', headers=customer).status_code == 403


def test_sensitive_fraud_escalates_at_low_amount(env):
    customer = account(env, 'fraud@example.com')
    ticket = create_ticket(env, customer, 'This is fraud, refund me', 50)
    assert ticket['status'] == 'ESCALATED'
    assert 'FRAUD' in ticket['risk_flags']


def test_risky_followup_escalates_existing_ticket(env):
    customer = account(env, 'riskyfollowup@example.com')
    ticket = create_ticket(env, customer)
    response = env.post(f"/tickets/{ticket['id']}/reply", headers=customer,
                        json={'content': 'I want a refund of this order'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'ESCALATED'
    with store.connect() as db:
        assert not db.execute('SELECT 1 FROM jobs WHERE ticket_id=? AND status="QUEUED"',
                              (ticket['id'],)).fetchone()
