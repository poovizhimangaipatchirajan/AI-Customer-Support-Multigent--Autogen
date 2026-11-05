"""Consolidated worker tests.

Merged without behaviour changes from:
- test_worker.py: regression tests
- test_phase3_worker_rag.py: FixFlow Phase 3: offline worker/RAG safety and reliability tests.
- test_phase6_worker_recovery.py: Phase 6: deterministic worker recovery, lease, and idempotency regression.
- test_rc1_lease_boundary.py: RC1 lease-bound persistence regression; isolated SQLite only.
- test_review_response_patch.py: Lightweight, dependency-free structural regression checks for review routing.
- test_universal_response_publication_contract.py: Regression contracts for universal response routing and safeguards.
- test_followup_context.py: regression tests
"""
import pytest
from backend.db.store import init, connect
from backend.worker.main import recover, claim
import json
import time
import uuid
from types import SimpleNamespace
from backend.db import store
from backend.worker import main as worker
from backend.rag import hybrid
import concurrent.futures
from pathlib import Path
import ast
from backend.services.grounding_guard import check_response_grounding
import sqlite3
from backend.services.followup_context import refund_timing_question, review_context, safe_timing_fallback


# ======================================================================
# From test_worker.py
# ======================================================================

def test_claim_fifo(tmp_path,monkeypatch):
 monkeypatch.setenv('FIXFLOW_DB',str(tmp_path/'test.sqlite3'))
 init();recover()
 with connect() as c:
  c.execute("INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at) VALUES ('t','demo','test','description',NULL,'QUEUED','',1)")
  c.execute("INSERT INTO jobs(id,ticket_id,status,created_at) VALUES ('j','t','QUEUED',1)")
 assert claim()['id']=='j'
 assert claim() is None


# ======================================================================
# From test_phase3_worker_rag.py
# ======================================================================

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


def assert_only_safe_fallback(messages, rejected_draft):
    visible = [m['content'] for m in messages if m['role'] == 'assistant' and m['visible']]
    assert len(visible) == 1
    assert rejected_draft not in visible[0]
    assert 'human review' in visible[0].lower()
    assert 'no refund or replacement has been approved or processed' in visible[0].lower()


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
    draft = (
        "I cannot verify your order's current delivery status from policy documents. "
        "Please provide your order ID so support can check."
    )
    evidence = {
        'chunk-1': {
            'id': 'chunk-1',
            'visibility': 'customer',
            'source': '01_Shipping_and_Delivery_SOP.pdf',
            'page': 1,
            'text': (
                'Order-specific delivery status requires a verified order ID. '
                'Delivery information must be checked against the order record.'
            ),
        }
    }
    assert worker.persist(
        jid, tid, 'Delivery summary', draft, ['chunk-1'], False, evidence
    ) == 'PERSISTED'
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'ANSWERED'
    assert job['status'] == 'DONE'
    assert len([m for m in messages if m['role'] == 'assistant' and m['visible']]) == 1
    worker.finish(row, True)
    _, _, messages_again = state(tid, jid)
    assert len(messages_again) == len(messages)



def test_missing_policy_text_requires_human_review(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    assert worker.persist(
        jid, tid, 'Delivery summary', 'Here is the policy guidance.',
        ['chunk-1'], False,
        {'chunk-1': {
            'id': 'chunk-1',
            'visibility': 'customer',
            'source': 'customer_policy.pdf',
            'page': 1,
            # No text: a citation identifier alone cannot justify publication.
        }},
    ) == 'PERSISTED'
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert job['status'] == 'DONE'
    assert_only_safe_fallback(messages, 'Here is the policy guidance.')
    with store.connect() as c:
        result = c.execute('SELECT requires_review FROM ai_results WHERE job_id=?', (jid,)).fetchone()
    assert result is not None and result['requires_review'] == 1


def test_missing_evidence_stays_private_and_escalates(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    worker.persist(jid, tid, 'Uncertain', 'Unverified response', ['invented'], False, {})
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert job['status'] == 'DONE'
    assert_only_safe_fallback(messages, 'Unverified response')
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
    assert_only_safe_fallback(messages, 'Draft only')


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
    # The current legacy customer search ranks the immutable approved catalog,
    # not arbitrary documents in an old FAISS manifest. Use offline embeddings
    # with the same indexed response shape as the real provider.
    from backend.rag.customer_policy import approved_passages

    approved = approved_passages()
    r = hybrid.HybridRetriever.__new__(hybrid.HybridRetriever)
    r.docs = [
        approved[0],
        {'id': 'internal-1', 'text': 'internal escalation', 'visibility': 'internal'},
        approved[1],
    ]
    r.model = 'test-embedding-model'

    def fake_embeddings_create(*, model, input):
        assert model == r.model
        if isinstance(input, list):
            assert input == [p['text'] for p in approved]
            return SimpleNamespace(data=[SimpleNamespace(
                index=i, embedding=[1.0 if j == i else 0.0
                                    for j in range(len(approved))])
                for i in range(len(approved))])
        assert input == 'shipping'
        return SimpleNamespace(data=[SimpleNamespace(
            index=0, embedding=[1.0] + [0.0] * (len(approved) - 1))])

    r.client = SimpleNamespace(embeddings=SimpleNamespace(
        create=fake_embeddings_create))

    public = r.search('shipping', k=3)
    assert [p['id'] for p in public] == [approved[0]['id']]
    assert all(p['visibility'] == 'customer' for p in public)

    with pytest.raises(PermissionError):
        r.search('shipping', k=3, include_internal=True)

    # A forged raw document must not replace curated catalog evidence.
    r.docs[0] = {'id': 'legacy', 'text': 'shipping delivery',
                 'visibility': 'customer'}
    remaining = r.search('shipping', k=3)
    assert [p['id'] for p in remaining] == [approved[0]['id']]
    assert all(p['id'] != 'legacy' for p in remaining)

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
    assert_only_safe_fallback(messages, 'Unverified draft')


def test_internal_visibility_escalates_without_publication(isolated_db):
    tid, jid = seed_ticket()
    row = worker.claim()
    worker.persist(jid, tid, 'Summary', 'Internal draft', ['chunk-1'], False,
                   {'chunk-1': {'id': 'chunk-1', 'visibility': 'internal', 'source': 'internal_policy.pdf'}})
    worker.finish(row, True)
    ticket, job, messages = state(tid, jid)
    assert ticket['status'] == 'ESCALATED'
    assert_only_safe_fallback(messages, 'Internal draft')


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
    assert_only_safe_fallback(messages, 'Mixed evidence draft')


# ======================================================================
# From test_phase6_worker_recovery.py
# ======================================================================

@pytest.fixture
def temporary_database(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "phase6.sqlite3"))
    store.init()


def create_job(*, status="QUEUED", version=0, attempts=0, risk=""):
    uid, tid, jid = (str(uuid.uuid4()) for _ in range(3))
    now = time.time()
    with store.connect() as c:
        c.execute("INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)",
                  (uid, uid + "@example.test", "test-only", "customer", 1))
        c.execute("INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at,version) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (tid, uid, "Delivery question", "Fictional delivery question", None, status, risk, now, version))
        c.execute("INSERT INTO jobs(id,ticket_id,status,created_at,input_version,attempts) VALUES (?,?,?,?,?,?)",
                  (jid, tid, status, now, version, attempts))
    return tid, jid


def read_state(tid, jid):
    with store.connect() as c:
        ticket = dict(c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
        job = dict(c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())
        messages = [dict(x) for x in c.execute("SELECT * FROM messages WHERE ticket_id=?", (tid,))]
        return ticket, job, messages


def save_evidence(row, draft="Verified fictional policy answer"):
    worker.persist(row["id"], row["ticket_id"], "Policy summary", draft,
                   ["source-1"], False, {"source-1": {"id": "source-1", "visibility": "customer", "source": "01_Shipping_and_Delivery_SOP.pdf", "text": "Verified fictional policy answer"}})


def expire(jid):
    with store.connect() as c:
        c.execute("UPDATE jobs SET heartbeat=? WHERE id=?",
                  (time.time() - worker.LEASE_SECONDS - 60, jid))


@pytest.mark.usefixtures('temporary_database')
def test_recovery_preserves_unexpired_lease():
    tid, jid = create_job()
    row = worker.claim()
    worker.recover()
    ticket, job, _ = read_state(tid, jid)
    assert ticket["status"] == job["status"] == "PROCESSING"
    assert job["lease_token"] == row["lease"]
    assert job["attempts"] == 1


@pytest.mark.usefixtures('temporary_database')
def test_expired_lease_requeues_and_new_claim_uses_new_token():
    tid, jid = create_job()
    old = worker.claim()
    expire(jid)
    worker.recover()
    ticket, job, _ = read_state(tid, jid)
    assert ticket["status"] == job["status"] == "QUEUED"
    assert job["lease_token"] is None
    new = worker.claim()
    assert new["id"] == old["id"]
    assert new["lease"] != old["lease"]
    assert read_state(tid, jid)[1]["attempts"] == 2


@pytest.mark.usefixtures('temporary_database')
def test_old_worker_cannot_publish_after_lease_recovery():
    tid, jid = create_job()
    old = worker.claim()
    expire(jid)
    worker.recover()
    new = worker.claim()
    save_evidence(new)
    worker.finish(old, True)
    ticket, job, messages = read_state(tid, jid)
    assert ticket["status"] == job["status"] == "PROCESSING"
    assert not any(m["role"] == "assistant" for m in messages)
    worker.finish(new, True)
    ticket, job, messages = read_state(tid, jid)
    assert ticket["status"] == "ANSWERED" and job["status"] == "DONE"
    assert len([m for m in messages if m["role"] == "assistant"]) == 1


@pytest.mark.usefixtures('temporary_database')
def test_expired_lease_at_retry_limit_escalates_without_answer():
    tid, jid = create_job(attempts=1)
    worker.claim()
    expire(jid)
    worker.recover()
    ticket, job, messages = read_state(tid, jid)
    assert ticket["status"] == "ESCALATED"
    assert job["status"] == "FAILED"
    assert job["lease_token"] is None
    assert not any(m["role"] == "assistant" for m in messages)
    assert worker.claim() is None


@pytest.mark.usefixtures('temporary_database')
def test_stale_version_recovery_does_not_change_newer_ticket():
    tid, jid = create_job()
    worker.claim()
    with store.connect() as c:
        c.execute("UPDATE tickets SET status='ESCALATED', version=version+1 WHERE id=?", (tid,))
    expire(jid)
    worker.recover()
    ticket, job, _ = read_state(tid, jid)
    assert ticket["status"] == "ESCALATED"
    assert job["status"] == "SUPERSEDED"
    assert worker.claim() is None


@pytest.mark.usefixtures('temporary_database')
def test_failed_agent_run_escalates_and_never_publishes_saved_draft():
    tid, jid = create_job()
    row = worker.claim()
    save_evidence(row)
    worker.finish(row, False)
    ticket, job, messages = read_state(tid, jid)
    assert ticket["status"] == "ESCALATED"
    assert job["status"] == "FAILED"
    assistant = [m["content"] for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert "Verified fictional policy answer" not in assistant[0]
    assert "could not be completed safely" in assistant[0]
    assert "No refund or replacement has been approved or processed." in assistant[0]


@pytest.mark.usefixtures('temporary_database')
def test_no_persisted_agent_result_escalates():
    tid, jid = create_job()
    row = worker.claim()
    worker.finish(row, True)
    ticket, job, messages = read_state(tid, jid)
    assert ticket["status"] == "ESCALATED"
    assert job["status"] == "FAILED"
    assistant = [m["content"] for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert "Verified fictional policy answer" not in assistant[0]
    assert "could not be completed safely" in assistant[0]
    assert "No refund or replacement has been approved or processed." in assistant[0]


@pytest.mark.usefixtures('temporary_database')
def test_duplicate_persist_and_finalize_publish_once():
    tid, jid = create_job()
    row = worker.claim()
    save_evidence(row, "First draft")
    # Exact retries remain idempotent; a different draft must be rejected.
    save_evidence(row, "First draft")
    with pytest.raises(RuntimeError, match="Conflicting AI result for job"):
        save_evidence(row, "Second draft")
    worker.finish(row, True)
    worker.finish(row, True)
    ticket, job, messages = read_state(tid, jid)
    assert ticket["status"] == "ANSWERED"
    assert job["status"] == "DONE"
    assert [m["content"] for m in messages if m["role"] == "assistant"] == ["First draft"]
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM ai_results WHERE job_id=?", (jid,)).fetchone()[0] == 1


@pytest.mark.usefixtures('temporary_database')
def test_concurrent_claims_never_claim_same_job_twice():
    tid, jid = create_job()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(4)))
    non_null = [x for x in claims if x is not None]
    assert len(non_null) == 1
    assert non_null[0]["id"] == jid
    assert read_state(tid, jid)[1]["attempts"] == 1


@pytest.mark.usefixtures('temporary_database')
def test_second_job_waits_for_first_and_then_processes():
    first_tid, first_jid = create_job()
    time.sleep(0.01)
    second_tid, second_jid = create_job()
    first = worker.claim()
    assert first["id"] == first_jid
    assert worker.claim() is None
    save_evidence(first)
    worker.finish(first, True)
    second = worker.claim()
    assert second is not None and second["id"] == second_jid
    assert read_state(first_tid, first_jid)[0]["status"] == "ANSWERED"
    assert read_state(second_tid, second_jid)[0]["status"] == "PROCESSING"


# ======================================================================
# From test_rc1_lease_boundary.py
# ======================================================================

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "rc1.sqlite3"))
    store.init()


def create_job_rc1_lease_boundary():
    uid, tid, jid = [str(uuid.uuid4()) for _ in range(3)]
    now = time.time()
    with store.connect() as c:
        c.execute("INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)",
                  (uid, uid + "@example.test", "test-only", "customer", 1))
        c.execute("INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (tid, uid, "Delivery", "Fictional shipping question", None, "QUEUED", "", now))
        c.execute("INSERT INTO jobs(id,ticket_id,status,created_at,input_version) VALUES (?,?,?,?,?)",
                  (jid, tid, "QUEUED", now, 0))
    return tid, jid


def persist(row, draft):
    evidence = {"source-1": {"id": "source-1", "visibility": "customer", "source": "01_Shipping_and_Delivery_SOP.pdf", "text": draft}}
    return worker.persist(row["id"], row["ticket_id"], "Policy summary", draft,
                          ["source-1"], False, evidence,
                          lease=row["lease"], input_version=row["version"])


@pytest.mark.usefixtures('isolated')
def test_reclaimed_lease_cannot_persist_and_new_attempt_can():
    tid, jid = create_job_rc1_lease_boundary()
    old = worker.claim()
    with store.connect() as c:
        c.execute("UPDATE jobs SET heartbeat=? WHERE id=?",
                  (time.time() - worker.LEASE_SECONDS - 60, jid))
    worker.recover()
    new = worker.claim()
    assert new["lease"] != old["lease"]
    with pytest.raises(RuntimeError, match="Stale or unauthorized"):
        persist(old, "Old draft")
    assert persist(new, "Verified fictional policy answer") == "PERSISTED"
    with store.connect() as c:
        rows = c.execute("SELECT draft FROM ai_results WHERE job_id=?", (jid,)).fetchall()
    assert [r["draft"] for r in rows] == ["Verified fictional policy answer"]


@pytest.mark.usefixtures('isolated')
def test_old_attempt_result_cleared_on_reclaim():
    tid, jid = create_job_rc1_lease_boundary()
    old = worker.claim()
    persist(old, "Old draft")
    with store.connect() as c:
        c.execute("UPDATE jobs SET heartbeat=? WHERE id=?",
                  (time.time() - worker.LEASE_SECONDS - 60, jid))
    worker.recover()
    new = worker.claim()
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM ai_results WHERE job_id=?", (jid,)).fetchone()[0] == 0
    assert persist(new, "New draft") == "PERSISTED"


# ======================================================================
# From test_review_response_patch.py
# ======================================================================

ROOT = Path(__file__).resolve().parents[1]

def test_all_modified_modules_parse():
    for relative in ('backend/main.py','backend/worker/main.py','backend/agents/team.py'):
        ast.parse((ROOT / relative).read_text(encoding='utf-8'))

def test_worker_publishes_review_response():
    text = (ROOT / 'backend/worker/main.py').read_text(encoding='utf-8')
    # Escalated results still publish; a failed run's saved draft never does.
    assert 'if result and success:\n            # Publish the validated guidance' in text
    assert 'if result and not escalate:' not in text
    assert 'Human review is required because the requested refund' in text
    assert 'elif escalate:' in text

def test_initial_risk_review_explained():
    text = (ROOT / 'backend/main.py').read_text(encoding='utf-8')
    assert 'def initial_review_explanation(flags):' not in text  # removed dead helper
    assert "status='AWAITING_CUSTOMER' if clarification else 'QUEUED'" in text
    assert 'No refund or replacement has been' in text

def test_agent_low_value_policy():
    text = (ROOT / 'backend/agents/team.py').read_text(encoding='utf-8')
    assert 'QueryAgent' in text
    assert 'ResolutionAgent' in text


# ======================================================================
# From test_universal_response_publication_contract.py
# ======================================================================

ROOT_universal_response_publication_contract = Path(__file__).resolve().parents[1]

def test_replacement_intake_does_not_block_agents():
    code = (ROOT_universal_response_publication_contract / 'backend/main.py').read_text(encoding='utf-8')
    assert 'clarification = refund_clarification\n' in code
    assert 'clarification = refund_clarification or (replacement_clarification' not in code

def test_replacement_followup_not_blocked_by_attachment():
    code = (ROOT_universal_response_publication_contract / 'backend/main.py').read_text(encoding='utf-8')
    assert 'if awaiting_replacement and not flags:' not in code

def test_empty_policy_cannot_ground_answer():
    assert check_response_grounding('You can return this item.', [{'text': ''}]) == 'MISSING_POLICY_TEXT'

def test_worker_validates_before_publication():
    code = (ROOT_universal_response_publication_contract / 'backend/worker/main.py').read_text(encoding='utf-8')
    assert 'check_response_grounding(' in code
    assert 'customer_draft = result["draft"]' in code
    assert 'if invalid_evidence:' in code
    assert 'AI_EVIDENCE_REJECTED:' in code

def test_quantitative_guard_connected():
    code = (ROOT_universal_response_publication_contract / 'backend/services/grounding_guard.py').read_text(encoding='utf-8')
    assert 'unsupported_quantitative_claim(draft, texts, customer_message=customer_message)' in code

def test_refund_approval_does_not_claim_payment():
    code = (ROOT_universal_response_publication_contract / 'backend/worker/main.py').read_text(encoding='utf-8')
    assert 'The human refund review was approved for further' in code
    assert 'This does not confirm a refund payment.' in code


# ======================================================================
# From test_followup_context.py
# ======================================================================

def test_timing_question():
    assert refund_timing_question("how long the refund process will take generally?")
    assert not refund_timing_question("please refund my order")

def test_review_decision_read_only():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE refund_reviews (ticket_id TEXT, status TEXT, execution_status TEXT)")
    c.execute("INSERT INTO refund_reviews VALUES ('t1','APPROVED','NOT_CONFIGURED')")
    s=review_context(c,'t1')
    assert 'APPROVED' in s and 'NOT_CONFIGURED' in s
    assert 'No refund review decision' in review_context(c,'missing')

def test_timing_fallback_never_invents_days():
    s=safe_timing_fallback('How long will the refund take?', 'Refund review decision: APPROVED.')
    assert 'cannot confirm' in s and 'APPROVED' in s
    assert safe_timing_fallback('Please refund', '') is None
