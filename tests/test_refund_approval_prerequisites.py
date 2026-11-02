import uuid
import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.main import app
from backend.auth.security import hash_password, create_session

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',str(tmp_path/'enforce.sqlite3'))
    with TestClient(app) as client: yield client

def user(role):
    uid=str(uuid.uuid4())
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid,uid+'@example.test',hash_password('Testing123!Secure'),role,1))
    return uid,{'Authorization':'Bearer '+create_session(uid)}

def create_review(env):
    cid,customer=user('customer'); aid,admin=user('admin')
    r=env.post('/tickets',headers=customer,json={'subject':'Defective item','description':'I request a refund for a broken product','claimed_amount':500})
    assert r.status_code==201,r.text
    return r.json()['id'],cid,admin

def test_approval_requires_owned_order_and_assessment(env):
    tid,cid,admin=create_review(env)
    url=f'/admin/refund-reviews/{tid}'
    decision={'decision':'APPROVED','reason':'Checked by human reviewer','version':0}
    assert env.post(url+'/decision',headers=admin,json=decision).status_code==409
    with store.connect() as c:
        c.execute('INSERT INTO demo_orders(id,order_code,customer_id,placed_at,status,total_amount) VALUES (?,?,?,?,?,?)',
                  (str(uuid.uuid4()),'TEST-ORDER',cid,'2026-11-02','delivered',500))
        c.execute('INSERT INTO ticket_order_links(ticket_id,order_code,customer_id) VALUES (?,?,?)',(tid,'TEST-ORDER',cid))
    assert env.post(url+'/decision',headers=admin,json=decision).status_code==409
    a=env.post(url+'/assessment',headers=admin,json={'claim_type':'DAMAGED_DEFECTIVE','evidence_status':'ACCEPTED','evidence_ids':[], 'reason':'Reviewed submitted files','version':0})
    assert a.status_code==422,a.text
    a=env.post(url+'/assessment',headers=admin,json={'claim_type':'OTHER','evidence_status':'NOT_REQUIRED','reason':'Non-damage claim assessed by human','version':0})
    assert a.status_code==422,a.text
    approved=env.post(url+'/decision',headers=admin,json=decision)
    assert approved.status_code==409,approved.text

def test_rejection_never_requires_evidence(env):
    tid,_,admin=create_review(env)
    r=env.post(f'/admin/refund-reviews/{tid}/decision',headers=admin,json={'decision':'REJECTED','reason':'No order or evidence','version':0})
    assert r.status_code==200,r.text


def test_other_claim_can_be_assessed_when_not_damage(env):
    cid, customer = user('customer')
    _, admin = user('admin')
    response = env.post('/tickets', headers=customer, json={
        'subject': 'Shipping delay', 'description': 'Please refund me for delayed shipping',
        'claimed_amount': 500})
    assert response.status_code == 201, response.text
    tid = response.json()['id']
    with store.connect() as c:
        c.execute('INSERT INTO demo_orders(id,order_code,customer_id,placed_at,status,total_amount) VALUES (?,?,?,?,?,?)',
                  (str(uuid.uuid4()), 'TEST-SHIP-' + uuid.uuid4().hex[:8], cid, '2026-11-02', 'delivered', 500))
        order_code = c.execute('SELECT order_code FROM demo_orders WHERE customer_id=?', (cid,)).fetchone()['order_code']
        c.execute('INSERT INTO ticket_order_links(ticket_id,order_code,customer_id) VALUES (?,?,?)', (tid, order_code, cid))
    url = f'/admin/refund-reviews/{tid}'
    assessment = env.post(url+'/assessment', headers=admin, json={
        'claim_type': 'OTHER', 'evidence_status': 'NOT_REQUIRED',
        'reason': 'Reviewed as shipping delay', 'version': 0})
    assert assessment.status_code == 200, assessment.text
    decision = env.post(url+'/decision', headers=admin, json={
        'decision': 'APPROVED', 'reason': 'Human decision only', 'version': 0})
    assert decision.status_code == 200, decision.text
    assert decision.json()['execution_status'] == 'NOT_CONFIGURED'
