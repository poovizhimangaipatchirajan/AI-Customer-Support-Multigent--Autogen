"""RC1 lease-bound persistence regression; isolated SQLite only."""
import time
import uuid
import pytest
from backend.db import store
from backend.worker import main as worker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "rc1.sqlite3"))
    store.init()


def create_job():
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


def test_reclaimed_lease_cannot_persist_and_new_attempt_can():
    tid, jid = create_job()
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


def test_old_attempt_result_cleared_on_reclaim():
    tid, jid = create_job()
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
