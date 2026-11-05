"""Consolidated logging ui tests.

Merged without behaviour changes from:
- test_rubric_alignment.py: Focused, offline tests for additive rubric reporting.
- test_ticket_csv_sync.py: regression tests
- test_ticket_classification.py: Focused offline regression tests; no DB, API, AutoGen or OpenAI required.
- test_ticket_review_display.py: UI copy must not confuse unrelated tickets with financial approvals.
- test_frontend_api_errors.py: Offline frontend API error handling: no backend, no network.
"""
import csv
import io
from types import SimpleNamespace
import pytest
from backend.db import store
from backend.services.customer_orders import migrate as migrate_customer_orders
from backend.services.complaint_log import migrate, record_agent_events, _rows, complaints_csv
from pathlib import Path
from unittest.mock import patch
from backend.services.ticket_csv_sync import sync_csv
from backend.services.complaint_log import FIELDS
from backend.services.ticket_classification import classify_ticket
from frontend.ticket_review_display import review_message
import requests
from streamlit.testing.v1 import AppTest
from frontend import api_client
from frontend.api_client import ApiError, call


# ======================================================================
# From test_rubric_alignment.py
# ======================================================================

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path/'rubric.sqlite3'))
    store.init()
    migrate_customer_orders()
    migrate()
    with store.connect() as c:
        c.execute("INSERT INTO users(id,email,password_hash,role,verified,full_name) VALUES ('u','u@example.test','x','customer',1,'Demo Customer')")
        c.execute("INSERT INTO tickets(id,customer_id,subject,description,status,risk,created_at) VALUES ('t','u','Shipping question','Where is my order?','PROCESSING','',1000)")
        c.execute("INSERT INTO jobs(id,ticket_id,status,created_at,lease_token,input_version) VALUES ('j','t','PROCESSING',1000,'lease1',0)")
    yield

def test_section7_columns_and_classified_labels(isolated):
    rows = _rows()
    assert len(rows)==1
    assert len(rows[0])==12
    assert (rows[0]['category'], rows[0]['urgency'], rows[0]['sentiment']) == ('Shipping & delivery', 'Low', 'Neutral')
    assert rows[0]['status'] == 'PROCESSING' and rows[0]['outcome'] == 'Pending'
    assert rows[0]['complaint']=='Where is my order?'

def test_agent_events_are_observed_not_generated(isolated):
    job={'id':'j','ticket_id':'t','lease':'lease1','version':0}
    messages=[SimpleNamespace(source='QueryAgent',content='private content'),SimpleNamespace(source='KnowledgeAgent',content='internal SOP'),SimpleNamespace(source='unknown',content='x')]
    record_agent_events(job,messages)
    record_agent_events(job,messages)
    with store.connect() as c:
        rows=c.execute('SELECT * FROM agent_events ORDER BY agent').fetchall()
        assert len(rows)==2
        assert {r['agent'] for r in rows}=={'QueryAgent','KnowledgeAgent'}
        assert all('private content' not in str(tuple(r)) and 'internal SOP' not in str(tuple(r)) for r in rows)

def test_stale_lease_cannot_write_events(isolated):
    record_agent_events({'id':'j','ticket_id':'t','lease':'expired','version':0},[SimpleNamespace(source='LogAgent')])
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM agent_events').fetchone()[0]==0

def test_csv_export_columns(isolated):
    response=complaints_csv(user={'role':'admin'})
    assert response.body.startswith('﻿'.encode('utf-8'))
    rows=list(csv.DictReader(io.StringIO(response.body.decode('utf-8-sig'))))
    assert len(rows)==1
    assert len(rows[0])==12
    assert list(rows[0])[0]=='ticket_id' and 'outcome' in rows[0]

def test_customer_denied_export(isolated):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        complaints_csv(user={'role':'customer'})
    assert error.value.status_code==403

def test_agent_output_capture_admin_only_and_lease_guard(isolated):
    from backend.services.complaint_log import record_agent_outputs, agent_outputs
    job = {'id':'j','ticket_id':'t','lease':'lease1','version':0}
    record_agent_outputs(job, [SimpleNamespace(source='QueryAgent',content='Category: Shipping; password=secretvalue'),
                               SimpleNamespace(source='KnowledgeAgent',content={'tool_call':'internal'}),
                               SimpleNamespace(source='LogAgent',content='LOGGED')])
    rows = agent_outputs('t', user={'role':'admin'})
    assert [r['agent'] for r in rows] == ['QueryAgent','LogAgent']
    assert 'secretvalue' not in rows[0]['output']
    record_agent_outputs({'id':'j','ticket_id':'t','lease':'wrong','version':0},
                         [SimpleNamespace(source='ResolutionAgent',content='should not appear')])
    assert len(agent_outputs('t',user={'role':'admin'})) == 2
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        agent_outputs('t',user={'role':'customer'})
    assert exc.value.status_code == 403


def test_verified_order_in_complaint_export(isolated):
    with store.connect() as c:
        c.execute("INSERT INTO demo_orders(id,order_code,customer_id,placed_at,status,total_amount) VALUES ('o','DEMO-001','u','2026-01-01','SHIPPED',500)")
        c.execute("INSERT INTO ticket_order_links(ticket_id,order_code,customer_id) VALUES ('t','DEMO-001','u')")
    assert 'DEMO-001' in _rows()[0]['customer_name / order_id']


# ======================================================================
# From test_ticket_csv_sync.py
# ======================================================================

def test_sync_writes_all_rows_and_is_idempotent(tmp_path):
    rows = [{field: "" for field in FIELDS} for _ in range(2)]
    rows[0]["ticket_id"] = "FF-1"
    rows[1]["ticket_id"] = "FF-2"
    rows[1]["complaint"] = "=SUM(1,2)"
    path = tmp_path / "reports" / "tickets.csv"
    with patch("backend.services.ticket_csv_sync._rows", return_value=rows):
        assert sync_csv(path) == 2
        assert sync_csv(path) == 2
    with path.open(newline="", encoding="utf-8-sig") as stream:
        records = list(csv.DictReader(stream))
    assert len(records) == 2
    assert list(records[0]) == FIELDS
    assert records[1]["complaint"] == "'=SUM(1,2)"
    assert not list(path.parent.glob("*.tmp"))


def test_sync_failure_preserves_prior_file(tmp_path):
    path = tmp_path / "tickets.csv"
    path.write_text("previous-good-snapshot", encoding="utf-8")
    with patch("backend.services.ticket_csv_sync._rows", side_effect=RuntimeError("database offline")):
        try:
            sync_csv(path)
        except RuntimeError:
            pass
        else:
            assert False, "Expected failure"
    assert path.read_text(encoding="utf-8") == "previous-good-snapshot"


# ======================================================================
# From test_ticket_classification.py
# ======================================================================

@pytest.mark.parametrize('subject,description,expected', [
    ('Order status', 'Where is my order? It was supposed to arrive yesterday.', ('Shipping & delivery', 'Medium', 'Neutral')),
    ('Return shoes', 'I want to return the shoes I bought last week. How do I get a refund?', ('Returns & refunds', 'Low', 'Neutral')),
    ('Password reset', "I forgot my password and can't log in.", ('Account & login', 'Medium', 'Neutral')),
    ('Broken blender', 'The blender I received is broken. Can I get a replacement?', ('Product & warranty', 'Medium', 'Negative')),
    ('Card charged', "You charged my card three times! Fix this now or I'm contacting my bank and a lawyer.", ('Payments & billing', 'High', 'Negative')),
    ('Corporate gift cards', 'Do you sell gift cards for a company of 500 employees with custom branding?', ('Other / unclassified', 'Low', 'Neutral')),
])
def test_official_scenarios(subject, description, expected):
    assert classify_ticket(subject, description) == expected


def test_risk_flag_always_high():
    assert classify_ticket('General question', 'Please help.', 'LEGAL_THREAT')[1] == 'High'


def test_unknown_request_is_not_invented_category():
    assert classify_ticket('Hello', 'Can you assist?')[0] == 'Other / unclassified'


def test_payment_dispute_category_precedes_refund_keyword():
    assert classify_ticket('Refund disputed charge', 'My card was charged twice; I need a refund.')[0] == 'Payments & billing'


# ======================================================================
# From test_ticket_review_display.py
# ======================================================================

def test_password_recovery_not_described_as_refund():
    msg = review_message({'subject': 'Forgot password', 'description': "I can't log in", 'risk': ''})
    assert 'identity verification' in msg
    assert 'refund' not in msg.lower()


def test_uploaded_damage_photo_is_not_approval():
    msg = review_message({'subject': 'Broken headphones', 'description': 'Can I get a replacement?', 'risk': '', 'evidence': [{'id': 'e1'}]})
    assert 'received' in msg and 'awaiting review' in msg
    assert 'No replacement or refund has been approved' in msg


def test_missing_damage_photo_requests_photos():
    msg = review_message({'subject': 'Broken blender', 'risk': '', 'evidence': []})
    assert 'photographs' in msg


def test_refund_threshold_flag_keeps_review_warning():
    msg = review_message({'subject': 'Broken item', 'risk': 'REFUND_GE_500', 'evidence': [{'id': 'e1'}]})
    assert 'human review' in msg and 'No refund or payment has been executed' in msg


def test_payment_dispute_stays_specialist_review():
    msg = review_message({'subject': 'Forgot password', 'risk': 'PAYMENT_DISPUTE'})
    assert 'specialist human review' in msg


# ======================================================================
# From test_frontend_api_errors.py
# ======================================================================

class FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self.ok = status < 400
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def respond(monkeypatch, response=None, exc=None):
    def fake(*args, **kwargs):
        if exc:
            raise exc
        return response
    monkeypatch.setattr(api_client.requests, "request", fake)


def failure(monkeypatch, **kw):
    respond(monkeypatch, **kw)
    with pytest.raises(ApiError) as info:
        call("GET", "/x")
    return info.value


def test_success_returns_json(monkeypatch):
    respond(monkeypatch, FakeResponse(200, {"ok": True}))
    assert call("GET", "/x") == {"ok": True}


def test_client_error_shows_backend_detail_only(monkeypatch):
    err = failure(monkeypatch, response=FakeResponse(404, {"detail": "Refund review not found"}))
    assert (err.status, str(err)) == (404, "Refund review not found")
    assert "404" not in str(err) and "{" not in str(err)


def test_validation_error_shows_first_message(monkeypatch):
    body = {"detail": [{"loc": ["body", "email"], "msg": "Value error, Enter a valid email"}]}
    err = failure(monkeypatch, response=FakeResponse(422, body))
    assert str(err) == "Enter a valid email"


def test_server_error_never_exposes_server_text(monkeypatch):
    err = failure(monkeypatch, response=FakeResponse(500, {"detail": "Traceback: sqlite path C:/secret"}))
    assert err.status == 500 and str(err) == api_client.SERVER_ERROR
    assert "secret" not in str(err)


def test_non_json_client_error_uses_default(monkeypatch):
    err = failure(monkeypatch, response=FakeResponse(429, text="<html>rate limited</html>"))
    assert str(err) == api_client.DEFAULT_MESSAGES[429]


@pytest.mark.parametrize("exc,message", [
    (requests.ConnectionError("refused 127.0.0.1:8000"), api_client.UNREACHABLE),
    (requests.Timeout("read timeout"), api_client.TIMEOUT),
])
def test_network_errors(monkeypatch, exc, message):
    err = failure(monkeypatch, exc=exc)
    assert err.status is None and str(err) == message


def _script():
    import streamlit as st
    from frontend import app
    if "token" not in st.session_state:
        st.session_state.token = "session-token"
    result = app.api("GET", "/admin/refund-reviews/t1", expected={404: {}})
    st.text(f"result={result!r}")


def run_api(monkeypatch, status, message="detail"):
    from frontend import app

    def fake_call(*args, **kwargs):
        raise ApiError(status, message)
    monkeypatch.setattr(app, "call", fake_call)
    return AppTest.from_function(_script, default_timeout=30).run()


def test_expected_404_is_silent(monkeypatch):
    at = run_api(monkeypatch, 404, "Refund review not found")
    assert not at.error and not at.warning
    assert at.text[0].value == "result={}"


def test_conflict_is_a_refresh_warning(monkeypatch):
    at = run_api(monkeypatch, 409, "Stale review")
    assert not at.error
    assert "Refresh the page" in at.warning[0].value
    assert at.text[0].value == "result=None"


def test_server_error_is_shown_as_error(monkeypatch):
    from frontend.api_client import SERVER_ERROR
    at = run_api(monkeypatch, 500, SERVER_ERROR)
    assert at.error[0].value == SERVER_ERROR


def test_expired_session_signs_out(monkeypatch):
    at = run_api(monkeypatch, 401, "Invalid or expired session")
    assert at.session_state["token"] is None
    assert at.session_state["logout_warning"] == "Your session has expired. Please sign in again."


def test_escalation_reasons_are_plain_language():
    from backend.services.complaint_log import readable_reasons
    assert readable_reasons('REFUND_GE_500,LEGAL') == (
        'Refund of INR 500 or more requires human review; Legal threat or legal action mentioned')
    assert readable_reasons('') == ''
    assert readable_reasons('NEW_FLAG') == 'NEW_FLAG'  # unknown codes are never dropped


def test_dashboard_average_urgency():
    from frontend.app import average_urgency
    rows = [{'urgency': 'Low'}, {'urgency': 'High'}, {'urgency': 'Medium'}, {'urgency': 'Not assessed'}]
    assert average_urgency(rows) == '2.0 · Medium'
    assert average_urgency([{'urgency': 'High'}, {'urgency': 'High'}, {'urgency': 'Low'}]) == '2.3 · Medium'
    assert average_urgency([]) == '—'


def test_device_charging_is_not_billing():
    from backend.services.ticket_classification import classify_ticket
    assert classify_ticket('Damaged cable', 'My USB-C cable does not charge. Please refund me.')[0] == 'Returns & refunds'
    assert classify_ticket('', 'You charged my card three times!')[0] == 'Payments & billing'
    assert classify_ticket('', 'There are two charges on my statement')[0] == 'Payments & billing'


def test_log_outcome_says_what_actually_happened():
    import sqlite3
    from backend.services.complaint_log import outcome_and_reason
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE audit (ticket_id TEXT, event TEXT)')
    c.execute('CREATE TABLE auto_refund_acceptances (ticket_id TEXT)')
    c.execute("INSERT INTO audit VALUES ('reviewed', 'ADMIN_PUBLISHED')")
    c.execute("INSERT INTO auto_refund_acceptances VALUES ('auto')")
    reviewed = outcome_and_reason(c, {'id': 'reviewed', 'status': 'ANSWERED'}, 'REFUND_GE_500', True)
    assert reviewed == ('Resolved after human review',
                        'Reviewed by support: Refund of INR 500 or more requires human review')
    assert outcome_and_reason(c, {'id': 'auto', 'status': 'ANSWERED'}, '', True)[0].startswith('Passed automatic checks; queued for refund processing')
    assert outcome_and_reason(c, {'id': 'open', 'status': 'ESCALATED'}, 'LEGAL', True) == (
        'Escalated', 'Legal threat or legal action mentioned')
    assert outcome_and_reason(c, {'id': 'plain', 'status': 'ANSWERED'}, '', False) == ('Resolved', '')
