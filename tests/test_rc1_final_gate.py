"""RC1 fixed release gate: isolated customer, refund, and real process restart tests."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.main import app
from backend.auth.security import hash_password, create_session


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


def ticket(client, auth):
    response = client.post('/tickets', headers=auth, json={
        'subject': 'Order packaging question',
        'description': 'The packaging arrived with a damaged corner.'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_customer_isolation_read_write_and_listing(setup):
    client, _ = setup
    alice, bob = identity(), identity()
    tid = ticket(client, alice)
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
    tid = ticket(client, owner)
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
        assert current['status'] == 'ESCALATED'


def test_approval_survives_actual_new_python_process(setup):
    client, db = setup
    owner = identity()
    tid = ticket(client, owner)
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
