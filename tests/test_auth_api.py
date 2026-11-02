from fastapi.testclient import TestClient
from backend.db import store
from backend import main


def test_registration_and_verification_gate(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXFLOW_DB', str(tmp_path / 'test.db'))
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'test.db'))
    sent = []
    monkeypatch.setattr(main, 'send_token', lambda email, purpose, token: sent.append((email, purpose, token)))

    with TestClient(main.app) as client:
        credentials = {'email': 'customer@example.com', 'password': 'TestPassword123!'}
        assert client.post('/auth/register', json=credentials).status_code == 201
        assert len(sent) == 1
        assert sent[0][:2] == (credentials['email'], 'verify')

        assert client.post('/auth/login', json=credentials).status_code == 403
        token = sent[0][2]
        assert client.post('/auth/verify', json={'token': token}).status_code == 200
        assert client.post('/auth/verify', json={'token': token}).status_code == 400

        result = client.post('/auth/login', json=credentials)
        assert result.status_code == 200
        headers = {'Authorization': 'Bearer ' + result.json()['access_token']}
        assert client.get('/tickets', headers=headers).status_code == 200
        assert client.get('/admin/queue', headers=headers).status_code == 403
        ticket = client.post('/tickets', headers=headers, json={
            'subject': 'Delivery delay', 'description': 'My package is late',
        })
        assert ticket.status_code == 201
