import uuid,time,re,secrets,csv,io,json,sqlite3,logging
from fastapi import FastAPI,Depends,HTTPException,Header
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / '.env')
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field
from backend.db.store import init,connect,audit
from backend.auth.security import hash_password,verify_password,create_session,principal,admin_only,digest,revoke
from backend.auth.mailer import send_token
from backend.services.risk import screen, REFUND, AMOUNT, CURRENCY_CONTEXT, _money, refund_amount_from_text
from backend.services.refund_approvals import router as refund_review_router, migrate as migrate_refund_reviews, register_pending
from backend.services.help_center import router as help_router
from backend.services.customer_orders import router as customer_orders_router, migrate as migrate_customer_orders
from backend.services.ticket_evidence import router as evidence_router, migrate as migrate_evidence

logger = logging.getLogger('fixflow.auth')
app=FastAPI(title='FixFlow AI local build',version='0.6.0')


app.include_router(help_router)
app.include_router(customer_orders_router)
app.include_router(evidence_router)
app.include_router(refund_review_router)
from backend.services_rubric_logging import router as rubric_router, migrate as migrate_rubric
app.include_router(rubric_router)
@app.on_event('startup')
def startup():
    init()
    migrate_customer_orders()
    migrate_evidence()
    migrate_refund_reviews()
    migrate_rubric()
class Credentials(BaseModel):
 email:str=Field(min_length=5,max_length=254)
 password:str=Field(min_length=12,max_length=128)

class RegistrationIn(Credentials):
 # Optional for compatibility with existing API clients/tests.
 # Streamlit requires full_name for new registrations.
 full_name:str|None=None
 phone:str|None=None

NAME_PATTERN = re.compile(r"^[^\W\d_]+(?:[\s.'’-][^\W\d_]+)*$", re.UNICODE)
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
PHONE_PATTERN = re.compile(r"^(?:\+91)?[6-9]\d{9}$")

def validate_registration(email, password, full_name=None, phone=None):
    email = email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email) or len(email) > 254 or '..' in email:
        raise HTTPException(422, 'Enter a valid email address')
    if len(password) < 12 or len(password) > 128 or not all((
        re.search(r'[A-Z]', password),
        re.search(r'[a-z]', password),
        re.search(r'\d', password),
        re.search(r'[^A-Za-z0-9\s]', password),
    )):
        raise HTTPException(422, 'Password must be 12–128 characters and include uppercase, lowercase, number and special character')
    if full_name is not None:
        full_name = ' '.join(full_name.split())
        if not 2 <= len(full_name) <= 100 or not NAME_PATTERN.fullmatch(full_name):
            raise HTTPException(422, 'Full name must be 2–100 characters and contain valid name characters')
    if phone is not None:
        phone = re.sub(r'[\s-]', '', phone)
        if not phone:
            phone = None
        elif not PHONE_PATTERN.fullmatch(phone):
            raise HTTPException(422, 'Enter a valid Indian mobile number (10 digits or +91 followed by 10 digits)')
        else:
            phone = '+91' + phone[-10:]
    return email, full_name, phone
class EmailIn(BaseModel):email:str
class TokenIn(BaseModel):token:str
class ResetIn(BaseModel):token:str;password:str=Field(min_length=12,max_length=128)
class TicketIn(BaseModel):
 subject:str=Field(min_length=3,max_length=150)
 description:str=Field(min_length=10,max_length=5000)
 claimed_amount:float|None=Field(default=None,ge=0)
 order_code:str|None=Field(default=None,max_length=100)
class ReplyIn(BaseModel):content:str=Field(min_length=2,max_length=5000)
class DecisionIn(BaseModel):response:str=Field(min_length=2,max_length=5000);version:int=Field(ge=0)
class CloseTicketIn(BaseModel):
 reason: str = Field(min_length=10, max_length=1000)
 version: int = Field(ge=0)
@app.get('/health')
def health():return {'status':'ok','version':'0.6.0-local'}
# Fixed-window throttling, per process. Persistent multi-worker throttling is a future hardening item.
from collections import defaultdict, deque
from threading import Lock
_auth_windows = defaultdict(deque)
_auth_lock = Lock()
def throttle(action, key, limit=5, period=900):
    now = time.monotonic()
    with _auth_lock:
        q = _auth_windows[(action, key.strip().lower())]
        while q and q[0] < now-period:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, 'Too many requests. Try again later.')
        q.append(now)

def issue_token(uid, purpose, email=None):
    if purpose not in ('verify', 'reset'):
        raise ValueError('Invalid token purpose')
    token = secrets.token_urlsafe(32)
    if email is None:
        with connect() as c:
            row = c.execute('SELECT email FROM users WHERE id=?', (uid,)).fetchone()
        if not row:
            raise RuntimeError('Account not found')
        email = row['email']
    # Fail closed: never issue a usable token if email cannot be delivered.
    try:
        send_token(email, purpose, token)
    except Exception as exc:
        # Log exception class only: SMTP exceptions may contain addresses or server text.
        logger.error('Email delivery failed: purpose=%s exception_type=%s', purpose, type(exc).__name__)
        raise HTTPException(503, 'Verification email unavailable; try again later') from exc
    with connect() as c:
        c.execute('DELETE FROM verification_tokens WHERE user_id=? AND purpose=?', (uid, purpose))
        c.execute('INSERT INTO verification_tokens VALUES (?,?,?,?)',
                  (digest(token), uid, purpose, time.time()+1800))
    return True
@app.post('/auth/register',status_code=201)
def register(body:RegistrationIn):
 email,full_name,phone=validate_registration(body.email,body.password,body.full_name,body.phone)
 throttle('register', email)
 uid=str(uuid.uuid4())
 migrate_customer_orders()
 try:
  with connect() as c:
   c.execute('INSERT INTO users(id,email,password_hash,role,verified,full_name,phone) VALUES (?,?,?,?,?,?,?)',
             (uid,email,hash_password(body.password),'customer',0,full_name,phone))
 except sqlite3.IntegrityError as exc:
  if 'UNIQUE' in str(exc).upper():raise HTTPException(409,'Email already registered')
  raise
 issue_token(uid,'verify',email)
 return {'message':'Registered. Check your email for a verification code.'}
@app.post('/auth/verification/resend')
def resend_verification(body:EmailIn):
    email = body.email.strip().lower()
    throttle('resend-verify', email, limit=3)
    with connect() as c:
        row = c.execute('SELECT id, verified FROM users WHERE email=?', (email,)).fetchone()
    # Generic response prevents account enumeration.
    if row and not row['verified']:
        issue_token(row['id'], 'verify', email)
    return {'message':'If this account needs verification, a new code was sent.'}

@app.post('/auth/verify')
def verify(body:TokenIn):
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT user_id FROM verification_tokens WHERE token_hash=? AND purpose=? AND expires_at>?',(digest(body.token),'verify',time.time())).fetchone()
  if not r:raise HTTPException(400,'Invalid/expired verification token')
  c.execute('UPDATE users SET verified=1 WHERE id=?',(r['user_id'],));c.execute('DELETE FROM verification_tokens WHERE token_hash=?',(digest(body.token),))
 return {'verified':True}
@app.post('/auth/password-reset/request')
def reset_request(body:EmailIn):
 throttle('reset',body.email)
 with connect() as c:r=c.execute('SELECT id FROM users WHERE email=?',(body.email.strip().lower(),)).fetchone()
 if r:issue_token(r['id'],'reset')
 return {'message':'If the account exists, a reset code was sent by email.'}
@app.post('/auth/password-reset/confirm')
def reset_confirm(body:ResetIn):
 validate_registration('reset@example.com',body.password)
 throttle('reset-confirm',digest(body.token),limit=8)
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT user_id FROM verification_tokens WHERE token_hash=? AND purpose=? AND expires_at>?',(digest(body.token),'reset',time.time())).fetchone()
  if not r:raise HTTPException(400,'Invalid/expired reset token')
  c.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(body.password),r['user_id']))
  c.execute('DELETE FROM sessions WHERE user_id=?',(r['user_id'],));c.execute('DELETE FROM verification_tokens WHERE token_hash=?',(digest(body.token),))
 return {'reset':True}
@app.post('/auth/login')
def login(body:Credentials):
 throttle('login',body.email,limit=10)
 with connect() as c:r=c.execute('SELECT * FROM users WHERE email=?',(body.email.strip().lower(),)).fetchone()
 if not r or not verify_password(r['password_hash'],body.password):raise HTTPException(401,'Invalid credentials')
 if r['role'] == 'customer' and not r['verified']:
  raise HTTPException(403,'Email verification required. Request a new verification code.')
 return {'access_token':create_session(r['id']),'role':r['role'],'verified':bool(r['verified']),'customer_code':r['public_id']}
@app.post('/auth/logout')
def logout(user=Depends(principal),authorization:str|None=Header(default=None)):
 revoke(authorization[7:]);return {'logged_out':True}
@app.get('/auth/me')
def me(user=Depends(principal)):
 with connect() as c:
  r=c.execute('SELECT public_id FROM users WHERE id=?',(user['id'],)).fetchone()
 return {**user,'customer_code':r['public_id'] if r else None}
@app.post('/tickets',status_code=201)
def create_ticket(body:TicketIn,user=Depends(principal)):
 if user['role']!='customer':raise HTTPException(403,'Customer required')
 if not user['verified']:raise HTTPException(403,'Verify email first')
 if body.order_code:
  migrate_customer_orders()
  with connect() as c:
   match=c.execute('SELECT id FROM demo_orders WHERE order_code=? AND customer_id=?',(body.order_code,user['id'])).fetchone()
  if not match:raise HTTPException(422,'Related order not found for this customer')
 tid=str(uuid.uuid4());jid=str(uuid.uuid4());now=time.time()
 # Only use an unambiguous amount tied to the word 'refund'; never an order ID.
 parsed_amount = refund_amount_from_text(body.subject+' '+body.description)
 effective_amount = body.claimed_amount if body.claimed_amount is not None else (float(parsed_amount) if parsed_amount is not None else None)
 flags=screen(body.subject+' '+body.description,effective_amount)
 # An explicitly requested refund >= ₹500 requires review even when
 # conflicting amounts make the claim unverified. Never resolve conflicts here.
 if (REFUND.search(body.subject+' '+body.description) or parsed_amount is not None):
  amounts = [_money(effective_amount), parsed_amount]
  if any(v is not None and v >= 500 for v in amounts):
   flags = sorted(set(flags) | {'REFUND_GE_500'})
 status='ESCALATED' if flags else 'QUEUED'
 with connect() as c:
  c.execute('INSERT INTO tickets(id,customer_id,subject,description,claimed_amount,status,risk,created_at) VALUES (?,?,?,?,?,?,?,?)',(tid,user['id'],body.subject,body.description,effective_amount,status,','.join(flags),now))
  if body.order_code:c.execute('INSERT INTO ticket_order_links(ticket_id,order_code,customer_id) VALUES (?,?,?)',(tid,body.order_code,user['id']))
  c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),tid,'customer',body.description,1,now))
  if not flags:c.execute('INSERT INTO jobs(id,ticket_id,status,created_at,input_version) VALUES (?,?,?,?,?)',(jid,tid,'QUEUED',now,0))
  register_pending(c,tid,effective_amount,flags)
  audit(c,tid,'CREATED:'+status)
 with connect() as c:
  code=c.execute('SELECT public_id FROM tickets WHERE id=?',(tid,)).fetchone()['public_id']
 return {'id':tid,'ticket_code':code,'status':status,'risk_flags':flags}
def owned(c,tid,user):
 r=c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
 if not r or (user['role']!='admin' and r['customer_id']!=user['id']):raise HTTPException(404,'Ticket not found')
 return r
@app.get('/tickets')
def tickets(user=Depends(principal)):
 with connect() as c:
  if user['role']=='admin':r=c.execute('SELECT t.*,u.public_id AS customer_code FROM tickets t JOIN users u ON u.id=t.customer_id ORDER BY t.created_at DESC LIMIT 200').fetchall()
  else:r=c.execute('SELECT t.*,u.public_id AS customer_code FROM tickets t JOIN users u ON u.id=t.customer_id WHERE t.customer_id=? ORDER BY t.created_at DESC LIMIT 100',(user['id'],)).fetchall()
 return [dict(x) for x in r]
@app.get('/tickets/{ticket_id}')
def ticket(ticket_id:str,user=Depends(principal)):
 migrate_customer_orders()
 migrate_evidence()
 with connect() as c:
  r=owned(c,ticket_id,user);data=dict(r)
  customer=c.execute('SELECT public_id FROM users WHERE id=?',(r['customer_id'],)).fetchone()
  data['customer_code']=customer['public_id'] if customer else None
  link=c.execute('SELECT order_code FROM ticket_order_links WHERE ticket_id=?',(ticket_id,)).fetchone()
  data['related_order_code']=link['order_code'] if link else None
  data['evidence']=[dict(x) for x in c.execute('SELECT id,original_name,media_type,byte_size,created_at FROM ticket_evidence WHERE ticket_id=? ORDER BY created_at',(ticket_id,)).fetchall()]
  from backend.services.citation_catalog import resolve
  data['messages'] = []
  for m in c.execute('SELECT id,role,content,created_at FROM messages WHERE ticket_id=? AND visible=1 ORDER BY created_at',(ticket_id,)).fetchall():
    entry = {'role': m['role'], 'content': m['content'], 'created_at': m['created_at'], 'citations': []}
    # Worker uses job_id as assistant message id; match exact result, not latest ticket.
    if m['role'] == 'assistant':
     result = c.execute('SELECT citations FROM ai_results WHERE job_id=? AND ticket_id=?',(m['id'],ticket_id)).fetchone()
     if result:
      try:
       ids = json.loads(result['citations'])
       if isinstance(ids,list): entry['citations'] = resolve(ids)
      except (ValueError,TypeError):
       pass
    data['messages'].append(entry)
  if user['role']=='admin':data['drafts']=[dict(x) for x in c.execute('SELECT draft,citations,requires_review FROM ai_results WHERE ticket_id=?',(ticket_id,))]
 return data
@app.get('/tickets/{ticket_id}/refund-review')
def customer_refund_review_status(ticket_id: str, user=Depends(principal)):
    """Owner/admin-safe decision summary. Never expose internal reviewer IDs or private notes."""
    with connect() as c:
        owned(c, ticket_id, user)
        row = c.execute("SELECT status, amount, decided_at, execution_status FROM refund_reviews WHERE ticket_id=?", (ticket_id,)).fetchone()
    if row is None:
        return {'has_review': False}
    return {'has_review': True, 'status': row['status'], 'amount': row['amount'],
            'decided_at': row['decided_at'], 'execution_status': row['execution_status']}

@app.post('/tickets/{ticket_id}/reply')
def reply(ticket_id: str, body: ReplyIn, user=Depends(principal)):
    """Queue ordinary customer follow-ups; keep risky tickets under human review."""
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        ticket = owned(c, ticket_id, user)
        if ticket['status'] == 'RESOLVED':
            raise HTTPException(409, 'Resolved tickets require a new linked follow-up')
        if user['role'] == 'customer' and not user['verified']:
            raise HTTPException(403, 'Verify email first')
        now = time.time()
        c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',
                  (str(uuid.uuid4()), ticket_id, user['role'], body.content, 1, now))
        if user['role'] == 'customer':
            # A refund first requested in a follow-up must not bypass the approval gate.
            # Prefer an explicitly stated monetary amount in the new message; never
            # interpret an order number as a refund amount.
            followup_amount = None
            if REFUND.search(body.content) or refund_amount_from_text(body.content) is not None:
                values = {_money(m.group(1)) for m in AMOUNT.finditer(body.content)
                          if CURRENCY_CONTEXT.search(m.group(0))}
                values.discard(None)
                short_amount = refund_amount_from_text(body.content)
                if short_amount is not None: values.add(short_amount)
                if len(values) == 1:
                    followup_amount = next(iter(values))
            screening_amount = (float(followup_amount) if followup_amount is not None
                                else ticket['claimed_amount'])
            flags = set(screen(body.content, screening_amount))
            if (followup_amount is not None and ticket['claimed_amount'] is not None
                    and _money(ticket['claimed_amount']) != followup_amount):
                flags.add('REFUND_AMOUNT_UNVERIFIED')
            if (REFUND.search(body.content) or refund_amount_from_text(body.content) is not None) and followup_amount is None and ticket['claimed_amount'] is None:
                flags.add('REFUND_AMOUNT_UNVERIFIED')
            # Threshold is inclusive. Preserve a high-value review even when
            # the new claim conflicts with the original claimed amount.
            if REFUND.search(body.content) or refund_amount_from_text(body.content) is not None:
                candidate_amounts = (_money(ticket['claimed_amount']), followup_amount)
                if any(v is not None and v >= 500 for v in candidate_amounts):
                    flags.add('REFUND_GE_500')
            if 'REFUND_GE_500' in flags:
                register_pending(c, ticket_id, screening_amount, flags)
            risk = set(filter(None, (ticket['risk'] or '').split(','))) | flags
            # A ticket already escalated must stay escalated until human review.
            escalated = bool(risk) or ticket['status'] == 'ESCALATED'
            status = 'ESCALATED' if escalated else 'QUEUED'
            # Supersede any old queued jobs. Running jobs are invalidated by version.
            c.execute("UPDATE jobs SET status='SUPERSEDED', completed_at=? "
                      "WHERE ticket_id=? AND status='QUEUED'", (now, ticket_id))
            c.execute('UPDATE tickets SET status=?, risk=?, version=version+1 WHERE id=?',
                      (status, ','.join(sorted(risk)), ticket_id))
            if not escalated:
                c.execute('INSERT INTO jobs(id,ticket_id,status,created_at,input_version) '
                          'VALUES (?,?,?,?,?)',
                          (str(uuid.uuid4()), ticket_id, 'QUEUED', now, ticket['version'] + 1))
            audit(c, ticket_id, 'FOLLOWUP_' + status)
        else:
            c.execute('UPDATE tickets SET status=?,version=version+1 WHERE id=?',
                      ('AWAITING_CUSTOMER', ticket_id))
            audit(c, ticket_id, 'ADMIN_REPLY_ADDED')
    return {'status': status if user['role'] == 'customer' else 'AWAITING_CUSTOMER'}
@app.get('/admin/queue')
def queue(user=Depends(principal)):
 admin_only(user)
 with connect() as c:return [dict(x) for x in c.execute("SELECT t.*,u.public_id AS customer_code FROM tickets t JOIN users u ON u.id=t.customer_id WHERE t.status='ESCALATED' ORDER BY t.created_at LIMIT 200")]
@app.get('/admin/audit/{ticket_id}')
def audits(ticket_id:str,user=Depends(principal)):
 admin_only(user)
 with connect() as c:return [dict(x) for x in c.execute('SELECT event,created_at FROM audit WHERE ticket_id=? ORDER BY created_at',(ticket_id,))]
@app.post('/admin/tickets/{ticket_id}/publish')
def publish(ticket_id:str,body:DecisionIn,user=Depends(principal)):
 admin_only(user)
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');r=owned(c,ticket_id,user)
  if r['status']!='ESCALATED':raise HTTPException(409,'Not in review')
  if r['version']!=body.version:raise HTTPException(409,'Stale review')
  c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),ticket_id,'admin',body.response,1,time.time()))
  c.execute("UPDATE tickets SET status='ANSWERED',version=version+1 WHERE id=?",(ticket_id,));audit(c,ticket_id,'ADMIN_PUBLISHED')
 return {'status':'ANSWERED'}
@app.post('/tickets/{ticket_id}/resolve')
def customer_resolve_ticket(ticket_id: str, body: CloseTicketIn, user=Depends(principal)):
    """Ticket owner confirms support resolution; never executes a payment."""
    if user['role'] != 'customer' or not user['verified']:
        raise HTTPException(403, 'Verified customer required')
    reason = body.reason.strip()
    if len(reason) < 10:
        raise HTTPException(422, 'Please provide at least 10 characters')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        r = owned(c, ticket_id, user)
        if r['version'] != body.version:
            raise HTTPException(409, 'Stale ticket version')
        if r['status'] != 'ANSWERED':
            raise HTTPException(409, 'A reviewed support response must be published before resolution')
        review = c.execute('SELECT status FROM refund_reviews WHERE ticket_id=?', (ticket_id,)).fetchone()
        if review and review['status'] == 'PENDING':
            raise HTTPException(409, 'Refund review is still pending')
        if 'REFUND_GE_500' in set(filter(None, (r['risk'] or '').split(','))) and not review:
            raise HTTPException(409, 'Mandatory refund review is missing')
        if 'REFUND_AMOUNT_UNVERIFIED' in set(filter(None, (r['risk'] or '').split(','))):
            raise HTTPException(409, 'Refund amount requires human verification before resolution')
        updated = c.execute("UPDATE tickets SET status='RESOLVED',version=version+1 WHERE id=? AND customer_id=? AND version=? AND status='ANSWERED'",
                            (ticket_id, user['id'], body.version))
        if updated.rowcount != 1:
            raise HTTPException(409, 'Concurrent ticket update')
        audit(c, ticket_id, 'CUSTOMER_CONFIRMED_RESOLUTION: ' + reason)
    return {'status': 'RESOLVED', 'payment_executed': False,
            'message': 'Support case resolved by customer. No payment was executed.'}

@app.get('/admin/export.csv')
def export(user=Depends(principal)):
 admin_only(user)
 with connect() as c:rows=[dict(r) for r in c.execute('SELECT id,customer_id,subject,status,risk,created_at FROM tickets ORDER BY created_at')]
 stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=['id','customer_id','subject','status','risk','created_at']);writer.writeheader()
 for row in rows:
  writer.writerow({k:("'"+str(v) if str(v).startswith(('=','+','-','@')) else v) for k,v in row.items()})
 return StreamingResponse(iter([stream.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=fixflow_tickets.csv'})

@app.get('/tickets/{ticket_id}/citations')
def ticket_citations(ticket_id:str, user=Depends(principal)):
    from backend.services.citation_catalog import resolve
    with connect() as c:
        owned(c, ticket_id, user)
        result = c.execute('SELECT citations FROM ai_results WHERE ticket_id=? ORDER BY created_at DESC LIMIT 1', (ticket_id,)).fetchone()
    ids = json.loads(result['citations']) if result else []
    return {'citations': resolve(ids)}
