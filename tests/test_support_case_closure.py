"""Customer-owned closure: admin cannot close, pending refund blocks, no payments."""
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.auth.security import hash_password
from backend.main import app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'closure.sqlite3'))
    with TestClient(app) as c:
        yield c

def auth(c, role, suffix):
    uid = str(uuid.uuid4())
    with store.connect() as db:
        db.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,1)',
                   (uid, f'{suffix}@example.com', hash_password('GoodPassword!234'), role))
    response = c.post('/auth/login', json={'email': f'{suffix}@example.com', 'password': 'GoodPassword!234'})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['access_token']}

def test_customer_controls_resolution_and_pending_refund_blocks(client):
    customer = auth(client, 'customer', 'closecustomer')
    admin = auth(client, 'admin', 'closeadmin')
    other = auth(client, 'customer', 'closeother')
    created = client.post('/tickets', headers=customer, json={
        'subject': 'Refund order', 'description': 'Please refund my order', 'claimed_amount': 750})
    assert created.status_code == 201, created.text
    tid = created.json()['id']
    endpoint = f'/tickets/{tid}/resolve'
    body = {'reason': 'Support question addressed', 'version': 0}
    assert client.post(endpoint, headers=other, json=body).status_code == 404
    assert client.post(endpoint, headers=admin, json=body).status_code == 403
    assert client.post(endpoint, headers=customer, json=body).status_code == 409
    published = client.post(f'/admin/tickets/{tid}/publish', headers=admin,
                            json={'response': 'Your request was reviewed.', 'version': 0})
    assert published.status_code == 200, published.text
    body['version'] = 1
    assert client.post(endpoint, headers=customer, json=body).status_code == 409
    with store.connect() as db:
        db.execute("UPDATE refund_reviews SET status='REJECTED',version=version+1,decided_at=1,reason='Reviewed and rejected' WHERE ticket_id=?", (tid,))
    assert client.post(endpoint, headers=customer, json={**body,'version':0}).status_code == 409
    result = client.post(endpoint, headers=customer, json=body)
    assert result.status_code == 200, result.text
    assert result.json()['payment_executed'] is False
    assert client.post(endpoint, headers=customer, json=body).status_code == 409
    assert client.get(f'/tickets/{tid}', headers=customer).json()['status'] == 'RESOLVED'
    assert client.post(f'/tickets/{tid}/reply', headers=customer,
                       json={'content':'Another update please'}).status_code == 409
    assert client.post(f'/admin/tickets/{tid}/close', headers=admin, json=body).status_code == 404
