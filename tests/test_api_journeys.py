"""Consolidated api journeys tests.

Merged without behaviour changes from:
- test_customer_admin_journeys.py: Isolated API journeys: no real OpenAI calls and no production DB writes.
- test_phase5_policy_security.py: Phase 5: deterministic FixFlow policy/security regression tests.
- test_phase8d_safety.py: Additive Phase 8D API safety tests. Uses temporary SQLite; no live services.
- test_phase10_consolidated.py: Phase 10 additional API-level acceptance tests; isolated SQLite, no live AI/network.
- test_rc1_final_gate.py: RC1 fixed release gate: isolated customer, refund, and real process restart tests.
- test_five_issue_closure.py: regression tests
"""
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.auth.security import hash_password
from backend.main import app
import os
import time
from pathlib import Path
from backend.services.risk import screen
from backend.services.help_center import answer_question
from backend.auth.security import hash_password, create_session
import subprocess
import sys
import json
from unittest.mock import patch


# ======================================================================
# From test_customer_admin_journeys.py
# ======================================================================

PASSWORD = 'GoodPassword!234'

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'journey.sqlite3'))
    monkeypatch.setenv('FIXFLOW_LOCAL_DEMO', '1')
    # Never contact real SMTP from tests (no outbound email to test addresses).
    from backend import main
    monkeypatch.setattr(main, 'send_token', lambda email, purpose, token: None)
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


@pytest.mark.parametrize('amount,expected', [(499, 'QUEUED'), (500, 'QUEUED'), (501, 'QUEUED')])
def test_refund_boundary_end_to_end(env, amount, expected):
    customer = account(env, f'refund{amount}@example.com')
    result = create_ticket(env, customer, 'Please refund my purchase', amount)
    assert result['status'] == expected
    with store.connect() as db:
        jobs = db.execute('SELECT status FROM jobs WHERE ticket_id=?', (result['id'],)).fetchall()
    assert len(jobs) == 1
    if amount >= 500:
        assert 'REFUND_GE_500' in result['risk_flags']


def test_admin_review_version_guard_and_customer_response(env):
    customer = account(env, 'reviewer-customer@example.com')
    admin = account(env, 'reviewer-admin@example.com', 'admin')
    ticket = create_ticket(env, customer, 'Please refund my order', 750)
    tid = ticket['id']
    assert ticket['status'] == 'QUEUED'
    # Not in the review queue until the worker finalizes the agent run.
    assert all(row['id'] != tid for row in env.get('/admin/queue', headers=admin).json())
    from backend.worker import main as worker
    row = worker.claim()
    assert row is not None
    worker.finish(row, False)
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
    assert ticket['status'] == 'QUEUED'
    assert 'FRAUD' in ticket['risk_flags']


def test_risky_followup_escalates_existing_ticket(env):
    customer = account(env, 'riskyfollowup@example.com')
    ticket = create_ticket(env, customer)
    response = env.post(f"/tickets/{ticket['id']}/reply", headers=customer,
                        json={'content': 'I want a refund of this order'})
    assert response.status_code == 200, response.text
    assert response.json()['status'] in {'QUEUED', 'AWAITING_CUSTOMER'}
    with store.connect() as db:
        assert not db.execute('SELECT 1 FROM jobs WHERE ticket_id=? AND status="QUEUED"',
                              (ticket['id'],)).fetchone()


def test_refund_followup_without_amount_asks_for_refund_details(env):
    from backend.main import REFUND_CLARIFICATION
    customer = account(env, 'refundfollowup@example.com')
    tid = create_ticket(env, customer)['id']
    response = env.post(f'/tickets/{tid}/reply', headers=customer,
                        json={'content': 'I want a refund of this order'})
    assert response.json()['status'] == 'AWAITING_CUSTOMER'
    detail = env.get(f'/tickets/{tid}', headers=customer).json()
    assert detail['messages'][-1]['content'] == REFUND_CLARIFICATION


def test_risky_refund_followup_without_amount_keeps_risk_and_queues(env):
    customer = account(env, 'fraudfollowup@example.com')
    tid = create_ticket(env, customer)['id']
    response = env.post(f'/tickets/{tid}/reply', headers=customer,
                        json={'content': 'This is fraud, I want a refund of this order'})
    assert response.json()['status'] == 'QUEUED'
    with store.connect() as db:
        risk = db.execute('SELECT risk FROM tickets WHERE id=?', (tid,)).fetchone()['risk']
    assert 'FRAUD' in risk.split(',')


# ======================================================================
# From test_phase5_policy_security.py
# ======================================================================

@pytest.fixture
def env_phase5_policy_security(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "phase5.sqlite3"))
    store.init()
    # Main API startup also performs customer-order migrations.
    from backend.services.customer_orders import migrate
    migrate()
    from backend.main import app
    with TestClient(app) as client:
        yield client


def make_user(email, role="customer", verified=1):
    from backend.auth.security import hash_password, create_session
    uid = str(uuid.uuid4())
    with store.connect() as c:
        c.execute(
            "INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)",
            (uid, email, hash_password("Temporary-Test-Password1!"), role, verified),
        )
    return {"id": uid, "headers": {"Authorization": "Bearer " + create_session(uid)}}


def create(client, user, amount=None, subject="Delivery delay", description="My parcel has not arrived after the expected delivery date."):
    payload = {"subject": subject, "description": description, "claimed_amount": amount}
    response = client.post("/tickets", json=payload, headers=user["headers"])
    assert response.status_code == 201, response.text
    return response.json()


def escalate_via_worker():
    """Drive the queued ticket through worker finalization to ESCALATED (offline)."""
    from backend.worker import main as worker
    row = worker.claim()
    assert row is not None
    worker.finish(row, False)


def test_refund_threshold_inclusive():
    assert "REFUND_GE_500" not in screen("Please refund me for my order", 499)
    assert "REFUND_GE_500" in screen("Please refund me for my order", 500)
    assert "REFUND_GE_500" in screen("Please refund me for my order", 500.01)


def test_risk_escalations_and_ambiguous_currency():
    assert "FRAUD" in screen("I suspect fraud on my account", 20)
    assert "LEGAL" in screen("I will contact a lawyer", 20)
    assert "PAYMENT_DISPUTE" in screen("I was charged twice", 20)
    assert "REFUND_AMOUNT_UNVERIFIED" in screen("Please refund me", None)
    assert "REFUND_CURRENCY_UNCERTAIN" in screen("Please refund me $30", 30)


def test_general_policy_question_is_not_refund_case():
    assert "REFUND_GE_500" not in screen("What is the refund policy?")
    # A supplied claimed amount >= INR 500 is fail-closed regardless of wording.
    assert "REFUND_GE_500" in screen("What is the refund policy?", 900)


def test_customer_ticket_isolation_and_admin_access(env_phase5_policy_security):
    alice = make_user("phase5-alice@example.test")
    bob = make_user("phase5-bob@example.test")
    admin = make_user("phase5-admin@example.test", role="admin")
    tid = create(env_phase5_policy_security, alice)["id"]
    assert env_phase5_policy_security.get(f"/tickets/{tid}", headers=alice["headers"]).status_code == 200
    assert env_phase5_policy_security.get(f"/tickets/{tid}", headers=bob["headers"]).status_code == 404
    assert env_phase5_policy_security.post(f"/tickets/{tid}/reply", json={"content": "Not my ticket"}, headers=bob["headers"]).status_code == 404
    assert env_phase5_policy_security.get(f"/tickets/{tid}", headers=admin["headers"]).status_code == 200
    assert all(t["id"] != tid for t in env_phase5_policy_security.get("/tickets", headers=bob["headers"]).json())


def test_admin_endpoints_reject_customer(env_phase5_policy_security):
    customer = make_user("phase5-customer@example.test")
    tid = create(env_phase5_policy_security, customer)["id"]
    assert env_phase5_policy_security.get("/admin/queue", headers=customer["headers"]).status_code == 403
    assert env_phase5_policy_security.get(f"/admin/audit/{tid}", headers=customer["headers"]).status_code == 403
    assert env_phase5_policy_security.get("/admin/export.csv", headers=customer["headers"]).status_code == 403
    assert env_phase5_policy_security.post(f"/admin/tickets/{tid}/publish", json={"response": "Unauthorized", "version": 0}, headers=customer["headers"]).status_code == 403


def test_unverified_customer_cannot_submit(env_phase5_policy_security):
    user = make_user("phase5-unverified@example.test", verified=0)
    r = env_phase5_policy_security.post("/tickets", json={"subject": "Shipping issue", "description": "My shipment has not arrived yet."}, headers=user["headers"])
    assert r.status_code == 403


def test_high_value_refund_draft_not_customer_visible(env_phase5_policy_security):
    customer = make_user("phase5-refund@example.test")
    admin = make_user("phase5-reviewer@example.test", role="admin")
    ticket = create(env_phase5_policy_security, customer, 500, "Refund request", "Please refund me for the missing order.")
    tid = ticket["id"]
    assert ticket["status"] == "QUEUED"
    assert "REFUND_GE_500" in ticket["risk_flags"]
    escalate_via_worker()
    assert env_phase5_policy_security.get(f"/tickets/{tid}", headers=admin["headers"]).json()["status"] == "ESCALATED"
    with store.connect() as c:
        c.execute("INSERT INTO ai_results(job_id,ticket_id,summary,draft,citations,requires_review,created_at) VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), tid, "Review", "INTERNAL_DRAFT_NOT_APPROVED", "[]", 1, time.time()))
    customer_data = env_phase5_policy_security.get(f"/tickets/{tid}", headers=customer["headers"]).json()
    assert "drafts" not in customer_data
    assert "INTERNAL_DRAFT_NOT_APPROVED" not in str(customer_data)
    admin_data = env_phase5_policy_security.get(f"/tickets/{tid}", headers=admin["headers"]).json()
    assert any("INTERNAL_DRAFT_NOT_APPROVED" in d["draft"] for d in admin_data["drafts"])
    assert all("INTERNAL_DRAFT_NOT_APPROVED" not in m["content"] for m in customer_data["messages"])


def test_approval_version_and_one_time_publish(env_phase5_policy_security):
    customer = make_user("phase5-approval@example.test")
    admin = make_user("phase5-approver@example.test", role="admin")
    tid = create(env_phase5_policy_security, customer, 500, "Refund request", "Please refund me for a broken product.")["id"]
    queued = env_phase5_policy_security.get(f"/tickets/{tid}", headers=admin["headers"]).json()["version"]
    # A queued ticket is not in review yet; publish must refuse it.
    assert env_phase5_policy_security.post(f"/admin/tickets/{tid}/publish", json={"response": "Too early", "version": queued}, headers=admin["headers"]).status_code == 409
    escalate_via_worker()
    version = env_phase5_policy_security.get(f"/tickets/{tid}", headers=admin["headers"]).json()["version"]
    url = f"/admin/tickets/{tid}/publish"
    assert env_phase5_policy_security.post(url, json={"response": "Old approval", "version": version + 1}, headers=admin["headers"]).status_code == 409
    assert env_phase5_policy_security.post(url, json={"response": "Your request is under review; no refund was issued.", "version": version}, headers=admin["headers"]).status_code == 200
    assert env_phase5_policy_security.post(url, json={"response": "Duplicate", "version": version}, headers=admin["headers"]).status_code == 409
    data = env_phase5_policy_security.get(f"/tickets/{tid}", headers=customer["headers"]).json()
    assert data["status"] == "ANSWERED"
    assert sum("no refund was issued" in m["content"] for m in data["messages"]) == 1


class FakeSearch:
    def __init__(self, passages):
        self.passages = passages
        self.calls = []

    def search(self, query, k=4):
        self.calls.append((query, k))
        return self.passages[:k]


def test_help_center_blocks_case_specific_requests_without_search():
    search = FakeSearch([{"id": "x", "source": "01_Shipping_and_Delivery_SOP.pdf", "text": "shipping delivery"}])
    result = answer_question("Please refund me for my order", search=search)
    assert result["status"] == "ticket_required"
    assert result["sources"] == []
    assert not search.calls


def test_help_center_no_evidence_fallback():
    result = answer_question("What is the shipping delivery timeframe?", search=FakeSearch([]))
    assert result["status"] == "no_evidence"
    assert result["sources"] == []


def test_help_center_accepts_actual_approved_pdf_evidence():
    """Regression: current help-center mapping may still use obsolete .md names."""
    search = FakeSearch([{
        "id": "approved-shipping-1",
        "source": "01_Shipping_and_Delivery_SOP.pdf",
        "version": "pdf-sop-v1",
        "visibility": "customer",
        "text": "Shipping delivery timeframe: standard delivery normally takes five business days.",
    }])
    result = answer_question("What is the shipping delivery timeframe?", search=search)
    assert result["status"] == "answered", (
        "Help Center rejected relevant evidence from the actual indexed PDF filename: " + str(result)
    )
    assert result["sources"][0]["document"] == "01_Shipping_and_Delivery_SOP.pdf"
    assert "five business days" in result["answer"]


def test_help_center_does_not_leak_internal_sop():
    search = FakeSearch([{
        "id": "internal-1",
        "source": "06_Escalations_and_Soft_Skills_SOP.pdf",
        "version": "pdf-sop-v1",
        "text": "Internal escalation and customer handling instructions are confidential.",
    }])
    result = answer_question("What are the internal escalation handling instructions?", search=search)
    assert result["status"] != "answered"
    assert result["sources"] == []
    assert "confidential" not in result["answer"].lower()


# ======================================================================
# From test_phase8d_safety.py
# ======================================================================

@pytest.fixture
def env_phase8d_safety(tmp_path, monkeypatch):
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


def escalate_via_worker_phase8d_safety():
    """Drive the queued ticket through worker finalization to ESCALATED (offline)."""
    from backend.worker import main as worker
    row = worker.claim()
    assert row is not None
    worker.finish(row, False)


@pytest.mark.parametrize('amount,status,jobs', [
    (499, 'QUEUED', 1), (500, 'QUEUED', 1), (501, 'QUEUED', 1),
])
def test_backend_refund_boundary_is_persisted(env_phase8d_safety, amount, status, jobs):
    customer = user()
    result = ticket(env_phase8d_safety, customer, amount)
    assert result['status'] == status
    with store.connect() as db:
        persisted = db.execute('SELECT status FROM tickets WHERE id=?', (result['id'],)).fetchone()
        job_count = db.execute('SELECT COUNT(*) FROM jobs WHERE ticket_id=?', (result['id'],)).fetchone()[0]
    assert persisted['status'] == status
    assert job_count == jobs
    if amount >= 500:
        assert 'REFUND_GE_500' in result['risk_flags']


@pytest.mark.parametrize('description,expected_flag', [
    ('I suspect fraud and request a refund', 'FRAUD'),
    ('I will contact a lawyer, please refund me', 'LEGAL'),
    ('I was charged twice, please refund me', 'PAYMENT_DISPUTE'),
])
def test_low_value_sensitive_refund_escalates(env_phase8d_safety, description, expected_flag):
    customer = user()
    result = ticket(env_phase8d_safety, customer, 20, description)
    assert result['status'] == 'QUEUED'
    assert expected_flag in result['risk_flags']
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs WHERE ticket_id=?', (result['id'],)).fetchone()[0] == 1


def test_high_value_ticket_survives_new_client_and_remains_unpublished(env_phase8d_safety):
    customer = user()
    admin = user('admin')
    result = ticket(env_phase8d_safety, customer, 500)
    tid = result['id']
    # New TestClient instance, same temporary database: tests durable SQLite state,
    # not an operating-system process restart.
    with TestClient(app) as second_client:
        customer_view = second_client.get(f'/tickets/{tid}', headers=customer)
        assert customer_view.status_code == 200
        detail = customer_view.json()
        assert detail['status'] == 'QUEUED'
        assert 'REFUND_GE_500' in detail['risk'].split(',')
        assert not any(m['role'] == 'assistant' for m in detail['messages'])
        queue = second_client.get('/admin/queue', headers=admin)
        assert queue.status_code == 200
        assert not any(item['id'] == tid for item in queue.json()), 'Review queue is populated after worker finalization'


def test_admin_review_response_does_not_claim_refund_executed(env_phase8d_safety):
    customer = user()
    admin = user('admin')
    result = ticket(env_phase8d_safety, customer, 501)
    tid = result['id']
    escalate_via_worker_phase8d_safety()
    version = env_phase8d_safety.get(f'/tickets/{tid}', headers=admin).json()['version']
    text = 'Your case was reviewed; no refund has been issued.'
    publish = env_phase8d_safety.post(f'/admin/tickets/{tid}/publish', headers=admin,
                       json={'response': text, 'version': version})
    assert publish.status_code == 200, publish.text
    duplicate = env_phase8d_safety.post(f'/admin/tickets/{tid}/publish', headers=admin,
                         json={'response': 'Duplicate review', 'version': version})
    assert duplicate.status_code == 409
    detail = env_phase8d_safety.get(f'/tickets/{tid}', headers=customer).json()
    assert detail['status'] == 'ANSWERED'
    assert sum(m['content'] == text for m in detail['messages']) == 1


# ======================================================================
# From test_phase10_consolidated.py
# ======================================================================

PASSWORD_phase10_consolidated = 'GoodPassword!234'

@pytest.fixture
def env_phase10_consolidated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'phase10.sqlite3'))
    monkeypatch.setenv('FIXFLOW_LOCAL_DEMO', '0')
    with TestClient(app) as client:
        yield client

def login(client, role='customer'):
    email = f'p10-{uuid.uuid4().hex}@example.test'
    with store.connect() as db:
        db.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                   (str(uuid.uuid4()), email, hash_password(PASSWORD_phase10_consolidated), role, 1))
    response = client.post('/auth/login', json={'email': email, 'password': PASSWORD_phase10_consolidated})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['access_token']}

def ticket_phase10_consolidated(client, auth, description, amount=None):
    response = client.post('/tickets', headers=auth, json={
        'subject': 'Phase 10 synthetic support case',
        'description': description,
        'claimed_amount': amount,
    })
    assert response.status_code == 201, response.text
    return response.json()

@pytest.mark.parametrize('amount,expected', [(499, 'QUEUED'), (500, 'QUEUED'), (501, 'QUEUED')])
def test_refund_threshold_and_review_record(env_phase10_consolidated, amount, expected):
    customer = login(env_phase10_consolidated)
    created = ticket_phase10_consolidated(env_phase10_consolidated, customer, 'Please refund my order', amount)
    assert created['status'] == expected
    with store.connect() as db:
        reviews = db.execute('SELECT status,execution_status FROM refund_reviews WHERE ticket_id=?',
                             (created['id'],)).fetchall()
        jobs = db.execute('SELECT status FROM jobs WHERE ticket_id=?', (created['id'],)).fetchall()
    if amount >= 500:
        assert len(reviews) == 1
        assert reviews[0]['status'] == 'PENDING'
        assert reviews[0]['execution_status'] == 'NOT_CONFIGURED'
        assert len(jobs) == 1 and jobs[0]['status'] == 'QUEUED'
    else:
        assert not reviews
        assert len(jobs) == 1 and jobs[0]['status'] == 'QUEUED'

@pytest.mark.parametrize('decision', ['APPROVED', 'REJECTED'])
def test_refund_decision_is_admin_only_idempotent_and_nonexecuting(env_phase10_consolidated, decision):
    customer, admin = login(env_phase10_consolidated), login(env_phase10_consolidated, 'admin')
    created = ticket_phase10_consolidated(env_phase10_consolidated, customer, 'Please refund my order', 750)
    tid = created['id']
    endpoint = f'/admin/refund-reviews/{tid}'
    assert env_phase10_consolidated.get(endpoint, headers=customer).status_code == 403
    review = env_phase10_consolidated.get(endpoint, headers=admin)
    assert review.status_code == 200, review.text
    payload = {'decision': decision, 'reason': 'Synthetic reviewer acceptance test',
               'version': review.json()['version']}
    assert env_phase10_consolidated.post(endpoint + '/decision', headers=customer, json=payload).status_code == 403
    if decision == 'APPROVED':
        prepare_refund_prerequisites(env_phase10_consolidated, tid, admin)
    accepted = env_phase10_consolidated.post(endpoint + '/decision', headers=admin, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['status'] == decision
    assert accepted.json()['execution_status'] == 'NOT_CONFIGURED'
    assert env_phase10_consolidated.post(endpoint + '/decision', headers=admin, json=payload).status_code == 409
    final = env_phase10_consolidated.get(endpoint, headers=admin).json()
    assert final['status'] == decision
    assert final['execution_status'] == 'NOT_CONFIGURED'
    assert final['version'] == review.json()['version'] + 1

def test_customer_isolation_and_admin_endpoints(env_phase10_consolidated):
    alice, bob, admin = login(env_phase10_consolidated), login(env_phase10_consolidated), login(env_phase10_consolidated, 'admin')
    created = ticket_phase10_consolidated(env_phase10_consolidated, alice, 'Where is my parcel?')
    tid = created['id']
    assert env_phase10_consolidated.get(f'/tickets/{tid}', headers=bob).status_code == 404
    assert env_phase10_consolidated.post(f'/tickets/{tid}/reply', headers=bob,
                    json={'content': 'Unauthorized synthetic reply'}).status_code == 404
    assert env_phase10_consolidated.get('/admin/queue', headers=alice).status_code == 403
    assert env_phase10_consolidated.get('/admin/export.csv', headers=alice).status_code == 403
    assert env_phase10_consolidated.get(f'/tickets/{tid}', headers=admin).status_code == 200

def test_sensitive_case_is_queued_for_review_agents(env_phase10_consolidated):
    customer = login(env_phase10_consolidated)
    created = ticket_phase10_consolidated(env_phase10_consolidated, customer, 'I suspect fraud involving a payment and need a dispute reviewed')
    assert created['status'] == 'QUEUED'
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs WHERE ticket_id=?',
                          (created['id'],)).fetchone()[0] == 1
    assert {'FRAUD', 'PAYMENT_DISPUTE'} & set(created['risk_flags'])


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


# ======================================================================
# From test_rc1_final_gate.py
# ======================================================================

@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = tmp_path / 'rc1_final_gate.sqlite3'
    monkeypatch.setattr(store, 'DB', str(db))
    with TestClient(app) as client:
        yield client, db


def identity(role='customer'):
    uid = str(uuid.uuid4())
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid, uid+'@example.test', hash_password('Testing123!Secure'), role, 1))
    return {'Authorization': 'Bearer '+create_session(uid)}


def ticket_rc1_final_gate(client, auth):
    response = client.post('/tickets', headers=auth, json={
        'subject': 'Order packaging question',
        'description': 'The packaging arrived with a damaged corner.'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_customer_isolation_read_write_and_listing(setup):
    client, _ = setup
    alice, bob = identity(), identity()
    tid = ticket_rc1_final_gate(client, alice)
    assert client.get('/tickets/'+tid, headers=bob).status_code == 404
    assert client.post('/tickets/'+tid+'/reply', headers=bob,
                       json={'content': 'I want to alter this ticket'}).status_code == 404
    assert all(t['id'] != tid for t in client.get('/tickets', headers=bob).json())
    assert client.get('/tickets/'+tid, headers=alice).status_code == 200
    assert client.post('/tickets/'+tid+'/reply', headers=alice,
                       json={'content': 'Please check this packaging issue'}).status_code == 200


@pytest.mark.parametrize('amount,review_expected', [(499,False),(500,True),(501,True)])
def test_refund_first_mentioned_in_followup(setup, amount, review_expected):
    client, _ = setup
    owner = identity()
    tid = ticket_rc1_final_gate(client, owner)
    response = client.post('/tickets/'+tid+'/reply', headers=owner,
                           json={'content': f'Please refund me ₹{amount} for my damaged order.'})
    assert response.status_code == 200, response.text
    with store.connect() as c:
        review = c.execute('SELECT * FROM refund_reviews WHERE ticket_id=?', (tid,)).fetchone()
        current = c.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
    assert (review is not None) is review_expected
    if review_expected:
        assert review['status'] == 'PENDING'
        assert review['execution_status'] == 'NOT_CONFIGURED'
        assert current['status'] == 'QUEUED'
        assert 'REFUND_GE_500' in current['risk']


def test_approval_survives_actual_new_python_process(setup):
    client, db = setup
    owner = identity()
    tid = ticket_rc1_final_gate(client, owner)
    result = client.post('/tickets/'+tid+'/reply', headers=owner,
                         json={'content': 'Please refund me ₹500 for the damaged order.'})
    assert result.status_code == 200, result.text
    # A separate interpreter and SQLite connection simulate a genuine process restart.
    code = '''import os, sqlite3, json
from backend.db import store
from backend.services.refund_approvals import migrate
migrate()
c=sqlite3.connect(os.environ['FIXFLOW_DB'])
r=c.execute('SELECT status, execution_status FROM refund_reviews WHERE ticket_id=?', (os.environ['RC1_TICKET'],)).fetchone()
print(json.dumps(r))
'''
    env = dict(os.environ, FIXFLOW_DB=str(db), RC1_TICKET=tid)
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, '-c', code], cwd=root, env=env,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert '["PENDING", "NOT_CONFIGURED"]' in proc.stdout


def test_worker_lease_recovery_across_real_process_restart(setup):
    _, db = setup
    with store.connect() as c:
        uid, tid, jid = (str(uuid.uuid4()) for _ in range(3))
        import time
        now = time.time()
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid,uid+'@example.test','test-only','customer',1))
        c.execute('INSERT INTO tickets(id,customer_id,subject,description,status,risk,created_at) VALUES (?,?,?,?,?,?,?)',
                  (tid,uid,'Delivery query','Where is my order?','QUEUED','',now))
        c.execute('INSERT INTO jobs(id,ticket_id,status,created_at,input_version) VALUES (?,?,?,?,?)',
                  (jid,tid,'QUEUED',now,0))
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, FIXFLOW_DB=str(db), RC1_JOB=jid)
    claim_code = '''import json
from backend.worker.main import claim
print(json.dumps(claim()))'''
    first = subprocess.run([sys.executable,'-c',claim_code],cwd=root,env=env,
                           capture_output=True,text=True,timeout=30)
    assert first.returncode == 0, first.stderr
    import json, time
    old = json.loads(first.stdout.strip().splitlines()[-1])
    assert old['id'] == jid
    with store.connect() as c:
        c.execute('UPDATE jobs SET heartbeat=? WHERE id=?', (time.time()-10000,jid))
    recover_code = '''import json
from backend.worker.main import recover,claim
recover()
print(json.dumps(claim()))'''
    second = subprocess.run([sys.executable,'-c',recover_code],cwd=root,env=env,
                            capture_output=True,text=True,timeout=30)
    assert second.returncode == 0, second.stderr
    new = json.loads(second.stdout.strip().splitlines()[-1])
    assert new['id'] == jid
    assert new['lease'] != old['lease']
    with store.connect() as c:
        row = c.execute('SELECT status,attempts FROM jobs WHERE id=?',(jid,)).fetchone()
        assert row['status']=='PROCESSING' and row['attempts']==2


# ======================================================================
# From test_five_issue_closure.py
# ======================================================================

ROOT = Path(__file__).resolve().parents[1]

def test_20_plus_real_pdf_cases():
    from pypdf import PdfReader
    cases=json.loads((ROOT/'eval/golden_cases.json').read_text())
    assert len(cases)>=20
    for case in cases:
        pdf=ROOT/'policies'/case['expected_source']
        assert pdf.is_file()
        content=' '.join(p.extract_text() or '' for p in PdfReader(str(pdf)).pages)
        assert case['evidence_phrase'].casefold() in content.casefold(),case['id']

def test_citation_metadata_has_page_and_department():
    from backend.rag.hybrid import citation_metadata
    item=citation_metadata({'id':'abc','source':'01_Shipping_and_Delivery_SOP.pdf','page':3,'chunk':2,'visibility':'customer'})
    assert item['page']==3 and item['department']=='Shipping & Delivery'

def test_gmail_mailer_no_plaintext_file():
    from backend.auth.mailer import send_token
    with patch.dict('os.environ', {'FIXFLOW_SMTP_USER':'test@gmail.com','FIXFLOW_SMTP_APP_PASSWORD':'app-password'}):
        with patch('smtplib.SMTP_SSL') as smtp:
            send_token('customer@example.com','verify','SECRET_CODE')
            msg=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            assert 'SECRET_CODE' in msg.get_content()
            assert msg['To']=='customer@example.com'
    assert 'local_mailbox.txt' not in (ROOT/'backend/main.py').read_text()

def test_password_reset_policy_is_enforced():
    from backend.main import validate_registration
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        validate_registration('x@example.com','weakpassword')

def test_retriever_reuses_bm25():
    import inspect
    from backend.rag.hybrid import HybridRetriever
    source=inspect.getsource(HybridRetriever.search)
    assert 'BM25Okapi(' not in source
    assert 'faiss.read_index(' not in source

def test_frontend_api_is_configurable():
    assert 'FIXFLOW_API_URL' in (ROOT/'frontend/api_client.py').read_text()
