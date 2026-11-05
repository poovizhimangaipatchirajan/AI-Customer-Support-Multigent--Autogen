
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # run as: python scripts/<name>.py
import getpass
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from backend.db.store import init, connect
from backend.auth.security import hash_password


def main():
    if os.getenv("FIXFLOW_LOCAL_DEMO") != "1":
        raise RuntimeError(
            "Admin bootstrap is permitted only in local demo mode."
        )

    init()

    email = input("Admin test email: ").strip().lower()
    if not email or "@" not in email:
        raise ValueError("Enter a valid test email.")

    password = getpass.getpass(
        "Admin password (12+ characters): "
    )

    if len(password) < 12 or len(password) > 128:
        raise ValueError(
            "Password must contain 12 to 128 characters."
        )

    with connect() as db:
        existing = db.execute(
            "SELECT id FROM users WHERE email=?",
            (email,)
        ).fetchone()

        if existing:
            raise RuntimeError(
                "This email is already registered. "
                "Use a new dedicated admin test email."
            )

        db.execute(
            """
            INSERT INTO users(id, email, password_hash, role, verified)
    	    VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                email,
                hash_password(password),
                "admin",
                1,
            )
        )

    print("Local admin test account created successfully.")
    print("Email:", email)


if __name__ == "__main__":
    main()
