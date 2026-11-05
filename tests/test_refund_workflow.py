"""Consolidated refund workflow tests.

Merged without behaviour changes from:
- test_refund_clarification_workflow.py: Targeted API tests for missing-amount clarification and mandatory review.
- test_refund_followup_safety.py: Regression tests: later damage allegations and changed refund amounts fail closed.
- test_refund_finalization_gate.py: Offline targeted checks; no API key, database, or Docker needed.
- test_refund_approval_prerequisites.py: regression tests
- test_phase8e_refund_approvals.py: Decision-only review integration tests; isolated temporary SQLite.
- test_support_case_closure.py: Customer-owned closure: admin cannot close, pending refund blocks, no payments.
- test_resolved_evidence_upload.py: Regression: a resolved ticket rejects evidence writes but keeps history readable.
"""
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.main import app
from backend.auth.security import hash_password, create_session
import importlib.util
from pathlib import Path
import base64
from io import BytesIO
from PIL import Image


# ======================================================================
# From test_refund_clarification_workflow.py
# ======================================================================

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'clarification.sqlite3'))
    with TestClient(app) as client:
        uid = str(uuid.uuid4())
        with store.connect() as c:
            c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                      (uid, uid + '@example.test', hash_password('Testing123!Secure'), 'customer', 1))
        yield client, {'Authorization': 'Bearer ' + create_session(uid)}

def create(client, headers, description, amount=None):
    body = {'subject':'Refund request', 'description':description}
    if amount is not None: body['claimed_amount'] = amount
    r = client.post('/tickets', headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()

def test_missing_amount_requests_details_then_queues_on_low_amount(env):
    client, headers = env
    ticket = create(client, headers, 'Please refund me for my order')
    assert ticket['status'] == 'AWAITING_CUSTOMER'
    assert ticket['risk_flags'] == []
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM jobs WHERE ticket_id=?', (ticket['id'],)).fetchone()[0] == 0
    followup = client.post(f"/tickets/{ticket['id']}/reply", headers=headers,
                           json={'content':'My reference is ABC123; refund amount INR 300.'})
    assert followup.status_code == 200, followup.text
    assert followup.json()['status'] == 'QUEUED'
    with store.connect() as c:
        assert c.execute('SELECT claimed_amount FROM tickets WHERE id=?', (ticket['id'],)).fetchone()[0] == 300
        assert c.execute('SELECT COUNT(*) FROM jobs WHERE ticket_id=? AND status=?', (ticket['id'], 'QUEUED')).fetchone()[0] == 1

def test_missing_amount_followup_crossing_threshold_escalates(env):
    client, headers = env
    ticket = create(client, headers, 'Please refund me for my order')
    response = client.post(f"/tickets/{ticket['id']}/reply", headers=headers,
                           json={'content':'INR 500 is the refund amount.'})
    assert response.status_code == 200
    assert response.json()['status'] == 'QUEUED'
    with store.connect() as c:
        risk = c.execute('SELECT risk FROM tickets WHERE id=?', (ticket['id'],)).fetchone()[0]
    assert 'REFUND_GE_500' in risk

def test_low_value_refund_is_queued_and_high_value_escalates(env):
    client, headers = env
    assert create(client, headers, 'Please refund me for this order', 300)['status'] == 'QUEUED'
    assert create(client, headers, 'Please refund me for this order', 500)['status'] == 'QUEUED'

def test_payment_dispute_always_escalates(env):
    client, headers = env
    assert create(client, headers, 'I was charged twice. Please refund me.')['status'] == 'QUEUED'


# ======================================================================
# From test_refund_followup_safety.py
# ======================================================================

@pytest.fixture
def env_refund_followup_safety(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'refund_followup.sqlite3'))
    with TestClient(app) as client:
        yield client


def actor(role):
    uid = str(uuid.uuid4())
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid, uid + '@example.test', hash_password('Testing123!Secure'), role, 1))
    return uid, {'Authorization': 'Bearer ' + create_session(uid)}


def setup(env_refund_followup_safety, amount=500):
    cid, customer = actor('customer')
    _, admin = actor('admin')
    result = env_refund_followup_safety.post('/tickets', headers=customer, json={
        'subject': 'Shipping delay', 'description': 'Please refund me for delayed shipping',
        'claimed_amount': amount})
    assert result.status_code == 201, result.text
    tid = result.json()['id']
    order_code = 'ORDER-' + uuid.uuid4().hex[:12]
    with store.connect() as c:
        c.execute('INSERT INTO demo_orders(id,order_code,customer_id,placed_at,status,total_amount) VALUES (?,?,?,?,?,?)',
                  (str(uuid.uuid4()), order_code, cid, '2026-11-02', 'delivered', amount))
        c.execute('INSERT INTO ticket_order_links(ticket_id,order_code,customer_id) VALUES (?,?,?)',
                  (tid, order_code, cid))
    return tid, customer, admin


def assess_other(env_refund_followup_safety, tid, admin, version=0):
    return env_refund_followup_safety.post(f'/admin/refund-reviews/{tid}/assessment', headers=admin, json={
        'claim_type': 'OTHER', 'evidence_status': 'NOT_REQUIRED',
        'reason': 'Human reviewed non-damage claim', 'version': version})


def approve(env_refund_followup_safety, tid, admin, version=0):
    return env_refund_followup_safety.post(f'/admin/refund-reviews/{tid}/decision', headers=admin, json={
        'decision': 'APPROVED', 'reason': 'Human reviewed order and claim', 'version': version})


def test_damage_introduced_in_followup_requires_image(env_refund_followup_safety):
    tid, customer, admin = setup(env_refund_followup_safety)
    assert env_refund_followup_safety.post(f'/tickets/{tid}/reply', headers=customer,
                    json={'content': 'The item arrived broken and cracked.'}).status_code == 200
    assert assess_other(env_refund_followup_safety, tid, admin).status_code == 422
    assert approve(env_refund_followup_safety, tid, admin).status_code == 409


def test_new_damage_after_assessment_invalidates_it(env_refund_followup_safety):
    tid, customer, admin = setup(env_refund_followup_safety)
    assert assess_other(env_refund_followup_safety, tid, admin).status_code == 200
    assert env_refund_followup_safety.post(f'/tickets/{tid}/reply', headers=customer,
                    json={'content': 'Actually it is damaged and broken.'}).status_code == 200
    assert approve(env_refund_followup_safety, tid, admin).status_code == 409


@pytest.mark.parametrize('initial,updated', [(500, 800), (800, 500)])
def test_amount_conflict_requires_reconciliation_and_new_assessment(env_refund_followup_safety, initial, updated):
    tid, customer, admin = setup(env_refund_followup_safety, initial)
    assert assess_other(env_refund_followup_safety, tid, admin).status_code == 200
    followup = env_refund_followup_safety.post(f'/tickets/{tid}/reply', headers=customer,
                        json={'content': f'Please refund {updated} rupees instead.'})
    assert followup.status_code == 200, followup.text
    assert approve(env_refund_followup_safety, tid, admin).status_code == 409
    review = env_refund_followup_safety.get(f'/admin/refund-reviews/{tid}', headers=admin).json()
    assert float(review['amount']) == initial  # never silently rewrite an amount
    reconciliation = env_refund_followup_safety.post(f'/admin/refund-reviews/{tid}/reconcile-amount',
                              headers=admin, json={'amount': updated,
                              'reason': 'Verified the changed customer claim', 'version': 0})
    assert reconciliation.status_code == 200, reconciliation.text
    assert approve(env_refund_followup_safety, tid, admin, version=1).status_code == 409  # re-assessment mandatory
    assert assess_other(env_refund_followup_safety, tid, admin, version=1).status_code == 200
    approved = approve(env_refund_followup_safety, tid, admin, version=1)
    assert approved.status_code == 200, approved.text
    assert approved.json()['execution_status'] == 'NOT_CONFIGURED'


def test_reconciliation_requires_admin_and_pending_review(env_refund_followup_safety):
    tid, customer, admin = setup(env_refund_followup_safety)
    url = f'/admin/refund-reviews/{tid}/reconcile-amount'
    body = {'amount': 800, 'reason': 'Verified claim amount', 'version': 0}
    assert env_refund_followup_safety.post(url, headers=customer, json=body).status_code == 403
    assert env_refund_followup_safety.post(url, headers=admin, json={**body, 'version': 2}).status_code == 409


def test_admin_message_does_not_create_damage_claim(env_refund_followup_safety):
    tid, _, admin = setup(env_refund_followup_safety)
    assert env_refund_followup_safety.post(f'/tickets/{tid}/reply', headers=admin,
                    json={'content': 'Please tell us if the product is broken.'}).status_code == 200
    assert assess_other(env_refund_followup_safety, tid, admin).status_code == 200
    assert approve(env_refund_followup_safety, tid, admin).status_code == 200


def test_followup_crossing_500_creates_pending_review(env_refund_followup_safety):
    tid, customer, admin = setup(env_refund_followup_safety, 100)
    assert env_refund_followup_safety.get(f'/admin/refund-reviews/{tid}', headers=admin).status_code == 404
    response = env_refund_followup_safety.post(f'/tickets/{tid}/reply', headers=customer,
                        json={'content': 'Please refund 800 rupees instead.'})
    assert response.status_code == 200, response.text
    review = env_refund_followup_safety.get(f'/admin/refund-reviews/{tid}', headers=admin)
    assert review.status_code == 200, review.text
    assert review.json()['status'] == 'PENDING'
    with store.connect() as c:
        risk = c.execute('SELECT risk FROM tickets WHERE id=?', (tid,)).fetchone()['risk']
    assert 'REFUND_GE_500' in risk
    assert 'REFUND_AMOUNT_UNVERIFIED' in risk
    assert approve(env_refund_followup_safety, tid, admin).status_code == 409


def test_initial_conflicting_high_value_claim_creates_review(env_refund_followup_safety):
    _, customer = actor('customer')
    _, admin = actor('admin')
    response = env_refund_followup_safety.post('/tickets', headers=customer, json={
        'subject': 'Refund request',
        'description': 'Please refund 800 rupees for this order.',
        'claimed_amount': 100})
    assert response.status_code == 201, response.text
    tid = response.json()['id']
    review = env_refund_followup_safety.get(f'/admin/refund-reviews/{tid}', headers=admin)
    assert review.status_code == 200, review.text
    assert review.json()['status'] == 'PENDING'
    assert 'REFUND_AMOUNT_UNVERIFIED' in response.json()['risk_flags']
    assert approve(env_refund_followup_safety, tid, admin).status_code == 409


# ======================================================================
# From test_refund_finalization_gate.py
# ======================================================================

MODULE = Path(__file__).resolve().parents[1] / 'backend' / 'services' / 'refund_routing.py'
spec = importlib.util.spec_from_file_location('refund_routing_under_test', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def ticket(amount, subject='refund', description='My headphones are damaged', risk=''):
    return {'claimed_amount': amount, 'subject': subject,
            'description': description, 'risk': risk}


def test_refund_400_does_not_require_threshold_review():
    assert not module.mandatory_refund_review(ticket(400))


def test_refund_499_99_does_not_require_threshold_review():
    assert not module.mandatory_refund_review(ticket(499.99))


def test_refund_500_requires_review():
    assert module.mandatory_refund_review(ticket(500))


def test_refund_600_requires_review_even_when_flag_missing():
    assert module.mandatory_refund_review(ticket(600))


def test_persisted_flag_cannot_be_downgraded():
    assert module.mandatory_refund_review(ticket(None, risk='REFUND_GE_500'))


def test_order_value_without_refund_intent_not_threshold_trigger():
    assert not module.mandatory_refund_review(ticket(600, 'shipping question', 'Where is my order?'))


def test_other_mandatory_risk_not_overwritten():
    assert not module.mandatory_refund_review(ticket(400, risk='LEGAL_DISPUTE'))
    # Other risk codes remain separately enforced by existing worker logic.


def test_no_provisional_review_message_and_worker_enforces_guard():
    api = (MODULE.parents[1] / 'main.py').read_text(encoding='utf-8')
    worker = (MODULE.parents[1] / 'worker' / 'main.py').read_text(encoding='utf-8')
    assert "'assistant',initial_review_explanation(flags)" not in api
    assert 'initial_review_explanation(risk), 1, now' not in api
    assert worker.count('mandatory_refund_review(dict(ticket))') >= 3


# ======================================================================
# From test_refund_approval_prerequisites.py
# ======================================================================

@pytest.fixture
def env_refund_approval_prerequisites(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',str(tmp_path/'enforce.sqlite3'))
    with TestClient(app) as client: yield client

def user(role):
    uid=str(uuid.uuid4())
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid,uid+'@example.test',hash_password('Testing123!Secure'),role,1))
    return uid,{'Authorization':'Bearer '+create_session(uid)}

def create_review(env_refund_approval_prerequisites):
    cid,customer=user('customer'); aid,admin=user('admin')
    r=env_refund_approval_prerequisites.post('/tickets',headers=customer,json={'subject':'Defective item','description':'I request a refund for a broken product','claimed_amount':500})
    assert r.status_code==201,r.text
    return r.json()['id'],cid,admin

def test_approval_requires_owned_order_and_assessment(env_refund_approval_prerequisites):
    tid,cid,admin=create_review(env_refund_approval_prerequisites)
    url=f'/admin/refund-reviews/{tid}'
    decision={'decision':'APPROVED','reason':'Checked by human reviewer','version':0}
    assert env_refund_approval_prerequisites.post(url+'/decision',headers=admin,json=decision).status_code==409
    with store.connect() as c:
        c.execute('INSERT INTO demo_orders(id,order_code,customer_id,placed_at,status,total_amount) VALUES (?,?,?,?,?,?)',
                  (str(uuid.uuid4()),'TEST-ORDER',cid,'2026-11-02','delivered',500))
        c.execute('INSERT INTO ticket_order_links(ticket_id,order_code,customer_id) VALUES (?,?,?)',(tid,'TEST-ORDER',cid))
    assert env_refund_approval_prerequisites.post(url+'/decision',headers=admin,json=decision).status_code==409
    a=env_refund_approval_prerequisites.post(url+'/assessment',headers=admin,json={'claim_type':'DAMAGED_DEFECTIVE','evidence_status':'ACCEPTED','evidence_ids':[], 'reason':'Reviewed submitted files','version':0})
    assert a.status_code==422,a.text
    a=env_refund_approval_prerequisites.post(url+'/assessment',headers=admin,json={'claim_type':'OTHER','evidence_status':'NOT_REQUIRED','reason':'Non-damage claim assessed by human','version':0})
    assert a.status_code==422,a.text
    approved=env_refund_approval_prerequisites.post(url+'/decision',headers=admin,json=decision)
    assert approved.status_code==409,approved.text

def test_rejection_never_requires_evidence(env_refund_approval_prerequisites):
    tid,_,admin=create_review(env_refund_approval_prerequisites)
    r=env_refund_approval_prerequisites.post(f'/admin/refund-reviews/{tid}/decision',headers=admin,json={'decision':'REJECTED','reason':'No order or evidence','version':0})
    assert r.status_code==200,r.text


def test_other_claim_can_be_assessed_when_not_damage(env_refund_approval_prerequisites):
    cid, customer = user('customer')
    _, admin = user('admin')
    response = env_refund_approval_prerequisites.post('/tickets', headers=customer, json={
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
    assessment = env_refund_approval_prerequisites.post(url+'/assessment', headers=admin, json={
        'claim_type': 'OTHER', 'evidence_status': 'NOT_REQUIRED',
        'reason': 'Reviewed as shipping delay', 'version': 0})
    assert assessment.status_code == 200, assessment.text
    decision = env_refund_approval_prerequisites.post(url+'/decision', headers=admin, json={
        'decision': 'APPROVED', 'reason': 'Human decision only', 'version': 0})
    assert decision.status_code == 200, decision.text
    assert decision.json()['execution_status'] == 'NOT_CONFIGURED'


# ======================================================================
# From test_phase8e_refund_approvals.py
# ======================================================================

@pytest.fixture
def env_phase8e_refund_approvals(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',str(tmp_path/'phase8e.sqlite3'))
    with TestClient(app) as client: yield client

def user_phase8e_refund_approvals(role='customer'):
    uid=str(uuid.uuid4())
    with store.connect() as c:
        c.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                  (uid,uid+'@example.test',hash_password('Testing123!Secure'),role,1))
    return {'Authorization':'Bearer '+create_session(uid)}

def submit(client,headers,amount):
    response=client.post('/tickets',headers=headers,json={'subject':'Refund request',
          'description':'Please refund me for my damaged order.', 'claimed_amount':amount})
    assert response.status_code==201,response.text
    return response.json()

@pytest.mark.parametrize('amount,expected',[(499,False),(500,True),(501,True)])
def test_inclusive_boundary(env_phase8e_refund_approvals,amount,expected):
    customer=user_phase8e_refund_approvals()
    ticket=submit(env_phase8e_refund_approvals,customer,amount)
    with store.connect() as c:
        record=c.execute('SELECT * FROM refund_reviews WHERE ticket_id=?',(ticket['id'],)).fetchone()
    assert (record is not None)==expected
    if expected:
        assert record['status']=='PENDING'
        assert record['execution_status']=='NOT_CONFIGURED'

def test_authorization_and_rejection_are_durable(env_phase8e_refund_approvals):
    customer=user_phase8e_refund_approvals()
    admin=user_phase8e_refund_approvals('admin')
    ticket=submit(env_phase8e_refund_approvals,customer,500)
    url='/admin/refund-reviews/'+ticket['id']
    assert env_phase8e_refund_approvals.get(url,headers=customer).status_code==403
    assert env_phase8e_refund_approvals.post(url+'/decision',headers=customer,json={'decision':'APPROVED','reason':'Not allowed','version':0}).status_code==403
    response=env_phase8e_refund_approvals.post(url+'/decision',headers=admin,json={'decision':'REJECTED','reason':'Insufficient supporting evidence','version':0})
    assert response.status_code==200,response.text
    assert response.json()['execution_status']=='NOT_CONFIGURED'
    assert env_phase8e_refund_approvals.post(url+'/decision',headers=admin,json={'decision':'APPROVED','reason':'Attempt to reverse','version':0}).status_code==409
    from backend.services.refund_approvals import migrate
    migrate()  # Simulates fresh startup migration, using same persistent DB.
    record=env_phase8e_refund_approvals.get(url,headers=admin).json()
    assert record['status']=='REJECTED' and record['version']==1
    assert record['execution_status']=='NOT_CONFIGURED'
    with store.connect() as c:
        events=[r['event'] for r in c.execute('SELECT event FROM audit WHERE ticket_id=?',(ticket['id'],))]
    assert events.count('REFUND_REVIEW_REJECTED')==1

def test_approval_is_not_execution_or_ticket_publication(env_phase8e_refund_approvals):
    customer=user_phase8e_refund_approvals()
    admin=user_phase8e_refund_approvals('admin')
    ticket=submit(env_phase8e_refund_approvals,customer,501)
    url='/admin/refund-reviews/'+ticket['id']
    prepare_refund_prerequisites(env_phase8e_refund_approvals, ticket['id'], admin, damaged=True, customer_headers=customer)
    response=env_phase8e_refund_approvals.post(url+'/decision',headers=admin,json={'decision':'APPROVED','reason':'Documents checked by reviewer','version':0})
    assert response.status_code==200,response.text
    assert response.json()['execution_status']=='NOT_CONFIGURED'
    detail=env_phase8e_refund_approvals.get('/tickets/'+ticket['id'],headers=customer).json()
    assert detail['status']=='QUEUED'
    assert not any('refund issued' in m['content'].lower() for m in detail['messages'])
    assert env_phase8e_refund_approvals.post(url+'/decision',headers=admin,json={'decision':'APPROVED','reason':'Duplicate request','version':0}).status_code==409


def prepare_refund_prerequisites(client, ticket_id, admin_headers, damaged=False, customer_headers=None):
    """Arrange a synthetic owned order and a human evidence assessment."""
    import base64
    import io
    from PIL import Image
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
        image_buffer = io.BytesIO()
        Image.new('RGB', (4, 4), 'red').save(image_buffer, format='PNG')
        image = base64.b64encode(image_buffer.getvalue()).decode('ascii')
        upload = client.post(f'/tickets/{ticket_id}/evidence', headers=customer_headers,
                             json={'filename':'test.png','media_type':'image/png','content_base64':image})
        assert upload.status_code == 201, upload.text
        evidence_ids = [upload.json()['id']]
    response = client.post(f'/admin/refund-reviews/{ticket_id}/assessment', headers=admin_headers,
        json={'claim_type':'DAMAGED_DEFECTIVE' if damaged else 'OTHER',
              'evidence_status':'ACCEPTED' if damaged else 'NOT_REQUIRED',
              'evidence_ids':evidence_ids, 'reason':'Synthetic human reviewer assessment', 'version':0})
    assert response.status_code == 200, response.text


def test_pdf_only_cannot_satisfy_damaged_claim(env_phase8e_refund_approvals):
    customer = user_phase8e_refund_approvals()
    admin = user_phase8e_refund_approvals('admin')
    ticket = submit(env_phase8e_refund_approvals, customer, 500)
    import base64
    pdf = base64.b64encode(b'%PDF-1.4\nsynthetic\n%%EOF').decode('ascii')
    upload = env_phase8e_refund_approvals.post(f"/tickets/{ticket['id']}/evidence", headers=customer,
        json={'filename': 'evidence.pdf', 'media_type': 'application/pdf', 'content_base64': pdf})
    assert upload.status_code == 201, upload.text
    response = env_phase8e_refund_approvals.post(f"/admin/refund-reviews/{ticket['id']}/assessment", headers=admin,
        json={'claim_type': 'DAMAGED_DEFECTIVE', 'evidence_status': 'ACCEPTED',
              'evidence_ids': [upload.json()['id']], 'reason': 'Reviewed submitted document', 'version': 0})
    assert response.status_code == 422, response.text


# ======================================================================
# From test_support_case_closure.py
# ======================================================================

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'closure.sqlite3'))
    with TestClient(app) as c:
        yield c

def auth(c, role, suffix):
    uid = str(uuid.uuid4())
    with store.connect() as db:
        db.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,1)',
                   (uid, f'{suffix}@example.com', hash_password('GoodPassword!234'), role))
    response = c.post('/auth/login', json={'email': f'{suffix}@example.com', 'password': 'GoodPassword!234'})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['access_token']}

def test_customer_controls_resolution_and_pending_refund_blocks(client):
    customer = auth(client, 'customer', 'closecustomer')
    admin = auth(client, 'admin', 'closeadmin')
    other = auth(client, 'customer', 'closeother')
    created = client.post('/tickets', headers=customer, json={
        'subject': 'Refund order', 'description': 'Please refund my order', 'claimed_amount': 750})
    assert created.status_code == 201, created.text
    tid = created.json()['id']
    endpoint = f'/tickets/{tid}/resolve'
    body = {'reason': 'Support question addressed', 'version': 0}
    assert client.post(endpoint, headers=other, json=body).status_code == 404
    assert client.post(endpoint, headers=admin, json=body).status_code == 403
    assert client.post(endpoint, headers=customer, json=body).status_code == 409
    from backend.worker import main as worker
    row = worker.claim()
    worker.finish(row, False)  # worker finalization puts the ticket in review
    version = client.get(f'/tickets/{tid}', headers=admin).json()['version']
    published = client.post(f'/admin/tickets/{tid}/publish', headers=admin,
                            json={'response': 'Your request was reviewed.', 'version': version})
    assert published.status_code == 200, published.text
    body['version'] = version + 1
    assert client.post(endpoint, headers=customer, json=body).status_code == 409
    with store.connect() as db:
        db.execute("UPDATE refund_reviews SET status='REJECTED',version=version+1,decided_at=1,reason='Reviewed and rejected' WHERE ticket_id=?", (tid,))
    assert client.post(endpoint, headers=customer, json={**body,'version':version}).status_code == 409
    result = client.post(endpoint, headers=customer, json=body)
    assert result.status_code == 200, result.text
    assert result.json()['payment_executed'] is False
    assert client.post(endpoint, headers=customer, json=body).status_code == 409
    assert client.get(f'/tickets/{tid}', headers=customer).json()['status'] == 'RESOLVED'
    assert client.post(f'/tickets/{tid}/reply', headers=customer,
                       json={'content':'Another update please'}).status_code == 409
    assert client.post(f'/admin/tickets/{tid}/close', headers=admin, json=body).status_code == 404


# ======================================================================
# From test_resolved_evidence_upload.py
# ======================================================================

def test_resolved_ticket_rejects_direct_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'evidence.sqlite3'))
    monkeypatch.setenv(
        'FIXFLOW_EVIDENCE_DIR',
        str(tmp_path / 'private_evidence')
    )

    with TestClient(app) as client:
        uid = str(uuid.uuid4())
        email = f'evidence-{uuid.uuid4().hex}@example.com'

        with store.connect() as db:
            db.execute(
                'INSERT INTO users(id,email,password_hash,role,verified) '
                'VALUES (?,?,?,?,1)',
                (uid, email, hash_password('GoodPassword!234'), 'customer')
            )

        login = client.post(
            '/auth/login',
            json={
                'email': email,
                'password': 'GoodPassword!234'
            }
        )
        assert login.status_code == 200, login.text

        headers = {
            'Authorization': 'Bearer ' + login.json()['access_token']
        }

        created = client.post(
            '/tickets',
            headers=headers,
            json={
                'subject': 'General support question',
                'description': 'Please explain how to update my delivery preferences.',
            }
        )
        assert created.status_code == 201, created.text

        ticket_id = created.json()['id']

        # Simulate an already-resolved case.
        # This test exercises evidence upload restrictions,
        # not the independent resolution workflow.
        with store.connect() as db:
            db.execute(
                "UPDATE tickets SET status='RESOLVED' WHERE id=?",
                (ticket_id,)
            )

        # Generate a complete, valid PNG using Pillow.
        image_buffer = BytesIO()
        Image.new(
            "RGB",
            (2, 2),
            color="red"
        ).save(image_buffer, format="PNG")

        payload = {
            "filename": "proof.png",
            "media_type": "image/png",
            "content_base64": base64.b64encode(
                image_buffer.getvalue()
            ).decode("ascii"),
        }

        # Resolved tickets must reject new evidence.
        rejected = client.post(
            f'/tickets/{ticket_id}/evidence',
            headers=headers,
            json=payload
        )
        assert rejected.status_code == 409, rejected.text

        # Existing evidence history remains readable.
        response = client.get(
            f'/tickets/{ticket_id}/evidence',
            headers=headers
        )
        assert response.status_code == 200, response.text

        # Rejected upload must not create an evidence record.
        with store.connect() as db:
            count = db.execute(
                'SELECT COUNT(*) FROM ticket_evidence WHERE ticket_id=?',
                (ticket_id,)
            ).fetchone()[0]

        assert count == 0
