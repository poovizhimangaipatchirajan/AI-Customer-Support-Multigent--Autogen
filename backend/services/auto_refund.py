"""Low-value automatic refund acceptance (no human review). Never moves money.

Policy (README "Refund flow"; owner-approved amendment of the refund rules): a damaged/defective refund request
under INR 500 is queued for refund processing without a human review step only when
every deterministic check below passes. Otherwise the existing review flow
applies. Photos are confirmed as genuine uploaded images, never as proof of
the claimed defect; no payment is executed (execution_status NOT_CONFIGURED).
"""
import json
import time
import uuid

from backend.db.store import audit

THRESHOLD = 500.0
WINDOW_SECONDS = 365 * 24 * 3600
AUTO_LIMIT_PER_WINDOW = 1

# Fixed system text; reviewed Tamil/Tanglish versions live with it.
from backend.services.fixed_messages import AUTO_REFUND_ACCEPTED as ACCEPTED_MESSAGE  # noqa: E402


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS auto_refund_acceptances (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL UNIQUE,
        customer_id TEXT NOT NULL,
        order_code TEXT NOT NULL,
        amount REAL NOT NULL CHECK(amount > 0 AND amount < 500),
        evidence_ids TEXT NOT NULL,
        accepted_at REAL NOT NULL,
        execution_status TEXT NOT NULL DEFAULT 'NOT_CONFIGURED'
            CHECK(execution_status='NOT_CONFIGURED'))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_auto_refund_customer '
              'ON auto_refund_acceptances(customer_id, accepted_at)')


def eligibility(c, ticket_id, now=None):
    """Return (eligible, reason, details). reason names the first failed check."""
    from backend.services.risk import REFUND
    from backend.services.refund_approvals import known_damage_claim, valid_image_evidence
    now = time.time() if now is None else now
    migrate(c)
    t = c.execute('SELECT * FROM tickets WHERE id=?', (ticket_id,)).fetchone()
    if not t:
        return False, 'TICKET_MISSING', {}
    text = f"{t['subject'] or ''} {t['description'] or ''}"
    followups = ' '.join(r['content'] or '' for r in c.execute(
        "SELECT content FROM messages WHERE ticket_id=? AND role='customer'", (ticket_id,)))
    if not (REFUND.search(text.casefold()) or REFUND.search(followups.casefold())):
        return False, 'NOT_A_REFUND_REQUEST', {}
    if set(filter(None, (t['risk'] or '').split(','))):
        return False, 'RISK_FLAGS_PRESENT', {}
    amount = t['claimed_amount']
    if amount is None or not (0 < float(amount) < THRESHOLD):
        return False, 'AMOUNT_NOT_UNDER_THRESHOLD', {}
    present = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'refund_reviews', 'ticket_order_links', 'demo_orders', 'ticket_evidence'} <= present:
        return False, 'VERIFICATION_DATA_UNAVAILABLE', {}  # fail closed
    if c.execute('SELECT 1 FROM refund_reviews WHERE ticket_id=?', (ticket_id,)).fetchone():
        return False, 'HUMAN_REVIEW_EXISTS', {}
    if not known_damage_claim(c, ticket_id):
        return False, 'NOT_A_DAMAGE_CLAIM', {}
    order = c.execute(
        '''SELECT o.order_code, o.status, o.total_amount FROM ticket_order_links l
           JOIN demo_orders o ON o.order_code=l.order_code AND o.customer_id=l.customer_id
           WHERE l.ticket_id=? AND l.customer_id=?''', (ticket_id, t['customer_id'])).fetchone()
    if not order:
        return False, 'NO_VERIFIED_ORDER', {}
    if order['status'] != 'DELIVERED':
        return False, 'ORDER_NOT_DELIVERED', {}
    if float(amount) > float(order['total_amount']):
        return False, 'AMOUNT_EXCEEDS_ORDER', {}
    image_ids = [r['id'] for r in c.execute(
        "SELECT id FROM ticket_evidence WHERE ticket_id=? AND media_type IN ('image/png','image/jpeg')",
        (ticket_id,))]
    valid = [i for i in image_ids if valid_image_evidence(c, ticket_id, [i])]
    if not valid:
        return False, 'NO_IMAGE_EVIDENCE', {}
    if c.execute('SELECT 1 FROM auto_refund_acceptances WHERE order_code=?',
                 (order['order_code'],)).fetchone():
        return False, 'ORDER_ALREADY_AUTO_REFUNDED', {}
    recent = c.execute('SELECT COUNT(*) FROM auto_refund_acceptances WHERE customer_id=? AND accepted_at>?',
                       (t['customer_id'], now - WINDOW_SECONDS)).fetchone()[0]
    if recent >= AUTO_LIMIT_PER_WINDOW:
        return False, 'ANNUAL_AUTO_REFUND_LIMIT_REACHED', {}
    return True, 'ELIGIBLE', {'order_code': order['order_code'], 'amount': float(amount),
                              'customer_id': t['customer_id'], 'evidence_ids': valid}


def try_accept(c, ticket_id, now=None):
    """Record an automatic acceptance inside the caller's transaction.

    Returns the customer message when accepted, otherwise None. The caller
    owns ticket status/message updates so worker and upload paths stay atomic.
    """
    ok, reason, d = eligibility(c, ticket_id, now)
    if not ok:
        return None
    c.execute('INSERT INTO auto_refund_acceptances '
              '(id,ticket_id,customer_id,order_code,amount,evidence_ids,accepted_at) VALUES (?,?,?,?,?,?,?)',
              (str(uuid.uuid4()), ticket_id, d['customer_id'], d['order_code'], d['amount'],
               json.dumps(d['evidence_ids']), time.time() if now is None else now))
    audit(c, ticket_id, 'AUTO_REFUND_ACCEPTED')
    return ACCEPTED_MESSAGE.format(order=d['order_code'], amount=d['amount'])


def accept_after_evidence(c, ticket_id):
    """Re-evaluate when photos arrive after the agents finished.

    Only tickets that are settled (not queued/processing) and have no human
    decision in progress are moved; the version bump invalidates stale admin
    actions. Returns True when the ticket was accepted.
    """
    t = c.execute('SELECT status FROM tickets WHERE id=?', (ticket_id,)).fetchone()
    if not t or t['status'] not in ('ESCALATED', 'AWAITING_CUSTOMER'):
        return False
    if c.execute("SELECT 1 FROM jobs WHERE ticket_id=? AND status IN ('QUEUED','PROCESSING')",
                 (ticket_id,)).fetchone():
        return False
    message = try_accept(c, ticket_id)
    if not message:
        return False
    # Reply in the language of the customer's latest message (fixed text, no model).
    from backend.services.fixed_messages import localized
    from backend.services.language import detect_language
    latest = c.execute("SELECT content FROM messages WHERE ticket_id=? AND role='customer' "
                       "ORDER BY created_at DESC, rowid DESC LIMIT 1", (ticket_id,)).fetchone()
    message = localized(message, detect_language(latest['content'] if latest else ''))
    c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',
              (str(uuid.uuid4()), ticket_id, 'assistant', message, 1, time.time()))
    c.execute("UPDATE tickets SET status='ANSWERED', version=version+1 WHERE id=?", (ticket_id,))
    return True


def acceptance(c, ticket_id):
    migrate(c)
    row = c.execute('SELECT amount, accepted_at, execution_status FROM auto_refund_acceptances '
                    'WHERE ticket_id=?', (ticket_id,)).fetchone()
    return dict(row) if row else None
