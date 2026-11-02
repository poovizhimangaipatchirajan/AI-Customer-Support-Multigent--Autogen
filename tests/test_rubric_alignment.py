"""Focused, offline tests for additive rubric reporting."""
import csv
import io
from types import SimpleNamespace
import pytest
from backend.db import store
from backend.services.customer_orders import migrate as migrate_customer_orders
from backend.services_rubric_logging import migrate, record_agent_events, _rows, complaints_csv

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path/'rubric.sqlite3'))
    store.init()
    migrate_customer_orders()
    migrate()
    with store.connect() as c:
        c.execute("INSERT INTO users(id,email,password_hash,role,verified,full_name) VALUES ('u','u@example.test','x','customer',1,'Demo Customer')")
        c.execute("INSERT INTO tickets(id,customer_id,subject,description,status,risk,created_at) VALUES ('t','u','Shipping question','Where is my order?','PROCESSING','',1000)")
        c.execute("INSERT INTO jobs(id,ticket_id,status,created_at,lease_token,input_version) VALUES ('j','t','PROCESSING',1000,'lease1',0)")
    yield

def test_section7_columns_and_classified_labels(isolated):
    rows = _rows()
    assert len(rows)==1
    assert len(rows[0])==9
    assert rows[0]['category · urgency · sentiment'] == 'Shipping & delivery · Low · Neutral'
    assert rows[0]['complaint']=='Where is my order?'

def test_agent_events_are_observed_not_generated(isolated):
    job={'id':'j','ticket_id':'t','lease':'lease1','version':0}
    messages=[SimpleNamespace(source='QueryAgent',content='private content'),SimpleNamespace(source='KnowledgeAgent',content='internal SOP'),SimpleNamespace(source='unknown',content='x')]
    record_agent_events(job,messages)
    record_agent_events(job,messages)
    with store.connect() as c:
        rows=c.execute('SELECT * FROM agent_events ORDER BY agent').fetchall()
        assert len(rows)==2
        assert {r['agent'] for r in rows}=={'QueryAgent','KnowledgeAgent'}
        assert all('private content' not in str(tuple(r)) and 'internal SOP' not in str(tuple(r)) for r in rows)

def test_stale_lease_cannot_write_events(isolated):
    record_agent_events({'id':'j','ticket_id':'t','lease':'expired','version':0},[SimpleNamespace(source='LogAgent')])
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM agent_events').fetchone()[0]==0

def test_csv_export_columns(isolated):
    response=complaints_csv(user={'role':'admin'})
    rows=list(csv.DictReader(io.StringIO(response.body.decode())))
    assert len(rows)==1
    assert len(rows[0])==9

def test_customer_denied_export(isolated):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        complaints_csv(user={'role':'customer'})
    assert error.value.status_code==403
