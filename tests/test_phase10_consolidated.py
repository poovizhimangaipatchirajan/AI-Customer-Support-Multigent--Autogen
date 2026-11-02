"""Phase 10 additional API-level acceptance tests; isolated SQLite, no live AI/network."""
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.auth.security import hash_password
from backend.main import app

PASSWORD = 'GoodPassword!234'

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'phase10.sqlite3'))
    monkeypatch.setenv('FIXFLOW_LOCAL_DEMO', '0')
    with TestClient(app) as client:
        yield client

def login(client, role='customer'):
    email = f'p10-{uuid.uuid4().hex}@example.test'
    with store.connect() as db:
        db.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                   (str(uuid.uuid4()), email, hash_password(PASSWORD), role, 1))
    response = client.post('/auth/login', json={'email': email, 'password': PASSWORD})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['access_token']}

def ticket(client, auth, description, amount=None):
    response = client.post('/tickets', headers=auth, json={
        'subject': 'Phase 10 synthetic support case',
        'description': description,
        'claimed_amount': amount,
    })
    assert response.status_code == 201, response.text
    return response.json()

@pytest.mark.parametrize('amount,expected', [(499, 'QUEUED'), (500, 'ESCALATED'), (501, 'ESCALATED')])
def test_refund_threshold_and_review_record(env, amount, expected):
    customer = login(env)
    created = ticket(env, customer, 'Please refund my order', amount)
    assert created['status'] == expected
    with store.connect() as db:
        reviews = db.execute('SELECT status,execution_status FROM refund_reviews WHERE ticket_id=?',
                             (created['id'],)).fetchall()
        jobs = db.execute('SELECT status FROM jobs WHERE ticket_id=?', (created['id'],)).fetchall()
    if amount >= 500:
        assert len(reviews) == 1
        assert reviews[0]['status'] == 'PENDING'
        assert reviews[0]['execution_status'] == 'NOT_CONFIGURED'
        assert not jobs
    else:
        assert not reviews
        assert len(jobs) == 1 and jobs[0]['status'] == 'QUEUED'

@pytest.mark.parametrize('decision', ['APPROVED', 'REJECTED'])
def test_refund_decision_is_admin_only_idempotent_and_nonexecuting(env, decision):
    customer, admin = login(env), login(env, 'admin')
    created = ticket(env, customer, 'Please refund my order', 750)
    tid = created['id']
    endpoint = f'/admin/refund-reviews/{tid}'
    assert env.get(endpoint, headers=customer).status_code == 403
    review = env.get(endpoint, headers=admin)
    assert review.status_code == 200, review.text
    payload = {'decision': decision, 'reason': 'Synthetic reviewer acceptance test',
               'version': review.json()['version']}
    assert env.post(endpoint + '/decision', headers=customer, json=payload).status_code == 403
    if decision == 'APPROVED':
        prepare_refund_prerequisites(env, tid, admin)
    accepted = env.post(endpoint + '/decision', headers=admin, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['status'] == decision
    assert accepted.json()['execution_status'] == 'NOT_CONFIGURED'
    assert env.post(endpoint + '/decision', headers=admin, json=payload).status_code == 409
    final = env.get(endpoint, headers=admin).json()
    assert final['status'] == decision
    assert final['execution_status'] == 'NOT_CONFIGURED'
    assert final['version'] == review.json()['version'] + 1

def test_customer_isolation_and_admin_endpoints(env):
    alice, bob, admin = login(env), login(env), login(env, 'admin')
    created = ticket(env, alice, 'Where is my parcel?')
    tid = created['id']
    assert env.get(f'/tickets/{tid}', headers=bob).status_code == 404
    assert env.post(f'/tickets/{tid}/reply', headers=bob,
                    json={'content': 'Unauthorized synthetic reply'}).status_code == 404
    assert env.get('/admin/queue', headers=alice).status_code == 403
    assert env.get('/admin/export.csv', headers=alice).status_code == 403
    assert env.get(f'/tickets/{tid}', headers=admin).status_code == 200

def test_sensitive_case_has_no_queued_agent_job(env):
    customer = login(env)
    created = ticket(env, customer, 'I suspect fraud involving a payment and need a dispute reviewed')
    assert created['status'] == 'ESCALATED'
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs WHERE ticket_id=?',
                          (created['id'],)).fetchone()[0] == 0


def prepare_refund_prerequisites(client, ticket_id, admin_headers, damaged=False, customer_headers=None):
    """Arrange a synthetic owned order and a human evidence assessment."""
    import base64
    from backend.db import store
    with store.connect() as db:
        customer_id = db.execute('SELECT customer_id FROM tickets WHERE id=?', (ticket_id,)).fetchone()['customer_id']
        code = 'TEST-' + uuid.uuid4().hex[:16]
        db.execute('INSERT INTO demo_orders (id,order_code,customer_id,placed_at,status,total_amount) VALUES (?,?,?,?,?,?)',
                   (str(uuid.uuid4()), code, customer_id, '2026-01-01', 'DELIVERED', 750))
        db.execute('INSERT INTO ticket_order_links (ticket_id,order_code,customer_id) VALUES (?,?,?)',
                   (ticket_id, code, customer_id))
    evidence_ids = []
    if damaged:
        assert customer_headers is not None
        # Minimal PNG signature and ending; current upload checks header only for PNG.
        image = base64.b64encode(b'\x89PNG\r\n\x1a\nsynthetic-test-evidence').decode('ascii')
        upload = client.post(f'/tickets/{ticket_id}/evidence', headers=customer_headers,
                             json={'filename':'test.png','media_type':'image/png','content_base64':image})
        assert upload.status_code == 201, upload.text
        evidence_ids = [upload.json()['id']]
    response = client.post(f'/admin/refund-reviews/{ticket_id}/assessment', headers=admin_headers,
        json={'claim_type':'DAMAGED_DEFECTIVE' if damaged else 'OTHER',
              'evidence_status':'ACCEPTED' if damaged else 'NOT_REQUIRED',
              'evidence_ids':evidence_ids, 'reason':'Synthetic human reviewer assessment', 'version':0})
    assert response.status_code == 200, response.text
