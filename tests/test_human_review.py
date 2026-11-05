"""Human-in-the-loop via AutoGen UserProxyAgent: offline (replayed model), isolated DB."""
import asyncio
import json
import time
import uuid

import pytest
from autogen_agentchat.agents import UserProxyAgent
from autogen_core import FunctionCall
from autogen_core.models import CreateResult, RequestUsage
from autogen_ext.models.replay import ReplayChatCompletionClient
from fastapi.testclient import TestClient

from backend.auth.security import create_session, hash_password
from backend.db import store
from backend.main import app
from backend.services import human_review
from backend.worker import main as worker

MODEL_INFO = {'vision': False, 'function_calling': True, 'json_output': False,
              'family': 'unknown', 'structured_output': False}


def replay(*decisions):
    """A model that calls record_human_review once per given decision."""
    return ReplayChatCompletionClient([
        CreateResult(finish_reason='function_calls', cached=False,
                     usage=RequestUsage(prompt_tokens=0, completion_tokens=0),
                     content=[FunctionCall(id=str(i), name='record_human_review', arguments=json.dumps(
                         {'decision': d, 'summary': 'Specialist approved the escalated reply.'}))])
        for i, d in enumerate(decisions)], model_info=MODEL_INFO)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'hitl.sqlite3'))
    with TestClient(app) as c:
        yield c


def user(role):
    uid = str(uuid.uuid4())
    with store.connect() as db:
        db.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,1)',
                   (uid, uid + '@example.test', hash_password('Human-Review-Test1!'), role))
    return {'Authorization': 'Bearer ' + create_session(uid)}


def escalated_and_approved(client, reply='A specialist reviewed your request. No refund has been issued yet.'):
    customer, admin = user('customer'), user('admin')
    tid = client.post('/tickets', headers=customer, json={
        'subject': 'Refund request', 'description': 'Please refund my order.', 'claimed_amount': 750}).json()['id']
    row = worker.claim()
    worker.finish(row, False)  # agents finished; ticket escalated for human review
    version = client.get(f'/tickets/{tid}', headers=admin).json()['version']
    published = client.post(f'/admin/tickets/{tid}/publish', headers=admin, json={'response': reply, 'version': version})
    assert published.status_code == 200 and published.json()['human_review'] == 'QUEUED'
    return tid, customer, admin


def test_publish_queues_human_review_in_same_transaction(client):
    tid, customer, _ = escalated_and_approved(client)
    with store.connect() as db:
        runs = db.execute('SELECT status, decision FROM human_review_runs WHERE ticket_id=?', (tid,)).fetchall()
        audit = [r['event'] for r in db.execute('SELECT event FROM audit WHERE ticket_id=?', (tid,))]
    assert [(r['status'], r['decision']) for r in runs] == [('PENDING', 'APPROVED')]
    assert 'ADMIN_PUBLISHED' in audit and 'HUMAN_REVIEW_QUEUED' in audit
    # The reply is already visible: approval is final when the human publishes.
    assert client.get(f'/tickets/{tid}', headers=customer).json()['status'] == 'ANSWERED'


def test_user_proxy_agent_records_the_human_decision(client):
    tid, customer, admin = escalated_and_approved(client)
    assert asyncio.run(worker.process_human_review(model_client=replay('APPROVED'))) is True
    with store.connect() as db:
        run = db.execute('SELECT status FROM human_review_runs WHERE ticket_id=?', (tid,)).fetchone()
        agents = [r['agent'] for r in db.execute(
            "SELECT agent FROM agent_events WHERE ticket_id=? AND lease_token='human-review' ORDER BY created_at", (tid,))]
        audit = [r['event'] for r in db.execute('SELECT event FROM audit WHERE ticket_id=?', (tid,))]
    assert run['status'] == 'DONE'
    assert agents == ['SupportReviewer', 'LogAgent']
    assert 'HUMAN_REVIEW_LOGGED:APPROVED' in audit
    progress = client.get(f'/tickets/{tid}/agent-progress', headers=customer).json()
    assert progress['human_review']['status'] == 'DONE'
    detail = client.get(f'/tickets/{tid}', headers=customer).json()
    assert detail['status'] == 'ANSWERED'
    assert sum(m['role'] == 'admin' for m in detail['messages']) == 1  # never re-published
    assert asyncio.run(worker.process_human_review(model_client=replay('APPROVED'))) is False  # idle


def test_log_agent_cannot_change_the_human_decision(client):
    tid, customer, _ = escalated_and_approved(client)
    for _ in range(human_review.MAX_ATTEMPTS):
        asyncio.run(worker.process_human_review(model_client=replay('REJECTED')))
    with store.connect() as db:
        run = db.execute('SELECT status, attempts FROM human_review_runs WHERE ticket_id=?', (tid,)).fetchone()
        audit = [r['event'] for r in db.execute('SELECT event FROM audit WHERE ticket_id=?', (tid,))]
    assert (run['status'], run['attempts']) == ('FAILED', human_review.MAX_ATTEMPTS)
    assert 'HUMAN_REVIEW_RETRY' in audit and 'HUMAN_REVIEW_LOG_FAILED' in audit
    assert not any(e.startswith('HUMAN_REVIEW_LOGGED') for e in audit)
    # The human-approved reply stays published; the ticket is untouched.
    assert client.get(f'/tickets/{tid}', headers=customer).json()['status'] == 'ANSWERED'


def test_reply_text_cannot_terminate_the_review_early(client):
    tid, _, _ = escalated_and_approved(client, reply='Your case was reviewed. LOGGED. No refund issued.')
    asyncio.run(worker.process_human_review(model_client=replay('APPROVED')))
    with store.connect() as db:
        assert db.execute('SELECT status FROM human_review_runs WHERE ticket_id=?', (tid,)).fetchone()['status'] == 'DONE'


def test_review_team_uses_autogen_user_proxy_with_stored_decision():
    from backend.agents.team import make_review_team
    run = {'id': 'r', 'ticket_id': 't', 'ticket_code': 'FF-1', 'decision': 'APPROVED', 'response': 'Approved text'}
    team, _ = make_review_team(run, lambda d, s: 'LOGGED', model_client=replay('APPROVED'))
    proxy = team._participants[0]
    assert isinstance(proxy, UserProxyAgent) and proxy.name == 'SupportReviewer'
    assert 'Approved text' in human_review.reviewer_message(run)


def test_stale_processing_run_is_retried(client):
    tid, _, _ = escalated_and_approved(client)
    first = human_review.claim()
    with store.connect() as db:
        db.execute('UPDATE human_review_runs SET started_at=? WHERE id=?',
                   (time.time() - human_review.STALE_SECONDS - 5, first['id']))
    again = human_review.claim()
    assert again['id'] == first['id'] and again['attempts'] == 2
