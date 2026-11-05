"""Consolidated auth accounts tests.

Merged without behaviour changes from:
- test_auth_api.py: regression tests
- test_api_local.py: regression tests
- test_email_verification_targeted.py: Focused tests for registration mail, resend, and login enforcement.
- test_registration_validation.py: regression tests
- test_public_ids.py: regression tests
- test_ticket_evidence_security.py: Offline checks for evidence file signature validation.
"""
from fastapi.testclient import TestClient
from backend.db import store
from backend import main
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from backend.auth.mailer import send_token
from backend.main import validate_registration
import os
import sqlite3
import uuid
from backend.db.store import connect, init
from backend.services.ticket_evidence import sniff, MAX_BYTES, ALLOWED
from PIL import Image
import io


# ======================================================================
# From test_auth_api.py
# ======================================================================

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


# ======================================================================
# From test_api_local.py
# ======================================================================

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
    assert ticket.json()['status'] == 'QUEUED'
    assert 'REFUND_GE_500' in ticket.json()['risk_flags']
    assert api.post('/auth/logout', headers=headers).json()['logged_out']
    assert api.get('/tickets', headers=headers).status_code == 401


# ======================================================================
# From test_email_verification_targeted.py
# ======================================================================

class FakeConn:
    def __init__(self, row): self.row=row
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, *args): return self
    def fetchone(self): return self.row

def test_unverified_customer_cannot_login_or_create_session():
    row={'id':'uid','role':'customer','verified':0,'password_hash':'hashed','public_id':'C1'}
    with patch.object(main,'connect',return_value=FakeConn(row)), patch.object(main,'verify_password',return_value=True), patch.object(main,'create_session') as session, patch.object(main,'throttle'):
        with pytest.raises(HTTPException) as exc:
            main.login(main.Credentials(email='a@example.com',password='LongPassword!123'))
        assert exc.value.status_code==403
        session.assert_not_called()

def test_verified_customer_can_login():
    row={'id':'uid','role':'customer','verified':1,'password_hash':'hashed','public_id':'C1'}
    with patch.object(main,'connect',return_value=FakeConn(row)), patch.object(main,'verify_password',return_value=True), patch.object(main,'create_session',return_value='session'), patch.object(main,'throttle'):
        assert main.login(main.Credentials(email='a@example.com',password='LongPassword!123'))['access_token']=='session'

def test_resend_uses_existing_unverified_account():
    with patch.object(main,'connect',return_value=FakeConn({'id':'uid','verified':0})), patch.object(main,'issue_token') as issue, patch.object(main,'throttle'):
        main.resend_verification(main.EmailIn(email='A@example.com'))
        issue.assert_called_once_with('uid','verify','a@example.com')

def test_resend_does_not_issue_for_unknown_account():
    with patch.object(main,'connect',return_value=FakeConn(None)), patch.object(main,'issue_token') as issue, patch.object(main,'throttle'):
        response=main.resend_verification(main.EmailIn(email='unknown@example.com'))
        assert 'If this account' in response['message']
        issue.assert_not_called()

def test_mailer_uses_gmail_ssl_and_sends_code():
    with patch.dict('os.environ',{'FIXFLOW_SMTP_USER':'sender@gmail.com','FIXFLOW_SMTP_APP_PASSWORD':'example-app-password','FIXFLOW_MAIL_FROM':'sender@gmail.com'}), patch('smtplib.SMTP_SSL') as smtp:
        send_token('recipient@example.com','verify','TEST_TOKEN')
        smtp.assert_called_once()
        assert smtp.call_args.args[:2]==('smtp.gmail.com',465)
        msg=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        assert 'TEST_TOKEN' in msg.get_content()


# ======================================================================
# From test_registration_validation.py
# ======================================================================

def test_registration_valid():
    email, name, phone = validate_registration(' Test@Example.COM ', 'GoodPassword!234', '  Asha   Kumar ', '9876543210')
    assert (email, name, phone) == ('test@example.com', 'Asha Kumar', '+919876543210')


@pytest.mark.parametrize('email', ['not-an-email', 'abc@', 'a b@example.com'])
def test_invalid_email(email):
    with pytest.raises(HTTPException):
        validate_registration(email, 'GoodPassword!234', 'Asha Kumar')


@pytest.mark.parametrize('password', ['short', 'alllowercase123!', 'ALLUPPERCASE123!', 'NoNumbersHere!', 'NoSpecial1234'])
def test_weak_password(password):
    with pytest.raises(HTTPException):
        validate_registration('test@example.com', password, 'Asha Kumar')


@pytest.mark.parametrize('name', ['', 'A', '12345', '   '])
def test_invalid_name(name):
    with pytest.raises(HTTPException):
        validate_registration('test@example.com', 'GoodPassword!234', name)


@pytest.mark.parametrize('phone', ['12345', '5123456789', '+441234567890'])
def test_invalid_phone(phone):
    with pytest.raises(HTTPException):
        validate_registration('test@example.com', 'GoodPassword!234', 'Asha Kumar', phone)


def test_legacy_api_registration_payload_supported():
    assert validate_registration('test@example.com', 'GoodPassword!234') == ('test@example.com', None, None)


# ======================================================================
# From test_public_ids.py
# ======================================================================

def test_public_ids_backfill_and_new_rows(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXFLOW_DB', str(tmp_path / 'fixflow.sqlite3'))
    init()
    with connect() as c:
        c.execute("INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)", ('user-1','customer@example.test','hash','customer',1))
        c.execute("INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at) VALUES (?,?,?,?,?,?,?,?)", ('ticket-1','user-1','Shipping','Question about shipping',None,'QUEUED','',1793426201))
        user = c.execute("SELECT public_id FROM users WHERE id='user-1'").fetchone()['public_id']
        ticket = c.execute("SELECT public_id FROM tickets WHERE id='ticket-1'").fetchone()['public_id']
        assert user.startswith('NC-')
        assert ticket.startswith('FF-2026-')
    init()
    with connect() as c:
        assert c.execute("SELECT public_id FROM users WHERE id='user-1'").fetchone()['public_id'] == user
        assert c.execute("SELECT public_id FROM tickets WHERE id='ticket-1'").fetchone()['public_id'] == ticket


# ======================================================================
# From test_ticket_evidence_security.py
# ======================================================================

def image_bytes(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format=fmt)
    return buf.getvalue()

def test_supported_types_and_limits():
    assert MAX_BYTES == 2 * 1024 * 1024
    assert set(ALLOWED) == {'image/jpeg', 'image/png', 'application/pdf'}

def test_reject_mismatched_file_signatures():
    assert not sniff(b'<script>alert(1)</script>', 'image/png')
    assert not sniff(b'%PDF-malformed', 'application/pdf')
    assert not sniff(b'\xff\xd8\xfftruncated', 'image/jpeg')

def test_accept_representative_headers():
    assert sniff(image_bytes('PNG'), 'image/png')
    assert sniff(image_bytes('JPEG'), 'image/jpeg')
    assert sniff(b'%PDF-1.4\n...\n%%EOF\n', 'application/pdf')


def test_reject_incomplete_images_and_mislabeled_formats():
    assert not sniff(b'\x89PNG\r\n\x1a\n' + b'data', 'image/png')
    assert not sniff(b'\xff\xd8\xffdata\xff\xd9', 'image/jpeg')
    assert not sniff(image_bytes('PNG'), 'image/jpeg')
    assert not sniff(image_bytes('JPEG')[:-10], 'image/jpeg')
