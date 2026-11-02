import os
import sqlite3
import uuid

from backend.db.store import connect, init


def test_public_ids_backfill_and_new_rows(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXFLOW_DB', str(tmp_path / 'fixflow.sqlite3'))
    init()
    with connect() as c:
        c.execute("INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)", ('user-1','customer@example.test','hash','customer',1))
        c.execute("INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at) VALUES (?,?,?,?,?,?,?,?)", ('ticket-1','user-1','Shipping','Question about shipping',None,'QUEUED','',1793426201))
        user = c.execute("SELECT public_id FROM users WHERE id='user-1'").fetchone()['public_id']
        ticket = c.execute("SELECT public_id FROM tickets WHERE id='ticket-1'").fetchone()['public_id']
        assert user.startswith('NC-')
        assert ticket.startswith('FF-2026-')
    init()
    with connect() as c:
        assert c.execute("SELECT public_id FROM users WHERE id='user-1'").fetchone()['public_id'] == user
        assert c.execute("SELECT public_id FROM tickets WHERE id='ticket-1'").fetchone()['public_id'] == ticket
