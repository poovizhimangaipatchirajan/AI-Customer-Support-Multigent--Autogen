"""Decision-only review integration tests; isolated temporary SQLite."""
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.db import store
from backend.main import app
from backend.auth.security import hash_password, create_session

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DB',str(tmp_path/'phase8e.sqlite3'))
    with TestClient(app) as client: yield client

def user(role='customer'):
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
def test_inclusive_boundary(env,amount,expected):
    customer=user()
    ticket=submit(env,customer,amount)
    with store.connect() as c:
        record=c.execute('SELECT * FROM refund_reviews WHERE ticket_id=?',(ticket['id'],)).fetchone()
    assert (record is not None)==expected
    if expected:
        assert record['status']=='PENDING'
        assert record['execution_status']=='NOT_CONFIGURED'

def test_authorization_and_rejection_are_durable(env):
    customer=user()
    admin=user('admin')
    ticket=submit(env,customer,500)
    url='/admin/refund-reviews/'+ticket['id']
    assert env.get(url,headers=customer).status_code==403
    assert env.post(url+'/decision',headers=customer,json={'decision':'APPROVED','reason':'Not allowed','version':0}).status_code==403
    response=env.post(url+'/decision',headers=admin,json={'decision':'REJECTED','reason':'Insufficient supporting evidence','version':0})
    assert response.status_code==200,response.text
    assert response.json()['execution_status']=='NOT_CONFIGURED'
    assert env.post(url+'/decision',headers=admin,json={'decision':'APPROVED','reason':'Attempt to reverse','version':0}).status_code==409
    from backend.services.refund_approvals import migrate
    migrate()  # Simulates fresh startup migration, using same persistent DB.
    record=env.get(url,headers=admin).json()
    assert record['status']=='REJECTED' and record['version']==1
    assert record['execution_status']=='NOT_CONFIGURED'
    with store.connect() as c:
        events=[r['event'] for r in c.execute('SELECT event FROM audit WHERE ticket_id=?',(ticket['id'],))]
    assert events.count('REFUND_REVIEW_REJECTED')==1

def test_approval_is_not_execution_or_ticket_publication(env):
    customer=user()
    admin=user('admin')
    ticket=submit(env,customer,501)
    url='/admin/refund-reviews/'+ticket['id']
    prepare_refund_prerequisites(env, ticket['id'], admin, damaged=True, customer_headers=customer)
    response=env.post(url+'/decision',headers=admin,json={'decision':'APPROVED','reason':'Documents checked by reviewer','version':0})
    assert response.status_code==200,response.text
    assert response.json()['execution_status']=='NOT_CONFIGURED'
    detail=env.get('/tickets/'+ticket['id'],headers=customer).json()
    assert detail['status']=='ESCALATED'
    assert not any('refund issued' in m['content'].lower() for m in detail['messages'])
    assert env.post(url+'/decision',headers=admin,json={'decision':'APPROVED','reason':'Duplicate request','version':0}).status_code==409


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


def test_pdf_only_cannot_satisfy_damaged_claim(env):
    customer = user()
    admin = user('admin')
    ticket = submit(env, customer, 500)
    import base64
    pdf = base64.b64encode(b'%PDF-1.4\nsynthetic\n%%EOF').decode('ascii')
    upload = env.post(f"/tickets/{ticket['id']}/evidence", headers=customer,
        json={'filename': 'evidence.pdf', 'media_type': 'application/pdf', 'content_base64': pdf})
    assert upload.status_code == 201, upload.text
    response = env.post(f"/admin/refund-reviews/{ticket['id']}/assessment", headers=admin,
        json={'claim_type': 'DAMAGED_DEFECTIVE', 'evidence_status': 'ACCEPTED',
              'evidence_ids': [upload.json()['id']], 'reason': 'Reviewed submitted document', 'version': 0})
    assert response.status_code == 422, response.text
