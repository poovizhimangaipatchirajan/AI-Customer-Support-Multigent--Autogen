"""Authenticated, private ticket evidence. No automated refund/replacement execution."""
import base64
import binascii
import hashlib
import os
from pathlib import Path
import time
import uuid
import io
from PIL import Image, UnidentifiedImageError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from backend.db.store import connect
from backend.auth.security import principal

router = APIRouter(tags=['Ticket evidence'])
MAX_BYTES = 2 * 1024 * 1024
ALLOWED = {'image/jpeg': '.jpg', 'image/png': '.png', 'application/pdf': '.pdf'}

class EvidenceIn(BaseModel):
    filename: str = Field(min_length=1, max_length=150)
    media_type: str
    content_base64: str = Field(min_length=4, max_length=3_000_000)

def migrate():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS ticket_evidence (
            id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id),
            customer_id TEXT NOT NULL, original_name TEXT NOT NULL,
            media_type TEXT NOT NULL, byte_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL, storage_name TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_ticket_evidence_ticket ON ticket_evidence(ticket_id)')

def root():
    path = Path(os.getenv('FIXFLOW_EVIDENCE_DIR', 'private_evidence')).resolve()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path

def authorized(db, ticket_id, user):
    row = db.execute('SELECT customer_id FROM tickets WHERE id=?', (ticket_id,)).fetchone()
    if not row or (user['role'] != 'admin' and row['customer_id'] != user['id']):
        raise HTTPException(404, 'Ticket not found')
    return row

def sniff(data, media_type):
    if media_type in ('image/png', 'image/jpeg'):
        expected = 'PNG' if media_type == 'image/png' else 'JPEG'
        try:
            with Image.open(io.BytesIO(data)) as img:
                if img.format != expected:
                    return False
                img.verify()
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                return img.format == expected and img.width > 0 and img.height > 0
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            return False
    if media_type == 'application/pdf':
        return data.startswith(b'%PDF-') and b'%%EOF' in data[-1024:]
    return False

@router.post('/tickets/{ticket_id}/evidence', status_code=201)
def upload(ticket_id: str, body: EvidenceIn, user=Depends(principal)):
    if user['role'] != 'customer' or not user['verified']:
        raise HTTPException(403, 'Verified customer required')
    if body.media_type not in ALLOWED:
        raise HTTPException(422, 'Only JPEG, PNG and PDF are supported')
    name = Path(body.filename.replace('\\', '/')).name
    if not name or name in ('.', '..') or '\x00' in name or not name.lower().endswith(ALLOWED[body.media_type]):
        raise HTTPException(422, 'Invalid filename or extension')
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, 'Invalid file encoding')
    if not data or len(data) > MAX_BYTES or not sniff(data, body.media_type):
        raise HTTPException(422, 'Invalid file content or size (maximum 2 MB)')
    with connect() as db:
        authorized(db, ticket_id, user)
        status = db.execute('SELECT status FROM tickets WHERE id=?', (ticket_id,)).fetchone()
        if status['status'] == 'RESOLVED':
            raise HTTPException(409, 'Resolved tickets cannot accept new evidence')
        count = db.execute('SELECT COUNT(*) FROM ticket_evidence WHERE ticket_id=?', (ticket_id,)).fetchone()[0]
        if count >= 5:
            raise HTTPException(422, 'Maximum five evidence files per ticket')
        eid = str(uuid.uuid4())
        stored = eid + ALLOWED[body.media_type]
        path = root() / stored
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
            db.execute('INSERT INTO ticket_evidence VALUES (?,?,?,?,?,?,?,?,?)',
                       (eid, ticket_id, user['id'], name, body.media_type, len(data),
                        hashlib.sha256(data).hexdigest(), stored, time.time()))
        except Exception:
            path.unlink(missing_ok=True)
            raise
    # Photos arriving after the agents finished may complete a low-value
    # automatic acceptance. Serialized; any failure leaves the ticket as is.
    auto_accepted = False
    try:
        from backend.services.auto_refund import accept_after_evidence
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            auto_accepted = accept_after_evidence(db, ticket_id)
    except Exception:
        auto_accepted = False
    return {'id': eid, 'filename': name, 'byte_size': len(data), 'stored': True,
            'auto_refund_accepted': auto_accepted}

@router.get('/tickets/{ticket_id}/evidence')
def list_evidence(ticket_id: str, user=Depends(principal)):
    with connect() as c:
        # The ticket owner and administrators may list metadata; other customers
        # receive the same 404 as for an unknown ticket.
        authorized(c, ticket_id, user)
        rows = c.execute('SELECT id,original_name,media_type,byte_size,created_at FROM ticket_evidence WHERE ticket_id=? ORDER BY created_at', (ticket_id,)).fetchall()
        return [dict(row) for row in rows]

@router.get('/tickets/{ticket_id}/evidence/{evidence_id}')
def download(ticket_id: str, evidence_id: str, user=Depends(principal)):
    with connect() as db:
        authorized(db, ticket_id, user)
        item = db.execute('SELECT * FROM ticket_evidence WHERE id=? AND ticket_id=?',
                          (evidence_id, ticket_id)).fetchone()
        if not item:
            raise HTTPException(404, 'Evidence not found')
        path = root() / item['storage_name']
        if not path.is_file():
            raise HTTPException(404, 'Evidence file unavailable')
        return FileResponse(path, media_type='application/octet-stream',
                            filename=item['original_name'], content_disposition_type='attachment',
                            headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})
