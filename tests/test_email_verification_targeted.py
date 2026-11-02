"""Focused tests for registration mail, resend, and login enforcement."""
from unittest.mock import patch, MagicMock
import pytest
from fastapi import HTTPException
from backend import main
from backend.auth.mailer import send_token

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
