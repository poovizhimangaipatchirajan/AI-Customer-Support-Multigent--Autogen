"""Additive, admin-only, privacy-minimized rubric reporting. No model decisions here."""
import csv
import io
import json
import re
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from backend.db.store import connect
from backend.services.ticket_classification import classify_ticket
from backend.auth.security import principal, admin_only

router = APIRouter(prefix='/admin/rubric', tags=['rubric'])
AGENTS = ('QueryAgent', 'KnowledgeAgent', 'ResolutionAgent', 'LogAgent')
FIELDS = [
    "ticket_id",
    "timestamp",
    "customer_name / order_id",
    "complaint",
    "category",
    "urgency",
    "sentiment",
    "kb_sources",
    "response",
    "status",
    "outcome",
    "escalation_reason",
]

# Plain-language escalation reasons for the log; unknown codes are kept as-is.
REASONS = {
    'REFUND_GE_500': 'Refund of INR 500 or more requires human review',
    'REFUND_AMOUNT_UNVERIFIED': 'Refund amount unclear or conflicting; needs confirmation',
    'REFUND_CURRENCY_UNCERTAIN': 'Refund currency unclear; needs confirmation',
    'FRAUD': 'Possible fraud reported',
    'LEGAL': 'Legal threat or legal action mentioned',
    'PAYMENT_DISPUTE': 'Payment dispute or duplicate charge reported',
}


def readable_reasons(risk):
    return '; '.join(REASONS.get(code, code) for code in filter(None, (risk or '').split(',')))


# Assignment-level outcome derived from the authoritative ticket status.
# ANSWERED/RESOLVED have a published reply with no pending human decision.
OUTCOME = {
    'ANSWERED': 'Resolved', 'RESOLVED': 'Resolved', 'ESCALATED': 'Escalated',
    'QUEUED': 'Pending', 'PROCESSING': 'Pending', 'AWAITING_CUSTOMER': 'Awaiting customer',
}

def migrate():
    with connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS agent_events (
          id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, job_id TEXT NOT NULL,
          lease_token TEXT NOT NULL, agent TEXT NOT NULL, event TEXT NOT NULL,
          created_at REAL NOT NULL,
          UNIQUE(job_id, lease_token, agent, event)
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_agent_events_ticket ON agent_events(ticket_id, created_at)')
        c.execute('''CREATE TABLE IF NOT EXISTS agent_output_summaries (
          id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, job_id TEXT NOT NULL,
          lease_token TEXT NOT NULL, agent TEXT NOT NULL, output TEXT NOT NULL,
          created_at REAL NOT NULL, UNIQUE(job_id, lease_token, agent)
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_agent_outputs_ticket ON agent_output_summaries(ticket_id, created_at)')

def record_agent_events(job, messages):
    """Record observed speaker participation only, never message bodies or reasoning."""
    # Preserve first-observed speaker order, not the configured AGENTS order.
    # Store participation only: no message text, prompts, or private reasoning.
    observed = list(dict.fromkeys(
        source for message in messages
        if (source := getattr(message, 'source', None)) in AGENTS
    ))
    if not observed:
        return
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute('SELECT status,lease_token,input_version FROM jobs WHERE id=?', (job['id'],)).fetchone()
        ticket = c.execute('SELECT status,version FROM tickets WHERE id=?',(job['ticket_id'],)).fetchone()
        if not current or not ticket or current['status'] != 'PROCESSING' or current['lease_token'] != job['lease'] or current['input_version'] != job['version'] or ticket['version'] != job['version'] or ticket['status'] != 'PROCESSING':
            return
        # Microsecond offsets encode observed order for the existing API's
        # created_at ordering. These are recording times, not execution times.
        recorded_at = time.time()
        for position, name in enumerate(observed):
            c.execute('INSERT OR IGNORE INTO agent_events VALUES (?,?,?,?,?,?,?)',
                (str(uuid.uuid4()), job['ticket_id'], job['id'], job['lease'],
                 name, 'MESSAGE_OBSERVED', recorded_at + position * 0.000001))

def record_agent_outputs(job, messages):
    """Store bounded, admin-only agent-authored text, not tool outputs or model reasoning.

    This is a diagnostic view, not a trusted decision record. Keep only the first
    text response per named agent and never persist tool call payloads.
    """
    observed = {}
    for message in messages:
        name = getattr(message, 'source', None)
        content = getattr(message, 'content', None)
        if name not in AGENTS or name in observed or not isinstance(content, str):
            continue
        # Avoid accidentally persisting credentials embedded in model responses.
        content = re.sub(r'(?i)(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^\s,;]+',
                         r'\1=[REDACTED]', content)
        content = re.sub(r'(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,})',
                         '[REDACTED]', content)
        observed[name] = content[:1500]
    if not observed:
        return
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute('SELECT status,lease_token,input_version FROM jobs WHERE id=?', (job['id'],)).fetchone()
        ticket = c.execute('SELECT status,version FROM tickets WHERE id=?', (job['ticket_id'],)).fetchone()
        if not current or not ticket or current['status'] != 'PROCESSING' or current['lease_token'] != job['lease'] or current['input_version'] != job['version'] or ticket['version'] != job['version'] or ticket['status'] != 'PROCESSING':
            return
        now = time.time()
        for i, (agent, output) in enumerate(observed.items()):
            c.execute('INSERT OR IGNORE INTO agent_output_summaries VALUES (?,?,?,?,?,?,?)',
                      (str(uuid.uuid4()), job['ticket_id'], job['id'], job['lease'], agent, output, now + i * .000001))

@router.get('/agent-outputs/{ticket_id}')
def agent_outputs(ticket_id: str, user=Depends(principal)):
    admin_only(user)
    with connect() as c:
        if not c.execute('SELECT 1 FROM tickets WHERE id=?', (ticket_id,)).fetchone():
            raise HTTPException(404, 'Ticket not found')
        return [dict(r) for r in c.execute("SELECT agent,output,created_at,job_id FROM agent_output_summaries WHERE ticket_id=? ORDER BY created_at,id", (ticket_id,))]

def outcome_and_reason(c, ticket, risk, has_acceptances):
    """Outcome and escalation reason that say what actually happened.

    A ticket a human reviewed and answered reads 'Resolved after human review'
    with the reason prefixed 'Reviewed by support:', so a resolved row never
    looks like an open escalation.
    """
    status = ticket['status']
    reasons = readable_reasons(risk)
    if has_acceptances and c.execute('SELECT 1 FROM auto_refund_acceptances WHERE ticket_id=?',
                                     (ticket['id'],)).fetchone():
        return 'Passed automatic checks; queued for refund processing (no payment executed)', ''
    reviewed = c.execute("SELECT 1 FROM audit WHERE ticket_id=? AND event='ADMIN_PUBLISHED'",
                         (ticket['id'],)).fetchone()
    if status in ('ANSWERED', 'RESOLVED') and reviewed:
        return 'Resolved after human review', ('Reviewed by support: ' + reasons) if reasons else 'Reviewed by support'
    if status == 'ESCALATED':
        return 'Escalated', reasons or 'Policy evidence or case decision needs human review'
    return OUTCOME.get(status, status), reasons


def _rows():
    with connect() as c:
        has_acceptances = bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                         "AND name='auto_refund_acceptances'").fetchone())
        tickets = [dict(r) for r in c.execute('''SELECT t.*,u.full_name,u.public_id AS customer_code
            FROM tickets t LEFT JOIN users u ON u.id=t.customer_id ORDER BY t.created_at DESC''')]
        output = []
        for t in tickets:
            last = c.execute('''SELECT draft,citations FROM ai_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1''',(t['id'],)).fetchone()
            message = c.execute("SELECT content FROM messages WHERE ticket_id=? AND role IN ('assistant','admin') AND visible=1 ORDER BY created_at DESC LIMIT 1",(t['id'],)).fetchone()
            risk = t.get('risk') or ''
            outcome, reason = outcome_and_reason(c, t, risk, has_acceptances)
            category, urgency, sentiment = classify_ticket(
                t.get('subject'), t.get('description'), risk
            )
            citation_ids = []
            if last:
                try:
                    parsed = json.loads(last['citations'])
                    citation_ids = parsed if isinstance(parsed,list) else []
                except (ValueError,TypeError): pass
            linked = c.execute('''SELECT l.order_code FROM ticket_order_links l
                JOIN demo_orders o ON o.order_code=l.order_code AND o.customer_id=?
                WHERE l.ticket_id=? AND l.customer_id=?''', (t['customer_id'], t['id'], t['customer_id'])).fetchone()
            if citation_ids:
                from backend.services.citation_catalog import resolve as resolve_citations
                verified_sources = resolve_citations(citation_ids)
            else:
                verified_sources = []
            source_names = list(dict.fromkeys(m['source'] for m in verified_sources if m.get('source')))
            output.append({
                'ticket_id':t.get('public_id') or t['id'],
                'timestamp':time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime(t['created_at'])),
                'customer_name / order_id':(t.get('full_name') or t.get('customer_code') or 'Not provided') + ((' / ' + linked['order_code']) if linked else ''),
                'order_id':'Not recorded',
                'complaint':t.get('description') or '',
                'category':category,
                'urgency':urgency,
                'sentiment':sentiment,
                'category · urgency · sentiment':f'{category} · {urgency} · {sentiment}',
                'kb_sources':', '.join(source_names) if source_names else ('Source metadata unavailable' if citation_ids else ''),
                'response':message['content'] if message else '',
                'response_status':'Published' if message else 'No published response',
                'ai_draft':last['draft'] if last else '',
                'status':t['status'],
                'outcome':outcome,
                'escalation_reason':reason,
            })
        return [
            {field: row.get(field, "") for field in FIELDS}
            for row in output
        ]

@router.get('/complaints')
def complaints(user=Depends(principal)):
    admin_only(user)
    return _rows()

@router.get('/complaints.csv')
def complaints_csv(user=Depends(principal)):
    admin_only(user)
    buf = io.StringIO()
    writer = csv.DictWriter(
    buf,
    fieldnames=FIELDS,
    extrasaction="ignore"
    )
    writer.writeheader()
    for row in _rows():
        # Defend against spreadsheet formula injection, including whitespace prefix.
        writer.writerow({key: "'"+str(value) if re.match(r'^\s*[=+@\-\t\r]',str(value)) else value for key,value in row.items()})
    # UTF-8 BOM so spreadsheet tools decode non-ASCII text (e.g. ₹) correctly.
    return Response(content='﻿'+buf.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="fixflow_complaint_log.csv"'})

@router.get('/agent-events/{ticket_id}')
def agent_events(ticket_id:str,user=Depends(principal)):
    admin_only(user)
    with connect() as c:
        if not c.execute('SELECT 1 FROM tickets WHERE id=?',(ticket_id,)).fetchone():
            raise HTTPException(404,'Ticket not found')
        return [dict(r) for r in c.execute('''SELECT agent,event,created_at,job_id FROM agent_events
            WHERE ticket_id=? ORDER BY created_at,id''',(ticket_id,))]
