import pytest
pytest.importorskip('rank_bm25')
from backend.db.store import init,connect
from backend.worker.main import recover,claim

def test_claim_fifo(tmp_path,monkeypatch):
 monkeypatch.setenv('FIXFLOW_DB',str(tmp_path/'test.sqlite3'))
 init();recover()
 with connect() as c:
  c.execute("INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at) VALUES ('t','demo','test','description',NULL,'QUEUED','',1)")
  c.execute("INSERT INTO jobs(id,ticket_id,status,created_at) VALUES ('j','t','QUEUED',1)")
 assert claim()['id']=='j'
 assert claim() is None
