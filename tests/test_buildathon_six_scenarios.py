"""Offline acceptance preflight for the six exact Social Eagle complaints.

This checks API intake, backend safety routing, isolation, and an unsupported
Help Center question. It does NOT run AutoGen or certify final AI responses.
Run: python -m pytest tests/test_buildathon_six_scenarios.py -q -rs
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.auth.security import create_session, hash_password
from backend.db import store
from backend.main import app
from backend.services.help_center import answer_question


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
    monkeypatch.setattr(store, "DB", str(tmp_path / "six_scenarios.sqlite3"))
    with TestClient(app) as client:
        yield client


def customer_headers():
    uid = str(uuid.uuid4())
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)",
            (uid, uid + "@example.test", hash_password("Testing123!Secure"), "customer", 1),
        )
    return {"Authorization": "Bearer " + create_session(uid)}


@pytest.mark.parametrize("scenario_number", range(1, 7), ids=[f"scenario-{i}" for i in range(1, 7)])
def test_official_complaint_intake_and_safe_initial_routing(isolated_client, scenario_number):
    """All six official complaints must be accepted and never auto-resolved on intake."""
    category, complaint = COMPLAINTS[scenario_number - 1]
    owner = customer_headers()
    other_customer = customer_headers()
    response = isolated_client.post(
        "/tickets",
        headers=owner,
        json={"subject": f"Buildathon scenario {scenario_number}: {category}",
              "description": complaint},
    )
    assert response.status_code == 201, response.text
    ticket = response.json()
    assert ticket["status"] in ("QUEUED", "ESCALATED")
    assert isolated_client.get(f"/tickets/{ticket['id']}", headers=owner).status_code == 200
    assert isolated_client.get(f"/tickets/{ticket['id']}", headers=other_customer).status_code == 404

    with store.connect() as conn:
        jobs = conn.execute("SELECT status FROM jobs WHERE ticket_id=?", (ticket["id"],)).fetchall()
        if ticket["status"] == "ESCALATED":
            assert not jobs, "Escalated complaint must not enter AI processing queue"
        else:
            assert len(jobs) == 1 and jobs[0]["status"] == "QUEUED"

    if scenario_number == 5:
        assert ticket["status"] == "ESCALATED", "Legal/payment dispute must reach human support"


class NoMatchingPolicies:
    def search(self, query, k=4):
        return []


def test_scenario_6_unsupported_gift_cards_has_no_invented_policy():
    question = COMPLAINTS[5][1]
    result = answer_question(question, search=NoMatchingPolicies())
    assert result["status"] == "no_evidence"
    assert result["sources"] == []
    assert "could not verify" in result["answer"].lower()
