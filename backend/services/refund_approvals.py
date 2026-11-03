"""Durable, decision-only refund reviews. Never executes payments."""
import time
import uuid
import re
import hashlib
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from backend.db.store import connect, audit
from backend.auth.security import principal, admin_only
from backend.services.ticket_evidence import root, sniff

router = APIRouter(prefix='/admin/refund-reviews', tags=['refund-reviews'])

# Conservative guard for explicit damage/defect allegations. Ambiguous or negated
# language is not treated as proof of an OTHER claim: reviewers must investigate.
DAMAGE_TERMS = re.compile(
    r"\b(?:damag(?:e|ed)|defect(?:ive|s)?|broken|cracked|shattered|"
    r"faulty|malfunction(?:ing|s)?|leak(?:ing|s)?|not working)\b|"
    r"சேதம்|உடைந்த|பழுத", re.I
)


def known_damage_claim(c, ticket_id):
    ticket = c.execute('SELECT subject, description FROM tickets WHERE id=?', (ticket_id,)).fetchone()
    if not ticket:
        return False
    if DAMAGE_TERMS.search((ticket['subject'] or '') + ' ' + (ticket['description'] or '')):
        return True
    # Only customer messages are evidence of the customer's claim.
    return any(DAMAGE_TERMS.search(row['content'] or '') for row in c.execute(
        "SELECT content FROM messages WHERE ticket_id=? AND role='customer'", (ticket_id,)))


def valid_image_evidence(c, ticket_id, evidence_ids):
    """Require a genuine, still-present, unchanged image owned by this ticket."""
    for eid in evidence_ids:
        row = c.execute('SELECT media_type, storage_name, sha256 FROM ticket_evidence '
                        'WHERE ticket_id=? AND id=?', (ticket_id, eid)).fetchone()
        if not row or row['media_type'] not in ('image/png', 'image/jpeg'):
            continue
        # Storage names originate from UUID-based upload paths, never request input.
        name = row['storage_name']
        if Path(name).name != name:
            continue
        path = root() / name
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(data).hexdigest() == row['sha256'] and sniff(data, row['media_type']):
            return True
    return False

class Assessment(BaseModel):
    claim_type: str = Field(pattern='^(DAMAGED_DEFECTIVE|OTHER)$')
    evidence_status: str = Field(pattern='^(ACCEPTED|REJECTED|NOT_REQUIRED)$')
    evidence_ids: list[str] = Field(default_factory=list, max_length=5)
    reason: str = Field(min_length=5, max_length=2000)
    version: int = Field(ge=0)

class Reconciliation(BaseModel):
    amount: float = Field(ge=0)
    reason: str = Field(min_length=5, max_length=2000)
    version: int = Field(ge=0)

class Decision(BaseModel):
    decision: str = Field(pattern='^(APPROVED|REJECTED)$')
    reason: str = Field(min_length=5, max_length=2000)
    version: int = Field(ge=0)

def migrate():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS refund_reviews (
          id TEXT PRIMARY KEY,
          ticket_id TEXT NOT NULL UNIQUE,
          amount TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','REJECTED')),
          version INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL,
          decided_at REAL,
          reviewer_id TEXT,
          reason TEXT,
          execution_status TEXT NOT NULL DEFAULT 'NOT_CONFIGURED'
            CHECK(execution_status='NOT_CONFIGURED')
        );
        CREATE INDEX IF NOT EXISTS idx_refund_reviews_status ON refund_reviews(status);
        CREATE TABLE IF NOT EXISTS refund_evidence_assessments (
          ticket_id TEXT PRIMARY KEY,
          claim_type TEXT NOT NULL CHECK(claim_type IN ('DAMAGED_DEFECTIVE','OTHER')),
          evidence_status TEXT NOT NULL CHECK(evidence_status IN ('ACCEPTED','REJECTED','NOT_REQUIRED')),
          evidence_ids TEXT NOT NULL,
          reason TEXT NOT NULL,
          reviewer_id TEXT NOT NULL,
          assessed_at REAL NOT NULL,
          version INTEGER NOT NULL DEFAULT 0
        );
        ''')

def register_pending(c, ticket_id, amount, flags):
    """Call in the same DB transaction as ticket creation, after migrate()."""
    if 'REFUND_GE_500' not in flags:
        return
    c.execute('''INSERT OR IGNORE INTO refund_reviews
      (id,ticket_id,amount,status,created_at)
      VALUES (?,?,?,'PENDING',?)''',
      (str(uuid.uuid4()),ticket_id,str(amount),time.time()))
    audit(c,ticket_id,'REFUND_REVIEW_PENDING')

@router.get('')
def pending(user=Depends(principal)):
    admin_only(user)
    with connect() as c:
        return [dict(r) for r in c.execute('''SELECT id,ticket_id,amount,status,version,created_at,
            execution_status FROM refund_reviews WHERE status='PENDING' ORDER BY created_at''')]

@router.get('/history')
def review_history(user=Depends(principal)):
    """Admin-only decided review history; decisions never execute payments."""
    admin_only(user)
    with connect() as c:
        rows = c.execute("SELECT r.ticket_id, t.public_id AS ticket_code, r.amount, r.status, r.decided_at, r.execution_status, r.reason FROM refund_reviews r JOIN tickets t ON t.id=r.ticket_id WHERE r.status IN ('APPROVED','REJECTED') ORDER BY r.decided_at DESC LIMIT 200").fetchall()
    return [dict(row) for row in rows]

@router.get('/{ticket_id}')
def get_review(ticket_id: str, user=Depends(principal)):
    admin_only(user)
    with connect() as c:
        row=c.execute('SELECT * FROM refund_reviews WHERE ticket_id=?',(ticket_id,)).fetchone()
        if not row: raise HTTPException(404,'Refund review not found')
        return dict(row)


@router.post('/{ticket_id}/reconcile-amount')
def reconcile_amount(ticket_id: str, body: Reconciliation, user=Depends(principal)):
    """Explicit human reconciliation of conflicting claims; never executes refunds."""
    from decimal import Decimal
    admin_only(user)
    amount = Decimal(str(body.amount))
    if not amount.is_finite():
        raise HTTPException(422, 'Finite amount required')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        review = c.execute('SELECT status,version FROM refund_reviews WHERE ticket_id=?',
                           (ticket_id,)).fetchone()
        if not review:
            raise HTTPException(404, 'Refund review not found')
        if review['status'] != 'PENDING' or review['version'] != body.version:
            raise HTTPException(409, 'Review already decided or stale version')
        ticket = c.execute('SELECT risk FROM tickets WHERE id=?', (ticket_id,)).fetchone()
        if not ticket:
            raise HTTPException(404, 'Ticket not found')
        flags = set(filter(None, (ticket['risk'] or '').split(',')))
        flags.discard('REFUND_AMOUNT_UNVERIFIED')
        c.execute('UPDATE tickets SET risk=? WHERE id=?',
                  (','.join(sorted(flags)), ticket_id))
        c.execute('UPDATE refund_reviews SET amount=?,version=version+1 WHERE ticket_id=?',
                  (str(amount), ticket_id))
        # Any previous assessment must be repeated after reconciliation.
        c.execute('DELETE FROM refund_evidence_assessments WHERE ticket_id=?', (ticket_id,))
        audit(c, ticket_id, 'REFUND_AMOUNT_RECONCILED')
    return {'reconciled': True, 'version': body.version + 1,
            'execution_status': 'NOT_CONFIGURED'}

@router.post('/{ticket_id}/assessment')
def assess(ticket_id: str, body: Assessment, user=Depends(principal)):
    """Human evidence assessment; never infer sufficiency from upload count."""
    import json
    admin_only(user)
    if len(body.evidence_ids) != len(set(body.evidence_ids)):
        raise HTTPException(422, 'Duplicate evidence identifiers')
    if body.claim_type == 'DAMAGED_DEFECTIVE':
        if body.evidence_status == 'NOT_REQUIRED':
            raise HTTPException(422, 'Damaged or defective claims require evidence assessment')
        if body.evidence_status == 'ACCEPTED' and not body.evidence_ids:
            raise HTTPException(422, 'Accepted evidence identifiers required')
    elif body.evidence_status != 'NOT_REQUIRED':
        raise HTTPException(422, 'OTHER claims require NOT_REQUIRED evidence status')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        review=c.execute('SELECT status,version FROM refund_reviews WHERE ticket_id=?',(ticket_id,)).fetchone()
        if not review: raise HTTPException(404,'Refund review not found')
        if review['status']!='PENDING' or review['version']!=body.version:
            raise HTTPException(409,'Review already decided or stale version')
        if body.claim_type == 'OTHER' and known_damage_claim(c, ticket_id):
            raise HTTPException(422, 'Damage or defect claim cannot be assessed as OTHER')
        for eid in body.evidence_ids:
            if not c.execute('SELECT 1 FROM ticket_evidence WHERE ticket_id=? AND id=?',(ticket_id,eid)).fetchone():
                raise HTTPException(422,'Evidence does not belong to this ticket')
        if body.claim_type == 'DAMAGED_DEFECTIVE' and body.evidence_status == 'ACCEPTED' \
                and not valid_image_evidence(c, ticket_id, body.evidence_ids):
            raise HTTPException(422, 'A valid image is required for damaged or defective claims')
        c.execute("""INSERT INTO refund_evidence_assessments
          (ticket_id,claim_type,evidence_status,evidence_ids,reason,reviewer_id,assessed_at,version)
          VALUES (?,?,?,?,?,?,?,0)
          ON CONFLICT(ticket_id) DO UPDATE SET
          claim_type=excluded.claim_type,evidence_status=excluded.evidence_status,
          evidence_ids=excluded.evidence_ids,reason=excluded.reason,
          reviewer_id=excluded.reviewer_id,assessed_at=excluded.assessed_at,
          version=refund_evidence_assessments.version+1""",
          (ticket_id,body.claim_type,body.evidence_status,json.dumps(body.evidence_ids),
           body.reason,user['id'],time.time()))
        audit(c,ticket_id,'REFUND_EVIDENCE_ASSESSED')
    return {'assessed':True,'execution_status':'NOT_CONFIGURED'}

def approval_blockers(c,ticket_id):
    """Fail closed on absent order, absent human assessment or stale evidence."""
    import json
    ticket = c.execute('SELECT risk FROM tickets WHERE id=?', (ticket_id,)).fetchone()
    if not ticket or 'REFUND_AMOUNT_UNVERIFIED' in set(filter(None, (ticket['risk'] or '').split(','))):
        return 'Refund amount requires explicit human reconciliation'
    link=c.execute("""SELECT 1 FROM ticket_order_links l
      JOIN tickets t ON t.id=l.ticket_id AND t.customer_id=l.customer_id
      JOIN demo_orders o ON o.order_code=l.order_code AND o.customer_id=l.customer_id
      WHERE l.ticket_id=?""",(ticket_id,)).fetchone()
    if not link:
        return 'Verified customer-owned order required'
    a=c.execute('SELECT * FROM refund_evidence_assessments WHERE ticket_id=?',(ticket_id,)).fetchone()
    if not a:
        return 'Human claim and evidence assessment required'
    # Customer follow-up after an assessment may change the evidence needed.
    newer_damage = c.execute(
        "SELECT content FROM messages WHERE ticket_id=? AND role='customer' AND created_at>?",
        (ticket_id, a['assessed_at'])).fetchall()
    if any(DAMAGE_TERMS.search(m['content'] or '') for m in newer_damage):
        return 'New damage allegation requires renewed human evidence assessment'
    if known_damage_claim(c, ticket_id) and a['claim_type'] != 'DAMAGED_DEFECTIVE':
        return 'Damage or defect claim requires image evidence assessment'
    if a['claim_type']=='DAMAGED_DEFECTIVE':
        if a['evidence_status']!='ACCEPTED':
            return 'Accepted supporting evidence required'
        ids=json.loads(a['evidence_ids'])
        if not ids:
            return 'Accepted supporting evidence required'
        if not valid_image_evidence(c, ticket_id, ids):
            return 'Valid image evidence no longer available'
    elif a['claim_type']=='OTHER' and a['evidence_status']!='NOT_REQUIRED':
        return 'Invalid evidence assessment'
    else:
        if a['claim_type'] not in ('OTHER','DAMAGED_DEFECTIVE'):
            return 'Invalid claim assessment'
    return None

@router.post('/{ticket_id}/decision')
def decide(ticket_id: str, body: Decision, user=Depends(principal)):
    admin_only(user)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM refund_reviews WHERE ticket_id=?',(ticket_id,)).fetchone()
        if not row: raise HTTPException(404,'Refund review not found')
        if row['status']!='PENDING' or row['version']!=body.version:
            raise HTTPException(409,'Review already decided or stale version')
        if body.decision == 'APPROVED':
            blocker=approval_blockers(c,ticket_id)
            if blocker: raise HTTPException(409,blocker)
        result=c.execute('''UPDATE refund_reviews SET status=?,version=version+1,
          decided_at=?,reviewer_id=?,reason=? WHERE ticket_id=? AND status='PENDING' AND version=?''',
          (body.decision,time.time(),user['id'],body.reason,ticket_id,body.version))
        if result.rowcount!=1: raise HTTPException(409,'Concurrent decision')
        audit(c,ticket_id,'REFUND_REVIEW_'+body.decision)
    return {'status':body.decision,'execution_status':'NOT_CONFIGURED',
            'message':'Review decision recorded; no refund executed.'}
