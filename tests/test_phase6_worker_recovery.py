"""Phase 6: deterministic worker recovery, lease, and idempotency regression.

Run from repository root: python -m pytest tests/test_phase6_worker_recovery.py -v
No live services, OpenAI calls, or modifications to the developer database.
"""
import concurrent.futures
import time
import uuid

import pytest
from backend.db import store
from backend.worker import main as worker


@pytest.fixture(autouse=True)
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


def test_recovery_preserves_unexpired_lease():
    tid, jid = create_job()
    row = worker.claim()
    worker.recover()
    ticket, job, _ = read_state(tid, jid)
    assert ticket["status"] == job["status"] == "PROCESSING"
    assert job["lease_token"] == row["lease"]
    assert job["attempts"] == 1


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


def test_failed_agent_run_escalates_and_never_publishes_saved_draft():
    tid, jid = create_job()
    row = worker.claim()
    save_evidence(row)
    worker.finish(row, False)
    ticket, job, messages = read_state(tid, jid)
    assert ticket["status"] == "ESCALATED"
    assert job["status"] == "FAILED"
    assert not any(m["role"] == "assistant" for m in messages)


def test_no_persisted_agent_result_escalates():
    tid, jid = create_job()
    row = worker.claim()
    worker.finish(row, True)
    ticket, job, messages = read_state(tid, jid)
    assert ticket["status"] == "ESCALATED"
    assert job["status"] == "FAILED"
    assert not any(m["role"] == "assistant" for m in messages)


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


def test_concurrent_claims_never_claim_same_job_twice():
    tid, jid = create_job()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(4)))
    non_null = [x for x in claims if x is not None]
    assert len(non_null) == 1
    assert non_null[0]["id"] == jid
    assert read_state(tid, jid)[1]["attempts"] == 1


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
