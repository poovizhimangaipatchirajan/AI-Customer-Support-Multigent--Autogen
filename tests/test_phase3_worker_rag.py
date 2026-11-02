"""FixFlow Phase 3: offline worker/RAG safety and reliability tests.

Run from project root: python -m pytest tests/test_phase3_worker_rag.py -v
All tests use an isolated temporary SQLite database. No OpenAI calls.
"""
import json
import time
import uuid
from types import SimpleNamespace

import pytest

from backend.db import store
from backend.worker import main as worker
from backend.rag import hybrid


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'phase3.sqlite3'))
    store.init()
    return str(tmp_path / 'phase3.sqlite3')


def seed_ticket(*, risk='', status='QUEUED', version=0, job_status='QUEUED', attempts=0):
    uid, tid, jid = (str(uuid.uuid4()) for _ in range(3))
    now = time.time()
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid, f'{uid}@example.test', 'not-a-real-password-hash', 'customer', 1))
        c.execute('INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at,version) '
                  'VALUES (?,?,?,?,?,?,?,?,?)',
                  (tid, uid, 'Delivery question', 'Where is my fictional order?', None, status, risk, now, version))
        c.execute('INSERT INTO jobs(id,ticket_id,status,created_at,input_version,attempts) '
                  'VALUES (?,?,?,?,?,?)', (jid, tid, job_status, now, version, attempts))
    return tid, jid


def state(tid, jid):
    with store.connect() as c:
        t = dict(c.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone())
        j = dict(c.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
        messages = [dict(r) for r in c.execute('SELECT * FROM messages WHERE ticket_id=?', (tid,))]
        return t, j, messages


def test_claim_sets_processing_and_lease(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    assert row is not None and row['id'] == jid and row['ticket_id'] == tid
    assert row['lease'] and row['version'] == 0
    ticket, job, _ = state(tid, jid)
    assert ticket['status'] == job['status'] == 'PROCESSING'
    assert job['attempts'] == 1
    assert job['lease_token'] == row['lease']
    assert worker.claim() is None  # single globally active job


def test_safe_draft_publishes_once(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    assert worker.persist(jid, tid, 'Delivery summary', 'Here is the policy guidance.',
                          ['chunk-1'], False, {'chunk-1': {'id': 'chunk-1', 'visibility': 'customer', 'source': 'customer_policy.pdf'}}) == 'PERSISTED'
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'ANSWERED'
    assert job['status'] == 'DONE'
    assert len([m for m in messages if m['role'] == 'assistant' and m['visible']]) == 1
    worker.finish(row, True)
    _, _, messages_again = state(tid, jid)
    assert len(messages_again) == len(messages)


def test_missing_evidence_stays_private_and_escalates(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    worker.persist(jid, tid, 'Uncertain', 'Unverified response', ['invented'], False, {})
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert job['status'] == 'DONE'
    assert not any(m['role'] == 'assistant' and m['visible'] for m in messages)
    with store.connect() as c:
        draft = c.execute('SELECT * FROM ai_results WHERE job_id=?', (jid,)).fetchone()
    assert draft is not None and draft['requires_review'] == 1


def test_risky_ticket_never_auto_publishes(isolated_db):
    tid, jid = seed_ticket(risk='REFUND_GE_500')
    row = worker.claim()
    worker.persist(jid, tid, 'Refund', 'Draft only', ['chunk-1'], False,
                   {'chunk-1': {'id': 'chunk-1', 'visibility': 'customer', 'source': 'customer_policy.pdf'}})
    worker.finish(row, True)
    ticket, _, messages = state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert not any(m['role'] == 'assistant' and m['visible'] for m in messages)


def test_stale_result_never_publishes(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    worker.persist(jid, tid, 'Summary', 'Old answer', ['chunk-1'], False,
                   {'chunk-1': {'id': 'chunk-1', 'visibility': 'customer', 'source': 'customer_policy.pdf'}})
    with store.connect() as c:
        c.execute("UPDATE tickets SET version=version+1, status='QUEUED' WHERE id=?", (tid,))
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'QUEUED'
    assert job['status'] == 'SUPERSEDED'
    assert not any(m['role'] == 'assistant' and m['visible'] for m in messages)


def test_expired_worker_lease_recovers_to_queue(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    with store.connect() as c:
        c.execute('UPDATE jobs SET heartbeat=? WHERE id=?',
                  (time.time() - worker.LEASE_SECONDS - 20, jid))
    worker.recover()
    ticket, job, _ = state(tid, jid)
    assert ticket['status'] == job['status'] == 'QUEUED'
    assert job['lease_token'] is None


def test_retry_exhaustion_escalates(isolated_db):
    tid, jid = seed_ticket(attempts=1)
    worker.claim()  # attempts becomes 2
    with store.connect() as c:
        c.execute('UPDATE jobs SET heartbeat=? WHERE id=?',
                  (time.time() - worker.LEASE_SECONDS - 20, jid))
    worker.recover()
    ticket, job, _ = state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert job['status'] == 'FAILED'


def test_log_acknowledgement_must_be_from_log_agent():
    assert worker.logged_by_agent([SimpleNamespace(source='LogAgent', content='LOGGED')])
    assert not worker.logged_by_agent([SimpleNamespace(source='QueryAgent', content='LOGGED')])
    assert not worker.logged_by_agent([SimpleNamespace(source='LogAgent', content='not finished')])


def test_hybrid_rrf_and_internal_filter_without_network(monkeypatch):
    # Construct a retriever without reading indexes or making API calls.
    r = hybrid.HybridRetriever.__new__(hybrid.HybridRetriever)
    r.docs = [
        {'id': 'public-1', 'text': 'shipping delivery', 'visibility': 'customer'},
        {'id': 'internal-1', 'text': 'internal escalation', 'visibility': 'internal'},
        {'id': 'public-2', 'text': 'returns refund', 'visibility': 'customer'},
    ]
    r.model = 'test-embedding-model'
    r.idx = SimpleNamespace(search=lambda vec, count: (None, [[1, 0, 2]]))
    r.bm = SimpleNamespace(get_scores=lambda tokens: [5.0, 99.0, 3.0])
    r.client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kw:
        SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0])])))
    monkeypatch.setattr(hybrid.faiss, 'normalize_L2', lambda x: None)
    public = r.search('shipping', k=3)
    assert [x['id'] for x in public] == ['public-1', 'public-2']
    all_results = r.search('shipping', k=3, include_internal=True)
    assert len(all_results) == 3
    assert all_results[0]['id'] == 'internal-1'


def test_empty_query_skips_embedding_call():
    r = hybrid.HybridRetriever.__new__(hybrid.HybridRetriever)
    assert r.search('   ') == []
    assert r.search('hello', k=0) == []


def test_missing_visibility_escalates_without_publication(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    worker.persist(jid, tid, 'Summary', 'Unverified draft', ['chunk-1'], False,
                   {'chunk-1': {'id': 'chunk-1', 'source': 'customer_policy.pdf'}})
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert not any(m['role'] == 'assistant' and m['visible'] for m in messages)


def test_internal_visibility_escalates_without_publication(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    worker.persist(jid, tid, 'Summary', 'Internal draft', ['chunk-1'], False,
                   {'chunk-1': {'id': 'chunk-1', 'visibility': 'internal', 'source': 'internal_policy.pdf'}})
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert not any(m['role'] == 'assistant' and m['visible'] for m in messages)


def test_mixed_valid_and_invalid_evidence_escalates(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    worker.persist(jid, tid, 'Summary', 'Mixed evidence draft',
                   ['public', 'private'], False, {
                       'public': {'id': 'public', 'visibility': 'customer', 'source': 'customer_policy.pdf'},
                       'private': {'id': 'private', 'visibility': 'internal', 'source': 'internal_policy.pdf'},
                   })
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert not any(m['role'] == 'assistant' and m['visible'] for m in messages)
