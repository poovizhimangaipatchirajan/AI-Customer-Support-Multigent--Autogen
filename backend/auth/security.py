import secrets
import hashlib
import time
from fastapi import Header, HTTPException
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from backend.db.store import connect

ph = PasswordHasher()


def digest(s):
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(p):
    return ph.hash(p)


def verify_password(h, p):
    try:
        return ph.verify(h, p)
    except (VerificationError, ValueError):
        return False


def create_session(user_id):
    token = secrets.token_urlsafe(40)
    with connect() as c:
        c.execute(
            "INSERT INTO sessions VALUES (?,?,?)",
            (digest(token), user_id, time.time() + 86400),
        )
    return token


def revoke(token):
    with connect() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))


def principal(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login required")
    with connect() as c:
        row = c.execute(
            "SELECT u.id,u.role,u.verified FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
            (digest(authorization[7:]), time.time()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid or expired session")
    return dict(row)


def admin_only(user):
    if user["role"] != "admin":
        raise HTTPException(403, "Admin required")
