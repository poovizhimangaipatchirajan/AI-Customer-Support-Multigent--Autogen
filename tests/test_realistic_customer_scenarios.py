"""24 realistic customer complaints: offline API intake, queueing, and isolation.

Does NOT run AutoGen, retrieve RAG evidence, or certify AI response quality.
No live API keys, Docker, or existing database required.
"""
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.auth.security import create_session, hash_password
from backend.db import store
from backend.main import app

# (id, category, exact customer utterance, mandatory immediate human escalation)
SCENARIOS = [
('S01','Return policy',"What is NovaCart's return policy? Please explain the eligibility conditions and cite the relevant policy.",False),
('S02','Return policy',"I bought something last week, opened the box, and now I'm not sure I want it. Can I still send it back?",False),
('S03','Return policy',"The product works, but it isn't what I expected. Is that enough reason to return it?",False),
('S04','Shipping',"What is the standard time for shipping and delivery at NovaCart?",False),
('S05','Shipping',"Please tell me the status of my shipment. I haven't received anything yet.",False),
('S06','Shipping',"It's been a while since I ordered. Do you think it'll arrive before Friday?",False),
('S07','Combined',"How long does delivery usually take, and what if I want to return the product after receiving it?",False),
('S08','Combined',"My parcel is late, and I might cancel it. Will I get my money back?",False),
('S09','Combined',"The item arrived damaged and two days late. Can I return it, or will you send a replacement?",False),
('S10','Combined',"I ordered headphones for ₹750, but they arrived broken. I want my money back, and I need a replacement urgently.",False),
('S11','Indirect',"Everyone else seems to have received their order. Mine still says processing.",False),
('S12','Follow-up',"So does that mean I'm eligible?",False),
('S13','Follow-up',"You said five days earlier, but now it's been eight. What's happening?",False),
('S14','Follow-up',"Okay, forget the replacement. Just refund the ₹650 instead.",False),
('S15','Unsupported promise',"Can you guarantee delivery tomorrow?",False),
('S16','Policy contradiction',"Another agent told me all returns are free. Is that true?",False),
('S17','Privacy',"My order ID is someone else's, but can you show me its delivery address?",False),
('S18','Payment dispute',"I was charged twice, and I want an immediate refund of ₹900.",True),
('S19','Shipping',"Where is my order? It was supposed to arrive yesterday.",False),
('S20','Refund',"I want to return the shoes I bought last week. How do I get a refund?",False),
('S21','Account',"I forgot my password and can't log in.",False),
('S22','Replacement',"The blender I received is broken. Can I get a replacement?",False),
('S23','Payment/legal',"You charged my card three times! Fix this now or I'm contacting my bank and a lawyer.",True),
('S24','Unsupported offering',"Do you sell gift cards for a company of 500 employees with custom branding?",False),
]

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'realistic_scenarios.sqlite3'))
    with TestClient(app) as test_client:
        yield test_client

def customer():
    uid = str(uuid.uuid4())
    with store.connect() as db:
        db.execute('INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)',
                   (uid, uid + '@example.test', hash_password('Testing123!Secure'), 'customer', 1))
    return {'Authorization': 'Bearer ' + create_session(uid)}

@pytest.mark.parametrize('case', SCENARIOS, ids=[x[0] for x in SCENARIOS])
def test_intake_queue_and_customer_isolation(client, case):
    scenario_id, category, question, must_escalate = case
    owner, stranger = customer(), customer()
    response = client.post('/tickets', headers=owner,
                           json={'subject': f'{scenario_id}: {category}', 'description': question})
    assert response.status_code == 201, (scenario_id, response.text)
    ticket = response.json()
    assert ticket['status'] in {'QUEUED', 'ESCALATED'}
    assert client.get('/tickets/' + ticket['id'], headers=owner).status_code == 200
    assert client.get('/tickets/' + ticket['id'], headers=stranger).status_code == 404
    with store.connect() as db:
        jobs = db.execute('SELECT status FROM jobs WHERE ticket_id=?', (ticket['id'],)).fetchall()
        assert (len(jobs) == 0) if ticket['status'] == 'ESCALATED' else (len(jobs) == 1 and jobs[0]['status'] == 'QUEUED')
    if must_escalate:
        assert ticket['status'] == 'ESCALATED', f'{scenario_id}: payment dispute must be escalated'

@pytest.mark.parametrize('case', [SCENARIOS[0], SCENARIOS[3], SCENARIOS[6], SCENARIOS[23]], ids=['return','shipping','combined','gift-cards'])
def test_no_premature_automatic_resolution(client, case):
    scenario_id, category, question, _ = case
    response = client.post('/tickets', headers=customer(),
                           json={'subject': f'{scenario_id}: {category}', 'description': question})
    assert response.status_code == 201
    assert response.json()['status'] not in {'ANSWERED', 'RESOLVED', 'REFUNDED'}
