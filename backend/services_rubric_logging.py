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
    "category · urgency · sentiment",
    "kb_sources",
    "response",
    "status",
    "escalation_reason",
]

def migrate():
    with connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS agent_events (
          id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, job_id TEXT NOT NULL,
          lease_token TEXT NOT NULL, agent TEXT NOT NULL, event TEXT NOT NULL,
          created_at REAL NOT NULL,
          UNIQUE(job_id, lease_token, agent, event)
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_agent_events_ticket ON agent_events(ticket_id, created_at)')

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

def _rows():
    with connect() as c:
        tickets = [dict(r) for r in c.execute('''SELECT t.*,u.full_name,u.public_id AS customer_code
            FROM tickets t LEFT JOIN users u ON u.id=t.customer_id ORDER BY t.created_at DESC''')]
        output = []
        for t in tickets:
            last = c.execute('''SELECT draft,citations FROM ai_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1''',(t['id'],)).fetchone()
            message = c.execute("SELECT content FROM messages WHERE ticket_id=? AND role IN ('assistant','admin') AND visible=1 ORDER BY created_at DESC LIMIT 1",(t['id'],)).fetchone()
            risk = t.get('risk') or ''
            category, urgency, sentiment = classify_ticket(
                t.get('subject'), t.get('description'), risk
            )
            citation_ids = []
            if last:
                try:
                    parsed = json.loads(last['citations'])
                    citation_ids = parsed if isinstance(parsed,list) else []
                except (ValueError,TypeError): pass
            output.append({
                'ticket_id':t.get('public_id') or t['id'],
                'timestamp':time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime(t['created_at'])),
                'customer_name / order_id':t.get('full_name') or t.get('customer_code') or 'Not provided',
                'order_id':'Not recorded',
                'complaint':t.get('description') or '',
                'category':category,
                'urgency':urgency,
                'sentiment':sentiment,
                'category · urgency · sentiment':f'{category} · {urgency} · {sentiment}',
                'kb_sources':', '.join(str(x) for x in citation_ids),
                'response':message['content'] if message else '',
                'response_status':'Published' if message else 'No published response',
                'ai_draft':last['draft'] if last else '',
                'status':t['status'],
                'escalation_reason':risk or ('Evidence/agent review required' if t['status']=='ESCALATED' else ''),
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
    return Response(content=buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="fixflow_complaint_log.csv"'})

@router.get('/agent-events/{ticket_id}')
def agent_events(ticket_id:str,user=Depends(principal)):
    admin_only(user)
    with connect() as c:
        if not c.execute('SELECT 1 FROM tickets WHERE id=?',(ticket_id,)).fetchone():
            raise HTTPException(404,'Ticket not found')
        return [dict(r) for r in c.execute('''SELECT agent,event,created_at,job_id FROM agent_events
            WHERE ticket_id=? ORDER BY created_at,id''',(ticket_id,))]
