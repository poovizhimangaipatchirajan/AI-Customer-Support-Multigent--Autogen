"""Human-in-the-loop approval recorded through AutoGen's UserProxyAgent.

When a support specialist approves (and optionally edits) an escalated reply
in the admin UI, the decision is stored and a review run is queued in the
same transaction. The worker then runs a small AutoGen conversation in which
a UserProxyAgent ("SupportReviewer") speaks the stored human decision and
LogAgent records it. The input function only reads the saved decision, so the
worker never blocks waiting for a person.

The human decision is authoritative: the reply is published when the admin
approves it, and a failed recording never unpublishes it or changes ticket
status. Nothing here executes a refund or payment.
"""
import time
import uuid

from backend.db.store import audit, connect

REVIEWER = 'SupportReviewer'
MAX_ATTEMPTS = 2           # one retry from a clean state (rule 37)
STALE_SECONDS = 300        # a run left PROCESSING this long is retried


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS human_review_runs (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL,
        reviewer_id TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('APPROVED')),
        response TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('PENDING','PROCESSING','DONE','FAILED')),
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        started_at REAL,
        completed_at REAL)''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_human_review_status ON human_review_runs(status, created_at)')


def enqueue(c, ticket_id, reviewer_id, response, decision='APPROVED'):
    """Queue the AutoGen recording of a human decision (caller's transaction)."""
    migrate(c)
    run_id = str(uuid.uuid4())
    c.execute('INSERT INTO human_review_runs (id,ticket_id,reviewer_id,decision,response,status,created_at) '
              "VALUES (?,?,?,?,?,'PENDING',?)", (run_id, ticket_id, reviewer_id, decision, response, time.time()))
    audit(c, ticket_id, 'HUMAN_REVIEW_QUEUED')
    return run_id


def claim():
    """Atomically take the oldest pending (or stale) run; None when idle."""
    now = time.time()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        migrate(c)
        row = c.execute(
            "SELECT * FROM human_review_runs WHERE attempts < ? AND (status='PENDING' OR "
            "(status='PROCESSING' AND started_at < ?)) ORDER BY created_at LIMIT 1",
            (MAX_ATTEMPTS, now - STALE_SECONDS)).fetchone()
        if not row:
            return None
        c.execute("UPDATE human_review_runs SET status='PROCESSING', attempts=attempts+1, started_at=? "
                  'WHERE id=?', (now, row['id']))
        ticket = c.execute('SELECT public_id FROM tickets WHERE id=?', (row['ticket_id'],)).fetchone()
        return {**dict(row), 'attempts': row['attempts'] + 1,
                'ticket_code': (ticket['public_id'] if ticket else None) or row['ticket_id']}


def reviewer_message(run):
    """What the UserProxyAgent says: the stored human decision, verbatim."""
    return (f"HUMAN DECISION: {run['decision']}. A NovaCart support specialist reviewed "
            f"escalated ticket {run['ticket_code']} and approved this reply, which has been "
            f"sent to the customer:\n{run['response']}")


def record(run, decision, summary):
    """LogAgent's tool. The model can record the human decision, never alter it."""
    if (decision or '').strip().upper() != run['decision']:
        return f"REJECTED: the human decision is {run['decision']}; record it exactly."
    from backend.services.complaint_log import migrate as migrate_agent_tables
    migrate_agent_tables()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute('SELECT status FROM human_review_runs WHERE id=?', (run['id'],)).fetchone()
        if not current or current['status'] != 'PROCESSING':
            return 'REJECTED: review run is not active.'
        now = time.time()
        c.execute("UPDATE human_review_runs SET status='DONE', completed_at=? WHERE id=?", (now, run['id']))
        for offset, agent in enumerate((REVIEWER, 'LogAgent')):
            c.execute('INSERT OR IGNORE INTO agent_events VALUES (?,?,?,?,?,?,?)',
                      (str(uuid.uuid4()), run['ticket_id'], run['id'], 'human-review', agent,
                       'MESSAGE_OBSERVED', now + offset * 0.000001))
        c.execute('INSERT OR IGNORE INTO agent_output_summaries VALUES (?,?,?,?,?,?,?)',
                  (str(uuid.uuid4()), run['ticket_id'], run['id'], 'human-review', 'LogAgent',
                   ('Human review logged: ' + (summary or '').strip())[:1000], now))
        audit(c, run['ticket_id'], 'HUMAN_REVIEW_LOGGED:' + run['decision'])
    return 'LOGGED'


def fail(run):
    """Retry once from PENDING, then mark FAILED. The approved reply stays published."""
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute('SELECT status, attempts FROM human_review_runs WHERE id=?', (run['id'],)).fetchone()
        if not current or current['status'] == 'DONE':
            return
        final = current['attempts'] >= MAX_ATTEMPTS
        c.execute('UPDATE human_review_runs SET status=?, completed_at=? WHERE id=?',
                  ('FAILED' if final else 'PENDING', time.time() if final else None, run['id']))
        audit(c, run['ticket_id'], 'HUMAN_REVIEW_LOG_FAILED' if final else 'HUMAN_REVIEW_RETRY')


def latest_for_ticket(c, ticket_id):
    """Customer/admin-safe summary (no reviewer identity, no reply text)."""
    migrate(c)
    row = c.execute('SELECT status, decision, completed_at FROM human_review_runs WHERE ticket_id=? '
                    'ORDER BY created_at DESC LIMIT 1', (ticket_id,)).fetchone()
    return dict(row) if row else None
