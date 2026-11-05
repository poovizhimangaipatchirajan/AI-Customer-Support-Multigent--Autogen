"""Isolated, opt-in real worker smoke test for FixFlow AI.
Run from project root: python scripts/phase9b_worker_smoke.py
Uses real OpenAI APIs; does not use the application's production/local database.
"""
import asyncio
import json
import os
import tempfile
import time
import uuid
from pathlib import Path


def main():
    if not os.getenv('FIXFLOW_RUN_WORKER_SMOKE') == '1':
        raise SystemExit('Opt in with: set FIXFLOW_RUN_WORKER_SMOKE=1')

    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / '.env')
    if not os.getenv('OPENAI_API_KEY'):
        raise SystemExit('OPENAI_API_KEY missing; no test executed')

    # A temporary DB path is set before importing backend database or worker modules.
    with tempfile.TemporaryDirectory(prefix='fixflow_worker_smoke_') as temp:
        db_path = str(Path(temp) / 'isolated.sqlite3')
        previous = os.environ.get('FIXFLOW_DB')
        os.environ['FIXFLOW_DB'] = db_path
        try:
            from backend.db.store import init, connect
            from backend.rag.hybrid import HybridRetriever
            from backend.worker.main import claim, process

            init()
            retriever = HybridRetriever()  # Real FAISS + BM25 index, no fixture.
            ticket_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
            now = time.time()
            with connect() as conn:
                conn.execute(
                    'INSERT INTO tickets (id,customer_id,subject,description,claimed_amount,status,risk,created_at,version) '
                    'VALUES (?,?,?,?,?,?,?,?,?)',
                    (ticket_id, 'ISOLATED_TEST_CUSTOMER', 'Product details for MOUSE-01',
                     'What is MOUSE-01? Please check product information and cite relevant approved product policy. '
                     'Do not claim live inventory or shipment tracking.', None, 'QUEUED', '', now, 0),
                )
                conn.execute(
                    'INSERT INTO jobs (id,ticket_id,status,created_at,attempts,input_version) VALUES (?,?,?,?,?,?)',
                    (job_id, ticket_id, 'QUEUED', now, 0, 0),
                )
                conn.execute(
                    'INSERT INTO messages (id,ticket_id,role,content,visible,created_at) VALUES (?,?,?,?,?,?)',
                    (str(uuid.uuid4()), ticket_id, 'user',
                     'Please explain the product MOUSE-01 and relevant product policy.', 1, now),
                )

            row = claim()
            assert row and row['id'] == job_id, 'Worker did not claim the isolated job'
            asyncio.run(process(row, retriever))

            with connect() as conn:
                job = conn.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
                ticket = conn.execute('SELECT status FROM tickets WHERE id=?', (ticket_id,)).fetchone()
                result = conn.execute('SELECT draft,citations,requires_review FROM ai_results WHERE job_id=?', (job_id,)).fetchone()
                messages = conn.execute('SELECT role,content FROM messages WHERE ticket_id=? AND role=? AND visible=1',
                                        (ticket_id, 'assistant')).fetchall()
                audits = conn.execute('SELECT event FROM audit WHERE ticket_id=?', (ticket_id,)).fetchall()

            print('Worker job status:', job['status'] if job else 'MISSING')
            print('Ticket status:', ticket['status'] if ticket else 'MISSING')
            print('Persisted AI result:', bool(result))
            print('Customer-visible assistant messages:', len(messages))
            print('Audit events:', [r['event'] for r in audits])
            assert job and job['status'] == 'DONE', 'Job did not complete successfully'
            assert result, 'No AI result persisted'
            ids = json.loads(result['citations'])
            assert isinstance(ids, list), 'Invalid citations representation'
            if ticket['status'] == 'ANSWERED':
                assert ids, 'Answered ticket has no approved policy citations'
                assert not result['requires_review'], 'Unreviewed result was published'
                assert messages, 'Answered ticket has no visible assistant response'
            else:
                assert ticket['status'] == 'ESCALATED', 'Unexpected ticket status'
                assert not messages, 'Escalated result became customer-visible'
            print('PASS: Isolated worker processing and persistence invariants verified')
            print('NOTE: This test does not independently prove the MCP tool was called.')
        finally:
            if previous is None:
                os.environ.pop('FIXFLOW_DB', None)
            else:
                os.environ['FIXFLOW_DB'] = previous


if __name__ == '__main__':
    main()
