"""Offline diagnostic regression; no live API calls or production DB."""
import pytest
from backend.db import store
from backend.worker import main as worker
import time
import uuid

def seed_ticket():
    uid, tid, jid = (str(uuid.uuid4()) for _ in range(3))
    now = time.time()
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid, f'{uid}@example.test', 'test-hash', 'customer', 1))
        c.execute('INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at,version) VALUES (?,?,?,?,?,?,?,?,?)',
                  (tid, uid, 'Shipping policy', 'Shipping question', None, 'QUEUED', '', now, 0))
        c.execute('INSERT INTO jobs(id,ticket_id,status,created_at,input_version,attempts) VALUES (?,?,?,?,?,?)',
                  (jid, tid, 'QUEUED', now, 0, 0))
    return tid, jid

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'diagnostics.sqlite3'))
    store.init()

@pytest.mark.parametrize('ids,evidence,expected', [
    ([], {}, 'MISSING_CITATIONS'),
    (['fake'], {}, 'INVALID_CITATIONS'),
    (['x'], {'x': {'id': 'x', 'source': '06_Escalations_and_Soft_Skills_SOP.pdf', 'visibility': 'internal'}}, 'NON_CUSTOMER_EVIDENCE'),
])
def test_rejection_reason_codes(isolated_db, capsys, ids, evidence, expected):
    tid, jid = seed_ticket()
    row = worker.claim()
    worker.persist(jid, tid, 'summary', 'A safe generic reply.', ids, False, evidence)
    assert expected in capsys.readouterr().out
    with store.connect() as c:
        result = c.execute('SELECT citations, requires_review FROM ai_results WHERE job_id=?', (jid,)).fetchone()
    assert result['citations'] == '[]'
    assert result['requires_review'] == 1

def test_valid_evidence_keeps_citations(isolated_db, capsys):
    tid, jid = seed_ticket()
    worker.claim()
    evidence = {'x': {'id': 'x', 'source': '01_Shipping_and_Delivery_SOP.pdf', 'visibility': 'customer', 'text': 'The customer can contact support about delivery.'}}
    worker.persist(jid, tid, 'summary', 'The customer can contact support about delivery.', ['x'], False, evidence)
    assert 'EVIDENCE_VALIDATION: ACCEPTED' in capsys.readouterr().out
    with store.connect() as c:
        result = c.execute('SELECT citations, requires_review FROM ai_results WHERE job_id=?', (jid,)).fetchone()
    assert result['citations'] == '["x"]'
    assert result['requires_review'] == 0

def test_sensitive_draft_logs_reason_and_fails_closed(isolated_db, capsys):
    tid, jid = seed_ticket()
    worker.claim()
    evidence = {'x': {'id': 'x', 'source': '01_Shipping_and_Delivery_SOP.pdf', 'visibility': 'customer', 'text': 'Shipping support available.'}}
    worker.persist(jid, tid, 'summary', 'We conduct driver GPS verification.', ['x'], False, evidence)
    assert 'SENSITIVE_OPERATIONAL_DETAIL' in capsys.readouterr().out
    with store.connect() as c:
        result = c.execute('SELECT citations, requires_review FROM ai_results WHERE job_id=?', (jid,)).fetchone()
    assert result['citations'] == '[]'
    assert result['requires_review'] == 1
