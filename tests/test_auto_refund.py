"""Low-value automatic refund acceptance: offline, isolated DB, no OpenAI."""
import base64
import io
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.auth.security import create_session, hash_password
from backend.db import store
from backend.main import app
from backend.services import auto_refund
from backend.worker import main as worker


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'auto_refund.sqlite3'))
    monkeypatch.setenv('FIXFLOW_LOCAL_DEMO', '1')
    monkeypatch.setenv('FIXFLOW_EVIDENCE_DIR', str(tmp_path / 'evidence'))
    with TestClient(app) as c:
        yield c


def customer(client):
    uid = str(uuid.uuid4())
    with store.connect() as db:
        db.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,1)',
                   (uid, uid + '@example.test', hash_password('Auto-Refund-Test1!'), 'customer'))
    headers = {'Authorization': 'Bearer ' + create_session(uid)}
    assert client.post('/account/orders/seed-demo', headers=headers,
                       json={'confirmation': 'CREATE_FICTIONAL_ORDERS'}).status_code == 201
    orders = {o['status']: o['order_code'] for o in client.get('/account/orders', headers=headers).json()}
    return uid, headers, orders


def add_delivered_order(uid, total=999.0):
    code = 'EXTRA-' + uuid.uuid4().hex[:8]
    with store.connect() as db:
        db.execute('INSERT INTO demo_orders(id,order_code,customer_id,placed_at,status,total_amount) '
                   'VALUES (?,?,?,?,?,?)', (str(uuid.uuid4()), code, uid, '2026-10-20', 'DELIVERED', total))
    return code


def png():
    buf = io.BytesIO()
    Image.new('RGB', (16, 16), (200, 30, 30)).save(buf, 'PNG')
    return {'filename': 'damage.png', 'media_type': 'image/png',
            'content_base64': base64.b64encode(buf.getvalue()).decode()}


def raise_ticket(client, headers, order, amount=299, text='My USB-C cable arrived damaged and does not charge. Please refund me.',
                 subject='Damaged item'):
    r = client.post('/tickets', headers=headers, json={
        'subject': subject, 'description': text, 'claimed_amount': amount, 'order_code': order})
    assert r.status_code == 201, r.text
    return r.json()


def run_worker(tid, requires_review=1):
    """Simulate a completed four-agent run that flagged a case decision."""
    row = worker.claim()
    assert row is not None and row['ticket_id'] == tid
    with store.connect() as db:
        db.execute('INSERT INTO ai_results(job_id,ticket_id,summary,draft,citations,requires_review,created_at) '
                   'VALUES (?,?,?,?,?,?,?)', (row['id'], tid, 'Damage refund', 'AGENT_DRAFT', '[]',
                                              requires_review, time.time()))
    worker.finish(row, True)


def state(client, headers, tid):
    detail = client.get(f'/tickets/{tid}', headers=headers).json()
    review = client.get(f'/tickets/{tid}/refund-review', headers=headers).json()
    with store.connect() as db:
        audit = [r['event'] for r in db.execute('SELECT event FROM audit WHERE ticket_id=?', (tid,))]
    return detail, review, audit


def test_eligible_low_value_damage_refund_is_accepted_without_review(client):
    uid, h, orders = customer(client)
    t = raise_ticket(client, h, orders['DELIVERED'])
    assert t['risk_flags'] == [] and t['status'] == 'QUEUED'
    assert client.post(f"/tickets/{t['id']}/evidence", headers=h, json=png()).status_code == 201
    run_worker(t['id'])
    detail, review, audit = state(client, h, t['id'])
    assert detail['status'] == 'ANSWERED'
    reply = [m['content'] for m in detail['messages'] if m['role'] == 'assistant'][-1]
    assert 'accepted for processing' in reply and 'No payment has been made yet' in reply
    assert 'AGENT_DRAFT' not in reply
    assert review['status'] == 'AUTO_ACCEPTED' and review['execution_status'] == 'NOT_CONFIGURED'
    assert 'AUTO_REFUND_ACCEPTED' in audit
    # Not in the human review queue.
    with store.connect() as db:
        assert not db.execute('SELECT 1 FROM refund_reviews WHERE ticket_id=?', (t['id'],)).fetchone()


def test_photos_uploaded_after_agents_finish_complete_acceptance(client):
    uid, h, orders = customer(client)
    t = raise_ticket(client, h, orders['DELIVERED'])
    run_worker(t['id'])
    assert state(client, h, t['id'])[0]['status'] == 'ESCALATED'
    up = client.post(f"/tickets/{t['id']}/evidence", headers=h, json=png())
    assert up.status_code == 201 and up.json()['auto_refund_accepted'] is True
    assert state(client, h, t['id'])[0]['status'] == 'ANSWERED'


def test_only_one_automatic_refund_per_customer_per_year(client):
    uid, h, orders = customer(client)
    first = raise_ticket(client, h, orders['DELIVERED'])
    client.post(f"/tickets/{first['id']}/evidence", headers=h, json=png())
    run_worker(first['id'])
    assert state(client, h, first['id'])[0]['status'] == 'ANSWERED'

    second = raise_ticket(client, h, add_delivered_order(uid))
    client.post(f"/tickets/{second['id']}/evidence", headers=h, json=png())
    run_worker(second['id'])
    assert state(client, h, second['id'])[0]['status'] == 'ESCALATED'
    with store.connect() as db:
        assert auto_refund.eligibility(db, second['id'])[1] == 'ANNUAL_AUTO_REFUND_LIMIT_REACHED'
        # After 365 days the customer is eligible again.
        later = time.time() + auto_refund.WINDOW_SECONDS + 60
        assert auto_refund.eligibility(db, second['id'], now=later)[0] is True


def test_same_order_never_auto_refunded_twice(client):
    uid, h, orders = customer(client)
    first = raise_ticket(client, h, orders['DELIVERED'])
    client.post(f"/tickets/{first['id']}/evidence", headers=h, json=png())
    run_worker(first['id'])
    later = time.time() + auto_refund.WINDOW_SECONDS + 60
    second = raise_ticket(client, h, orders['DELIVERED'])
    client.post(f"/tickets/{second['id']}/evidence", headers=h, json=png())
    with store.connect() as db:
        assert auto_refund.eligibility(db, second['id'], now=later)[1] == 'ORDER_ALREADY_AUTO_REFUNDED'


@pytest.mark.parametrize('case,expected_reason', [
    ('amount_500', 'RISK_FLAGS_PRESENT'),
    ('no_order', 'NO_VERIFIED_ORDER'),
    ('not_delivered', 'ORDER_NOT_DELIVERED'),
    ('no_photo', 'NO_IMAGE_EVIDENCE'),
    ('pdf_only', 'NO_IMAGE_EVIDENCE'),
    ('fraud', 'RISK_FLAGS_PRESENT'),
    ('not_damage', 'NOT_A_DAMAGE_CLAIM'),
    ('over_order_total', 'AMOUNT_EXCEEDS_ORDER'),
])
def test_ineligible_cases_keep_the_review_flow(client, case, expected_reason):
    uid, h, orders = customer(client)
    order, amount = orders['DELIVERED'], 299
    text = 'My USB-C cable arrived damaged and does not charge. Please refund me.'
    if case == 'amount_500':
        amount = 500
    elif case == 'no_order':
        order = None
    elif case == 'not_delivered':
        order = orders['SHIPPED']
    elif case == 'fraud':
        text = 'This is fraud, my cable arrived damaged. Please refund me.'
    elif case == 'not_damage':
        text = 'I changed my mind about this cable. Please refund me.'
    elif case == 'over_order_total':
        order = add_delivered_order(uid, total=100.0)
    t = raise_ticket(client, h, order, amount, text,
                     subject='Refund request' if case == 'not_damage' else 'Damaged item')
    if case == 'pdf_only':
        pdf = base64.b64encode(b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF').decode()
        assert client.post(f"/tickets/{t['id']}/evidence", headers=h, json={
            'filename': 'receipt.pdf', 'media_type': 'application/pdf', 'content_base64': pdf}).status_code == 201
    elif case != 'no_photo':
        assert client.post(f"/tickets/{t['id']}/evidence", headers=h, json=png()).status_code == 201
    if t['status'] == 'QUEUED':
        run_worker(t['id'])
    detail, review, audit = state(client, h, t['id'])
    assert detail['status'] != 'ANSWERED' or 'accepted for processing' not in str(detail['messages'])
    assert 'AUTO_REFUND_ACCEPTED' not in audit
    assert review.get('status') != 'AUTO_ACCEPTED'
    with store.connect() as db:
        assert auto_refund.eligibility(db, t['id'])[1] == expected_reason
    if case == 'amount_500':
        assert review['status'] == 'PENDING' and detail['status'] == 'ESCALATED'
