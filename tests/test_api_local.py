import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXFLOW_DB', str(tmp_path / 'test.sqlite3'))
    from backend.db.store import init
    init()
    from backend.main import app
    with TestClient(app) as client:
        yield client


def test_health(api):
    assert api.get('/health').status_code == 200


def test_requires_auth(api):
    assert api.get('/tickets').status_code == 401


def test_register_login_verification(api, monkeypatch):
    from backend import main
    sent = []
    monkeypatch.setattr(main, 'send_token', lambda email, purpose, token: sent.append((email, purpose, token)))
    email = 'sample@example.com'
    password = 'LongPassword!234'
    credentials = {'email': email, 'password': password}

    assert api.post('/auth/register', json=credentials).status_code == 201
    assert len(sent) == 1
    assert sent[0][:2] == (email, 'verify')
    assert api.post('/auth/login', json=credentials).status_code == 403

    token = sent[0][2]
    assert api.post('/auth/verify', json={'token': token}).status_code == 200
    assert api.post('/auth/verify', json={'token': token}).status_code == 400

    login = api.post('/auth/login', json=credentials)
    assert login.status_code == 200
    headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
    ticket = api.post('/tickets', headers=headers, json={
        'subject': 'Test refund',
        'description': 'I want a refund of 500 rupees',
        'claimed_amount': 500,
    })
    assert ticket.status_code == 201
    assert ticket.json()['status'] == 'ESCALATED'
    assert api.post('/auth/logout', headers=headers).json()['logged_out']
    assert api.get('/tickets', headers=headers).status_code == 401
