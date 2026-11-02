
import asyncio
import json
import os
import time
import uuid
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

from backend.db.store import init, connect, audit
from backend.rag.hybrid import HybridRetriever, INTERNAL_ONLY
from backend.agents.team import make_team
from backend.services.grounding_guard import check_response_grounding, check_customer_visibility
from backend.services_rubric_logging import migrate as migrate_rubric, record_agent_events

LEASE_SECONDS = 180


def recover():
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")

        expired = c.execute(
            """
            SELECT j.id, j.ticket_id, j.attempts, j.input_version,
                   t.version AS ticket_version, t.status AS ticket_status
            FROM jobs j JOIN tickets t ON t.id=j.ticket_id
            WHERE j.status='PROCESSING'
              AND (j.heartbeat IS NULL OR j.heartbeat < ?)
            """,
            (time.time() - LEASE_SECONDS,)
        ).fetchall()

        for job in expired:
            if (job['input_version'] != job['ticket_version']
                    or job['ticket_status'] != 'PROCESSING'):
                c.execute("UPDATE jobs SET status='SUPERSEDED', lease_token=NULL, "
                          "completed_at=? WHERE id=?", (time.time(), job['id']))
                continue
            if job["attempts"] >= 2:
                c.execute(
                    """
                    UPDATE jobs
                    SET status='FAILED', lease_token=NULL,
                        completed_at=?
                    WHERE id=?
                    """,
                    (time.time(), job["id"])
                )
                c.execute(
                    """
                    UPDATE tickets
                    SET status='ESCALATED', version=version+1
                    WHERE id=?
                    """,
                    (job["ticket_id"],)
                )
                audit(
                    c, job["ticket_id"],
                    "WORKER_RETRY_EXHAUSTED"
                )
            else:
                c.execute(
                    """
                    UPDATE jobs
                    SET status='QUEUED', lease_token=NULL
                    WHERE id=?
                    """,
                    (job["id"],)
                )
                c.execute(
                    """
                    UPDATE tickets
                    SET status='QUEUED'
                    WHERE id=? AND status='PROCESSING'
                    """,
                    (job["ticket_id"],)
                )


def claim():
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")

        active = c.execute(
            """
            SELECT 1 FROM jobs
            WHERE status='PROCESSING'
            LIMIT 1
            """
        ).fetchone()

        if active:
            return None

        job = c.execute(
            """
            SELECT j.id, j.ticket_id, t.version
            FROM jobs j
            JOIN tickets t ON t.id=j.ticket_id
            WHERE j.status='QUEUED' AND t.status='QUEUED'
            ORDER BY j.created_at, j.id
            LIMIT 1
            """
        ).fetchone()

        if not job:
            return None

        lease = str(uuid.uuid4())
        now = time.time()

        c.execute(
            """
            UPDATE jobs
            SET status='PROCESSING',
                started_at=?,
                heartbeat=?,
                lease_token=?,
                attempts=attempts+1,
                input_version=?
            WHERE id=?
            """,
            (
                now, now, lease,
                job["version"], job["id"]
            )
        )

        c.execute(
            """
            UPDATE tickets
            SET status='PROCESSING'
            WHERE id=?
            """,
            (job["ticket_id"],)
        )

        # A retry is a new attempt: the prior attempt's draft must not preempt it.
        c.execute("DELETE FROM ai_results WHERE job_id=?", (job["id"],))
        audit(c, job["ticket_id"], "WORKER_CLAIMED")

        return {
            "id": job["id"],
            "ticket_id": job["ticket_id"],
            "lease": lease,
            "version": job["version"]
        }


def persist(
    job_id, ticket_id, summary,
    draft, ids, escalate, evidence, *, lease=None, input_version=None
):
    """Persist an audited draft; fail closed on unapproved citations."""
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        job = c.execute("SELECT ticket_id, status, lease_token, input_version "
                        "FROM jobs WHERE id=?", (job_id,)).fetchone()
        ticket = c.execute(
            "SELECT risk, status, version FROM tickets WHERE id=?",
            (ticket_id,)
        ).fetchone()
        if (not job or not ticket or job["ticket_id"] != ticket_id
                or job["status"] != "PROCESSING"
                or ticket["status"] != "PROCESSING"
                or job["input_version"] != ticket["version"]
                or (lease is not None and job["lease_token"] != lease)
                or (input_version is not None and job["input_version"] != input_version)):
            raise RuntimeError("Stale or unauthorized AI result")

        if not ticket:
            raise RuntimeError("Ticket missing")

        requested_ids = list(ids)
        approved_ids = [
            item for item in requested_ids
            if item in evidence
            and evidence[item].get("visibility") == "customer"
            and evidence[item].get("source") not in INTERNAL_ONLY
        ]
        # Log decision codes only. Never log customer text or policy excerpts.
        rejection_reasons = []
        if not requested_ids:
            rejection_reasons.append("MISSING_CITATIONS")
        elif len(approved_ids) != len(requested_ids):
            if any(item not in evidence for item in requested_ids):
                rejection_reasons.append("INVALID_CITATIONS")
            if any(item in evidence and (
                    evidence[item].get("visibility") != "customer"
                    or evidence[item].get("source") in INTERNAL_ONLY)
                    for item in requested_ids):
                rejection_reasons.append("NON_CUSTOMER_EVIDENCE")

        # A valid citation is necessary but not sufficient for auto-publication.
        if not rejection_reasons and not escalate:
            grounding_reason = check_response_grounding(
                draft, [evidence[item] for item in approved_ids]
            )
            if grounding_reason:
                rejection_reasons.append(grounding_reason)
        visibility_reason = check_customer_visibility(draft)
        if visibility_reason:
            rejection_reasons.append(visibility_reason)
        invalid_evidence = bool(rejection_reasons)
        if rejection_reasons:
            print("EVIDENCE_REJECTION: " + ",".join(rejection_reasons), flush=True)
        elif escalate or ticket["risk"]:
            print("EVIDENCE_REVIEW: AGENT_OR_TICKET_RISK", flush=True)
        else:
            print("EVIDENCE_VALIDATION: ACCEPTED", flush=True)

        escalate = bool(escalate or ticket["risk"] or invalid_evidence)

        if invalid_evidence:
            draft = (
                "Your request requires human review because "
                "verified customer-facing policy evidence "
                "is insufficient."
            )
            approved_ids = []

        # A duplicate callback is safe only if it is byte-for-byte equivalent
        # in all decision fields. Never acknowledge conflicting decisions.
        payload = (ticket_id, summary, draft, json.dumps(approved_ids), int(escalate))
        existing = c.execute(
            "SELECT ticket_id, summary, draft, citations, requires_review "
            "FROM ai_results WHERE job_id=?", (job_id,)
        ).fetchone()
        if existing is not None:
            stored = tuple(existing[key] for key in
                           ("ticket_id", "summary", "draft", "citations", "requires_review"))
            if stored != payload:
                raise RuntimeError("Conflicting AI result for job")
        else:
            c.execute(
                "INSERT INTO ai_results "
                "(job_id, ticket_id, summary, draft, citations, requires_review, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, *payload, time.time())
            )

    return "PERSISTED"


def finish(row, success):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")

        job = c.execute(
            "SELECT * FROM jobs WHERE id=?",
            (row["id"],)
        ).fetchone()

        if (
            not job
            or job["status"] != "PROCESSING"
            or job["lease_token"] != row["lease"]
        ):
            print(
                "Worker finalization skipped: lease mismatch",
                flush=True
            )
            return

        result = c.execute(
            """
            SELECT * FROM ai_results
            WHERE job_id=?
            """,
            (row["id"],)
        ).fetchone()

        ticket = c.execute(
            "SELECT * FROM tickets WHERE id=?",
            (row["ticket_id"],)
        ).fetchone()

        if not ticket:
            raise RuntimeError("Ticket missing during finalization")

        stale = (ticket["version"] != row["version"]
                 or ticket["status"] != 'PROCESSING')
        if stale:
            c.execute("UPDATE jobs SET status='SUPERSEDED', completed_at=?, "
                      "lease_token=NULL WHERE id=?", (time.time(), row['id']))
            audit(c, row['ticket_id'], 'AI_RESULT_SUPERSEDED')
            print('Obsolete AI job discarded without changing ticket status', flush=True)
            return

        requires_review = bool(
            result["requires_review"]
        ) if result else True

        escalate = bool(
            stale
            or not success
            or not result
            or ticket["risk"]
            or requires_review
        )

        if result and not escalate:
            c.execute(
                """
                INSERT OR IGNORE INTO messages
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["ticket_id"],
                    "assistant",
                    result["draft"],
                    1,
                    time.time()
                )
            )

        ticket_status = (
            "ESCALATED" if escalate else "ANSWERED"
        )

        job_status = (
            "DONE" if success and result else "FAILED"
        )

        c.execute(
            """
            UPDATE tickets
            SET status=?, version=version+1
            WHERE id=?
            """,
            (ticket_status, row["ticket_id"])
        )

        c.execute(
            """
            UPDATE jobs
            SET status=?,
                completed_at=?,
                lease_token=NULL
            WHERE id=?
            """,
            (job_status, time.time(), row["id"])
        )

        event = "AI_" + ticket_status
        if stale:
            event += "_STALE"

        audit(c, row["ticket_id"], event)

        print(
            f"Ticket {row['ticket_id']}: "
            f"job={job_status}, ticket={ticket_status}, "
            f"stale={stale}, review={requires_review}",
            flush=True
        )


def logged_by_agent(messages):
    for message in messages:
        if getattr(message, "source", None) != "LogAgent":
            continue

        content = getattr(message, "content", None)

        if isinstance(content, str):
            if content.strip() == "LOGGED":
                return True

        elif isinstance(content, list):
            for item in content:
                if (
                    getattr(item, "content", None) == "LOGGED"
                    and getattr(item, "name", None) == "persist_outcome"
                ):
                    return True

    return False

async def process(row, retriever):
    with connect() as c:
        ticket = c.execute(
            "SELECT subject, description FROM tickets WHERE id=?",
            (row['ticket_id'],)
        ).fetchone()
        history = c.execute(
            "SELECT role, content FROM messages "
            "WHERE ticket_id=? AND visible=1 "
            "ORDER BY created_at DESC, rowid DESC LIMIT 20",
            (row['ticket_id'],)
        ).fetchall()

    if not ticket:
        finish(row, False)
        return

    # Deterministic RAG retrieval happens before any model/tool selection.
    # Prefer the latest customer follow-up; do not retrieve on the entire history.
    latest_customer = next(
        (m["content"] for m in history if m["role"] in ("customer", "user")), None
    )
    initial_query = latest_customer or (
        f"{ticket['subject']} {ticket['description']}"
    )
    try:
        team, client = make_team(
            retriever,
            row["id"],
            row["ticket_id"],
            partial(persist, lease=row["lease"], input_version=row["version"]),
            initial_query=initial_query,
        )
    except Exception as exc:
        print("Mandatory RAG initialization failed:", type(exc).__name__, flush=True)
        finish(row, False)
        return

    async def heartbeat():
        while True:
            await asyncio.sleep(10)
            with connect() as c:
                c.execute(
                    """
                    UPDATE jobs
                    SET heartbeat=?
                    WHERE id=? AND lease_token=?
                    """,
                    (
                        time.time(),
                        row["id"],
                        row["lease"]
                    )
                )

    hb = asyncio.create_task(heartbeat())

    try:
        print(
            f"Processing ticket {row['ticket_id']}",
            flush=True
        )

        conversation = "\n".join(
            f"{m['role']}: {m['content'][:2500]}" for m in reversed(history)
        )
        result = await team.run(
            task=(
                f"Customer subject: {ticket['subject']}\n"
                f"Original complaint: {ticket['description']}\n"
                f"Conversation (latest customer message takes priority):\n{conversation}\n"
                "Answer the latest customer follow-up, not only the original complaint. "
                "Use the four-agent workflow and retrieved policy evidence. "
                "Do not invent live tracking data, shipment milestones, delivery ETAs, "
                "refund decisions, or order status. If live order information is unavailable, "
                "say so clearly."
            )
        )

        with connect() as c:
            saved = c.execute(
                """
                SELECT 1 FROM ai_results
                WHERE job_id=?
                """,
                (row["id"],)
            ).fetchone()

        acknowledged = logged_by_agent(result.messages)

        print(
            "AutoGen termination:",
            getattr(result, "stop_reason", None),
            flush=True
        )
        print(
            "AI result persisted:",
            bool(saved),
            flush=True
        )
        print(
            "LogAgent acknowledged:",
            acknowledged,
            flush=True
        )

        if not acknowledged:
            print(
                "Agent message sources:",
                [
                    getattr(m, "source", "?")
                    for m in result.messages
                ],
                flush=True
            )

        record_agent_events(row, result.messages)
        success = bool(saved) and acknowledged

        finish(row, success)

    except Exception as exc:
        print(
            "Worker error:",
            type(exc).__name__,
            flush=True
        )
        finish(row, False)

    finally:
        hb.cancel()

        try:
            await hb
        except asyncio.CancelledError:
            pass

        await client.close()


async def main():
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY required in .env"
        )

    init()
    migrate_rubric()
    retriever = HybridRetriever()

    print("FixFlow worker ready", flush=True)

    while True:
        recover()
        row = claim()

        if row:
            await process(row, retriever)
        else:
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
