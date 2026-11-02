
"""Regression: a resolved ticket rejects evidence writes but keeps history readable."""

import base64
import uuid
from io import BytesIO

from PIL import Image
from fastapi.testclient import TestClient

from backend.db import store
from backend.auth.security import hash_password
from backend.main import app


def test_resolved_ticket_rejects_direct_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB', str(tmp_path / 'evidence.sqlite3'))
    monkeypatch.setenv(
        'FIXFLOW_EVIDENCE_DIR',
        str(tmp_path / 'private_evidence')
    )

    with TestClient(app) as client:
        uid = str(uuid.uuid4())
        email = f'evidence-{uuid.uuid4().hex}@example.com'

        with store.connect() as db:
            db.execute(
                'INSERT INTO users(id,email,password_hash,role,verified) '
                'VALUES (?,?,?,?,1)',
                (uid, email, hash_password('GoodPassword!234'), 'customer')
            )

        login = client.post(
            '/auth/login',
            json={
                'email': email,
                'password': 'GoodPassword!234'
            }
        )
        assert login.status_code == 200, login.text

        headers = {
            'Authorization': 'Bearer ' + login.json()['access_token']
        }

        created = client.post(
            '/tickets',
            headers=headers,
            json={
                'subject': 'General support question',
                'description': 'Please explain how to update my delivery preferences.',
            }
        )
        assert created.status_code == 201, created.text

        ticket_id = created.json()['id']

        # Simulate an already-resolved case.
        # This test exercises evidence upload restrictions,
        # not the independent resolution workflow.
        with store.connect() as db:
            db.execute(
                "UPDATE tickets SET status='RESOLVED' WHERE id=?",
                (ticket_id,)
            )

        # Generate a complete, valid PNG using Pillow.
        image_buffer = BytesIO()
        Image.new(
            "RGB",
            (2, 2),
            color="red"
        ).save(image_buffer, format="PNG")

        payload = {
            "filename": "proof.png",
            "media_type": "image/png",
            "content_base64": base64.b64encode(
                image_buffer.getvalue()
            ).decode("ascii"),
        }

        # Resolved tickets must reject new evidence.
        rejected = client.post(
            f'/tickets/{ticket_id}/evidence',
            headers=headers,
            json=payload
        )
        assert rejected.status_code == 409, rejected.text

        # Existing evidence history remains readable.
        response = client.get(
            f'/tickets/{ticket_id}/evidence',
            headers=headers
        )
        assert response.status_code == 200, response.text

        # Rejected upload must not create an evidence record.
        with store.connect() as db:
            count = db.execute(
                'SELECT COUNT(*) FROM ticket_evidence WHERE ticket_id=?',
                (ticket_id,)
            ).fetchone()[0]

        assert count == 0
