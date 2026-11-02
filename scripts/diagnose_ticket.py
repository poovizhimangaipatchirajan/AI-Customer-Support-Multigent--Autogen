"""Read-only FixFlow AI ticket decision diagnostic.

Usage: python -m scripts.diagnose_ticket FF-2026-000102
"""
import argparse
import json
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ticket', help='Public ticket ID, e.g. FF-2026-000102')
    args = parser.parse_args()

    # This mirrors backend.db.store.database_path() without importing the app
    # or invoking initialization/migrations.
    import os
    db_path = Path(os.environ.get('FIXFLOW_DB', 'fixflow_local.sqlite3')).resolve()
    if not db_path.is_file():
        raise SystemExit(f'Database not found: {db_path}\nCheck FIXFLOW_DB in your running API/worker environment.')
    uri = db_path.as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        ticket = connection.execute(
            'SELECT id, public_id, subject, description, status, risk, claimed_amount, version '
            'FROM tickets WHERE public_id = ?', (args.ticket,)
        ).fetchone()
        if ticket is None:
            raise SystemExit(f'Ticket not found: {args.ticket} in {db_path}')
        print('Database:', db_path)
        print('\nTICKET')
        for field in ('public_id', 'subject', 'description', 'status', 'risk', 'claimed_amount', 'version'):
            print(f'{field}: {ticket[field]}')

        results = connection.execute(
            'SELECT a.job_id, a.summary, a.draft, a.citations, a.requires_review, '
            'j.status AS job_status, j.attempts '
            'FROM ai_results a LEFT JOIN jobs j ON j.id = a.job_id '
            'WHERE a.ticket_id = ? ORDER BY a.created_at DESC LIMIT 5',
            (ticket['id'],)
        ).fetchall()
        print(f'\nAI RESULTS ({len(results)})')
        for n, result in enumerate(results, 1):
            print(f'\nResult #{n}')
            for field in ('job_id', 'job_status', 'attempts', 'summary', 'draft', 'requires_review'):
                print(f'{field}: {result[field]}')
            try:
                citations = json.loads(result['citations'])
            except (ValueError, TypeError):
                citations = result['citations']
            print('citations:', citations)

        audits = connection.execute(
            'SELECT event FROM audit WHERE ticket_id=? ORDER BY created_at DESC LIMIT 10',
            (ticket['id'],)
        ).fetchall()
        print('\nRECENT AUDIT EVENTS:', [row['event'] for row in audits])
        print('\nInterpretation: risk nonempty, requires_review=1, and empty citations are separate signals. '
              'This snapshot cannot reconstruct the agent tool-call arguments or explain why the LogAgent chose them.')
    finally:
        connection.close()


if __name__ == '__main__':
    main()
