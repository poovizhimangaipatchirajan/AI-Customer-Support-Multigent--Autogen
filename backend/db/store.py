
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager


# ---------------------------------------------------------
# Database configuration
# ---------------------------------------------------------

# Environment configuration when the module is first loaded.
DEFAULT_DB = os.getenv("FIXFLOW_DB", "fixflow_local.sqlite3")

# Tests can override this using monkeypatch.setattr(store, "DB", ...).
DB = DEFAULT_DB


def database_path():
    """
    Resolve the SQLite database path.

    Supports:
    1. Tests overriding store.DB.
    2. Tests overriding FIXFLOW_DB.
    3. Normal application database configuration.
    """
    if DB != DEFAULT_DB:
        return DB

    return os.getenv("FIXFLOW_DB", DB)


# ---------------------------------------------------------
# SQLite connection management
# ---------------------------------------------------------

@contextmanager
def connect():
    connection = sqlite3.connect(
        database_path(),
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")

        yield connection
        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# ---------------------------------------------------------
# Database initialization and migrations
# ---------------------------------------------------------

def init():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            claimed_amount REAL,
            status TEXT NOT NULL,
            risk TEXT NOT NULL,
            created_at REAL NOT NULL,
            version INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL,
            started_at REAL,
            completed_at REAL,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_token TEXT,
            heartbeat REAL,
            input_version INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            visible INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit (
            id TEXT PRIMARY KEY,
            ticket_id TEXT,
            event TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS verification_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            purpose TEXT NOT NULL,
            expires_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ai_results (
            job_id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            draft TEXT NOT NULL,
            citations TEXT NOT NULL,
            requires_review INTEGER NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS public_id_sequence (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL
        );
        """)

        # Add missing columns without deleting existing records.
        migrations = {
            "tickets": {
                "version": "INTEGER NOT NULL DEFAULT 0",
                "public_id": "TEXT",
            },
            "users": {
                "public_id": "TEXT",
            },
            "jobs": {
                "attempts": "INTEGER NOT NULL DEFAULT 0",
                "lease_token": "TEXT",
                "heartbeat": "REAL",
                "input_version": "INTEGER NOT NULL DEFAULT 0",
            },
        }

        for table, columns in migrations.items():
            existing = {
                row["name"]
                for row in c.execute(
                    f"PRAGMA table_info({table})"
                )
            }

            for column, definition in columns.items():
                if column not in existing:
                    c.execute(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN {column} {definition}"
                    )

        # -------------------------------------------------
        # Permanent public IDs
        # -------------------------------------------------

        c.executescript("""
        CREATE TRIGGER IF NOT EXISTS ticket_public_id_on_insert
        AFTER INSERT ON tickets
        WHEN NEW.public_id IS NULL
        BEGIN
            INSERT INTO public_id_sequence(kind)
            VALUES ('ticket');

            UPDATE tickets
            SET public_id =
                'FF-' ||
                strftime('%Y', NEW.created_at, 'unixepoch') ||
                '-' ||
                printf('%06d', last_insert_rowid())
            WHERE id = NEW.id;
        END;

        CREATE TRIGGER IF NOT EXISTS customer_public_id_on_insert
        AFTER INSERT ON users
        WHEN NEW.public_id IS NULL
        BEGIN
            INSERT INTO public_id_sequence(kind)
            VALUES ('customer');

            UPDATE users
            SET public_id =
                'NC-' ||
                printf('%06d', last_insert_rowid())
            WHERE id = NEW.id;
        END;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_public_id
        ON tickets(public_id);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_public_id
        ON users(public_id);
        """)

        # -------------------------------------------------
        # Backfill IDs for existing records
        # -------------------------------------------------

        existing_tickets = c.execute(
            """
            SELECT id, created_at
            FROM tickets
            WHERE public_id IS NULL
            ORDER BY created_at, id
            """
        ).fetchall()

        for row in existing_tickets:
            cursor = c.execute(
                """
                INSERT INTO public_id_sequence(kind)
                VALUES ('ticket')
                """
            )

            year = time.strftime(
                "%Y",
                time.gmtime(row["created_at"]),
            )

            public_id = (
                f"FF-{year}-{cursor.lastrowid:06d}"
            )

            c.execute(
                """
                UPDATE tickets
                SET public_id = ?
                WHERE id = ?
                """,
                (public_id, row["id"]),
            )

        existing_users = c.execute(
            """
            SELECT id
            FROM users
            WHERE public_id IS NULL
            ORDER BY rowid
            """
        ).fetchall()

        for row in existing_users:
            cursor = c.execute(
                """
                INSERT INTO public_id_sequence(kind)
                VALUES ('customer')
                """
            )

            public_id = (
                f"NC-{cursor.lastrowid:06d}"
            )

            c.execute(
                """
                UPDATE users
                SET public_id = ?
                WHERE id = ?
                """,
                (public_id, row["id"]),
            )

    # Phase 8E: create refund reviews in every newly initialized database.
    # Import locally to avoid circular imports during module loading.
    from backend.services.refund_approvals import migrate
    migrate()


# ---------------------------------------------------------
# Audit logging
# ---------------------------------------------------------

def audit(c, t, e):
    c.execute(
        """
        INSERT INTO audit (
            id,
            ticket_id,
            event,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            t,
            e,
            time.time(),
        ),
    )
