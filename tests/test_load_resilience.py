"""Timeouts and busy handling under load: offline, isolated DB, no OpenAI."""
import asyncio
import sqlite3
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.auth.security import create_session, hash_password
from backend.db import store
from backend.services import human_review
from backend.worker import main as worker


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'load.sqlite3'))
    store.init()
    from backend.services.complaint_log import migrate
    migrate()


def queued_ticket():
    uid, tid, jid = (str(uuid.uuid4()) for _ in range(3))
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,1)',
                  (uid, uid + '@example.test', 'x', 'customer'))
        c.execute('INSERT INTO tickets(id,customer_id,subject,description,status,risk,created_at) '
                  "VALUES (?,?,?,?,'QUEUED','',?)", (tid, uid, 'Where is my order', 'Where is my order?', time.time()))
        c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',
                  (str(uuid.uuid4()), tid, 'customer', 'Where is my order?', 1, time.time()))
        c.execute("INSERT INTO jobs(id,ticket_id,status,created_at,input_version) VALUES (?,?,'QUEUED',?,0)",
                  (jid, tid, time.time()))
    return tid, jid


class StuckTeam:
    """Simulates a model call that never returns."""
    async def run_stream(self, task, cancellation_token=None):
        await asyncio.sleep(3600)
        yield None


class FakeClient:
    async def close(self):
        pass


def state(tid, jid):
    with store.connect() as c:
        ticket = c.execute('SELECT status FROM tickets WHERE id=?', (tid,)).fetchone()['status']
        job = dict(c.execute('SELECT status, attempts FROM jobs WHERE id=?', (jid,)).fetchone())
        audit = [r['event'] for r in c.execute('SELECT event FROM audit WHERE ticket_id=?', (tid,))]
        replies = [r['content'] for r in c.execute(
            "SELECT content FROM messages WHERE ticket_id=? AND role='assistant'", (tid,))]
    return ticket, job, audit, replies


def test_stuck_ai_run_retries_once_then_fails_closed(db, monkeypatch):
    monkeypatch.setattr(worker, 'RUN_TIMEOUT_SECONDS', 0.2)
    monkeypatch.setattr(worker, 'make_team', lambda *a, **k: (StuckTeam(), FakeClient()))
    tid, jid = queued_ticket()

    started = time.monotonic()
    asyncio.run(worker.process(worker.claim(), retriever=None))
    assert time.monotonic() - started < 5, 'the worker must not hang on a stuck model call'
    ticket, job, audit, replies = state(tid, jid)
    assert (ticket, job['status']) == ('QUEUED', 'QUEUED')     # one clean retry
    assert 'AI_RUN_TIMEOUT_RETRY' in audit and not replies

    asyncio.run(worker.process(worker.claim(), retriever=None))
    ticket, job, audit, replies = state(tid, jid)
    assert ticket == 'ESCALATED' and job['status'] == 'FAILED' and job['attempts'] == 2
    assert 'AI_RUN_TIMEOUT' in audit
    assert len(replies) == 1 and 'could not be completed safely' in replies[0]


def test_timeout_discards_partial_result_before_retry(db):
    tid, jid = queued_ticket()
    row = worker.claim()
    with store.connect() as c:
        c.execute('INSERT INTO ai_results(job_id,ticket_id,summary,draft,citations,requires_review,created_at) '
                  "VALUES (?,?,'s','partial','[]',0,?)", (jid, tid, time.time()))
    assert worker.requeue_after_timeout(row) is True
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM ai_results WHERE job_id=?', (jid,)).fetchone()[0] == 0


def test_stuck_human_review_recording_is_retried(db, monkeypatch):
    import backend.agents.team as team

    class StuckReview:
        async def run(self, task):
            await asyncio.sleep(3600)

    monkeypatch.setattr(worker, 'REVIEW_TIMEOUT_SECONDS', 0.2)
    monkeypatch.setattr(team, 'make_review_team', lambda *a, **k: (StuckReview(), FakeClient()))
    tid, _ = queued_ticket()
    with store.connect() as c:
        human_review.enqueue(c, tid, 'admin', 'Approved reply')
    started = time.monotonic()
    assert asyncio.run(worker.process_human_review()) is True
    assert time.monotonic() - started < 5
    with store.connect() as c:
        assert c.execute('SELECT status FROM human_review_runs WHERE ticket_id=?', (tid,)).fetchone()['status'] == 'PENDING'


def test_model_and_embedding_calls_are_bounded():
    import inspect
    import backend.agents.team as team
    import backend.rag.customer_runtime as runtime
    assert team.MODEL_TIMEOUT_SECONDS <= 60
    assert 'timeout=MODEL_TIMEOUT_SECONDS' in inspect.getsource(team.make_team)
    assert 'OpenAI(timeout=10.0, max_retries=1)' in inspect.getsource(runtime._new_approved_retriever)
    assert worker.RUN_TIMEOUT_SECONDS < worker.LEASE_SECONDS


# ------------------------------------------------------------------- API --
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'api.sqlite3'))
    from backend.main import app
    with TestClient(app) as client:
        uid = str(uuid.uuid4())
        with store.connect() as c:
            c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,1)',
                      (uid, uid + '@example.test', hash_password('Load-Test-Passw0rd!'), 'customer'))
        yield client, {'Authorization': 'Bearer ' + create_session(uid)}


def test_help_center_provider_failure_is_a_friendly_503(api, monkeypatch):
    from backend.services import help_center
    client, headers = api

    def slow_provider(question):
        raise TimeoutError('embedding request timed out')
    monkeypatch.setattr(help_center, 'answer_question', slow_provider)
    r = client.post('/help/ask', headers=headers, json={'question': 'What is the return window?'})
    assert r.status_code == 503 and r.headers['Retry-After'] == '5'
    assert r.json()['detail'] == help_center.HELP_CENTER_BUSY
    assert 'timed out' not in r.text  # provider text is never echoed


def test_database_lock_is_a_retryable_503(api, monkeypatch):
    from backend import main
    client, headers = api

    def locked():
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(main, 'connect', locked)
    r = client.get('/tickets', headers=headers)
    assert r.status_code == 503 and r.headers['Retry-After'] == '2'
    assert r.json()['detail'] == main.SERVICE_BUSY


def test_other_database_errors_are_not_disguised_as_busy(api, monkeypatch):
    from backend import main
    client, headers = api

    def broken():
        raise sqlite3.OperationalError('no such table: tickets')
    monkeypatch.setattr(main, 'connect', broken)
    with pytest.raises(sqlite3.OperationalError):
        client.get('/tickets', headers=headers)


def test_frontend_shows_the_busy_message(monkeypatch):
    from frontend import api_client

    class Busy:
        status_code, ok, text = 503, False, ''
        def json(self):
            return {'detail': 'The service is busy. Please try again in a moment.'}
    monkeypatch.setattr(api_client.requests, 'request', lambda *a, **k: Busy())
    with pytest.raises(api_client.ApiError) as err:
        api_client.call('GET', '/tickets')
    assert err.value.status == 503 and 'busy' in str(err.value)


def test_timeout_cancels_the_agents_and_bounds_client_close(db, monkeypatch):
    """After a timeout the AutoGen run is cancelled and a hung close() cannot block."""
    seen = {}

    class CancellableStuckTeam:
        async def run_stream(self, task, cancellation_token=None):
            seen['token'] = cancellation_token
            await asyncio.sleep(3600)
            yield None

    class HangingClient:
        async def close(self):
            await asyncio.sleep(3600)

    monkeypatch.setattr(worker, 'RUN_TIMEOUT_SECONDS', 0.2)
    monkeypatch.setattr(worker, 'make_team', lambda *a, **k: (CancellableStuckTeam(), HangingClient()))
    real_wait_for = asyncio.wait_for
    monkeypatch.setattr(worker.asyncio, 'wait_for', lambda aw, timeout: real_wait_for(aw, timeout=min(timeout, 0.2)))
    queued_ticket()
    started = time.monotonic()
    asyncio.run(worker.process(worker.claim(), retriever=None))
    assert time.monotonic() - started < 5
    assert seen['token'] is not None and seen['token'].is_cancelled()
