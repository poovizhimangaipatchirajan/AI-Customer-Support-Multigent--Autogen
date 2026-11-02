"""Opt-in live Social Eagle acceptance: real OpenAI + real hybrid RAG + worker.

Run ONLY after setting FIXFLOW_RUN_BUILDATHON_LIVE=1. Each parameter uses a
fresh temporary SQLite database. No production/dev database writes or index rebuild.

This is an evidence-collection test, not automatic semantic certification.
"""
import asyncio
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.auth.security import create_session, hash_password
from backend.db import store
from backend.main import app
from backend.worker import main as worker
from backend.rag.hybrid import HybridRetriever
from backend.services_rubric_logging import migrate as migrate_rubric

COMPLAINTS = [
    ("Shipping", "Where is my order? It was supposed to arrive yesterday."),
    ("Refund", "I want to return the shoes I bought last week. How do I get a refund?"),
    ("Account", "I forgot my password and can't log in."),
    ("Product / Refund", "The blender I received is broken. Can I get a replacement?"),
    ("Billing", "You charged my card three times! Fix this now or I'm contacting my bank and a lawyer."),
    ("Unsupported", "Do you sell gift cards for a company of 500 employees with custom branding?"),
]


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    # Explicit opt-in, BEFORE any expensive operation or database initialization.
    if os.getenv("FIXFLOW_RUN_BUILDATHON_LIVE") != "1":
        pytest.skip("Set FIXFLOW_RUN_BUILDATHON_LIVE=1 to run paid live scenarios")
    if not os.getenv("OPENAI_API_KEY"):
        # The worker loads .env during import, but this also catches missing keys.
        pytest.skip("OPENAI_API_KEY not loaded; check local .env")
    monkeypatch.setattr(store, "DB", str(tmp_path / "buildathon_live.sqlite3"))
    with TestClient(app) as client:
        migrate_rubric()
        yield client


def customer_headers():
    uid = str(uuid.uuid4())
    with store.connect() as db:
        db.execute(
            "INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)",
            (uid, uid + "@example.test", hash_password("Testing123!Secure"), "customer", 1),
        )
    return {"Authorization": "Bearer " + create_session(uid)}


@pytest.mark.parametrize("number", range(1, 7), ids=[f"official-{i}" for i in range(1, 7)])
def test_live_official_scenario(isolated_client, number, capsys):
    category, complaint = COMPLAINTS[number - 1]
    headers = customer_headers()
    created = isolated_client.post(
        "/tickets", headers=headers,
        json={"subject": f"Buildathon scenario {number}: {category}", "description": complaint},
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    initial_status = created.json()["status"]
    assert initial_status in {"QUEUED", "ESCALATED"}

    if initial_status == "QUEUED":
        # Real hybrid retrieval, OpenAI AutoGen, LogAgent callback, and SQLite finish.
        retriever = HybridRetriever()
        claimed = worker.claim()
        assert claimed is not None and claimed["ticket_id"] == ticket_id
        asyncio.run(worker.process(claimed, retriever))

    with store.connect() as db:
        current = db.execute("SELECT status, risk FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        jobs = db.execute("SELECT id, status FROM jobs WHERE ticket_id=?", (ticket_id,)).fetchall()
        results = db.execute(
            "SELECT summary,draft,citations,requires_review FROM ai_results WHERE ticket_id=?",
            (ticket_id,),
        ).fetchall()

    assert current is not None
    final_status = current["status"]
    assert final_status in {"ANSWERED", "ESCALATED"}, (
        f"Scenario {number} unfinished: {final_status}; jobs={[(j['id'], j['status']) for j in jobs]}"
    )
    if initial_status == "ESCALATED":
        assert not jobs, "Backend-escalated complaints must not be processed by AutoGen"
    else:
        assert len(jobs) == 1 and jobs[0]["status"] in {"DONE", "FAILED"}
        assert jobs[0]["status"] == "DONE", "Live workflow did not complete successfully"
        assert len(results) == 1, "LogAgent did not persist exactly one result"
        cited = json.loads(results[0]["citations"])
        assert isinstance(cited, list)
        if final_status == "ANSWERED":
            assert cited, "Published AI response lacks policy citations"
            assert not results[0]["requires_review"]
        else:
            assert results[0]["requires_review"] or current["risk"]

    if number == 5:
        assert final_status == "ESCALATED", "Payment dispute/legal threat must escalate"
    if number == 6:
        assert final_status == "ESCALATED", "Unsupported gift-card request must escalate"

    detail = isolated_client.get(f"/tickets/{ticket_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == final_status

    # Do not print raw complaint, user identifiers, policy excerpts, or API keys.
    # The result draft remains in the isolated temporary database for this test.
    report = {
        "scenario": number, "rubric_category": category,
        "initial_status": initial_status, "final_status": final_status,
        "job_status": jobs[0]["status"] if jobs else "NOT_QUEUED",
        "ai_result_persisted": bool(results),
        "citation_count": len(json.loads(results[0]["citations"])) if results else 0,
        "requires_review": bool(results[0]["requires_review"]) if results else True,
        "manual_review_needed": True,
    }
    with capsys.disabled():
        print("BUILDATHON_LIVE_RESULT " + json.dumps(report), flush=True)
