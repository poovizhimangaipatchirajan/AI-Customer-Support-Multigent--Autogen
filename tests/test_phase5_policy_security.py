"""Phase 5: deterministic FixFlow policy/security regression tests.

Run: python -m pytest tests/test_phase5_policy_security.py -v
Uses a temporary SQLite DB. No OpenAI calls or live servers required.
"""
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.db import store
from backend.services.risk import screen
from backend.services.help_center import answer_question


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "phase5.sqlite3"))
    store.init()
    # Main API startup also performs customer-order migrations.
    from backend.services.customer_orders import migrate
    migrate()
    from backend.main import app
    with TestClient(app) as client:
        yield client


def make_user(email, role="customer", verified=1):
    from backend.auth.security import hash_password, create_session
    uid = str(uuid.uuid4())
    with store.connect() as c:
        c.execute(
            "INSERT INTO users(id,email,password_hash,role,verified) VALUES (?,?,?,?,?)",
            (uid, email, hash_password("Temporary-Test-Password1!"), role, verified),
        )
    return {"id": uid, "headers": {"Authorization": "Bearer " + create_session(uid)}}


def create(client, user, amount=None, subject="Delivery delay", description="My parcel has not arrived after the expected delivery date."):
    payload = {"subject": subject, "description": description, "claimed_amount": amount}
    response = client.post("/tickets", json=payload, headers=user["headers"])
    assert response.status_code == 201, response.text
    return response.json()


def test_refund_threshold_inclusive():
    assert "REFUND_GE_500" not in screen("Please refund me for my order", 499)
    assert "REFUND_GE_500" in screen("Please refund me for my order", 500)
    assert "REFUND_GE_500" in screen("Please refund me for my order", 500.01)


def test_risk_escalations_and_ambiguous_currency():
    assert "FRAUD" in screen("I suspect fraud on my account", 20)
    assert "LEGAL" in screen("I will contact a lawyer", 20)
    assert "PAYMENT_DISPUTE" in screen("I was charged twice", 20)
    assert "REFUND_AMOUNT_UNVERIFIED" in screen("Please refund me", None)
    assert "REFUND_CURRENCY_UNCERTAIN" in screen("Please refund me $30", 30)


def test_general_policy_question_is_not_refund_case():
    assert "REFUND_GE_500" not in screen("What is the refund policy?", 900)


def test_customer_ticket_isolation_and_admin_access(env):
    alice = make_user("phase5-alice@example.test")
    bob = make_user("phase5-bob@example.test")
    admin = make_user("phase5-admin@example.test", role="admin")
    tid = create(env, alice)["id"]
    assert env.get(f"/tickets/{tid}", headers=alice["headers"]).status_code == 200
    assert env.get(f"/tickets/{tid}", headers=bob["headers"]).status_code == 404
    assert env.post(f"/tickets/{tid}/reply", json={"content": "Not my ticket"}, headers=bob["headers"]).status_code == 404
    assert env.get(f"/tickets/{tid}", headers=admin["headers"]).status_code == 200
    assert all(t["id"] != tid for t in env.get("/tickets", headers=bob["headers"]).json())


def test_admin_endpoints_reject_customer(env):
    customer = make_user("phase5-customer@example.test")
    tid = create(env, customer)["id"]
    assert env.get("/admin/queue", headers=customer["headers"]).status_code == 403
    assert env.get(f"/admin/audit/{tid}", headers=customer["headers"]).status_code == 403
    assert env.get("/admin/export.csv", headers=customer["headers"]).status_code == 403
    assert env.post(f"/admin/tickets/{tid}/publish", json={"response": "Unauthorized", "version": 0}, headers=customer["headers"]).status_code == 403


def test_unverified_customer_cannot_submit(env):
    user = make_user("phase5-unverified@example.test", verified=0)
    r = env.post("/tickets", json={"subject": "Shipping issue", "description": "My shipment has not arrived yet."}, headers=user["headers"])
    assert r.status_code == 403


def test_high_value_refund_draft_not_customer_visible(env):
    customer = make_user("phase5-refund@example.test")
    admin = make_user("phase5-reviewer@example.test", role="admin")
    ticket = create(env, customer, 500, "Refund request", "Please refund me for the missing order.")
    tid = ticket["id"]
    assert ticket["status"] == "ESCALATED"
    with store.connect() as c:
        c.execute("INSERT INTO ai_results(job_id,ticket_id,summary,draft,citations,requires_review,created_at) VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), tid, "Review", "INTERNAL_DRAFT_NOT_APPROVED", "[]", 1, time.time()))
    customer_data = env.get(f"/tickets/{tid}", headers=customer["headers"]).json()
    assert "drafts" not in customer_data
    assert "INTERNAL_DRAFT_NOT_APPROVED" not in str(customer_data)
    admin_data = env.get(f"/tickets/{tid}", headers=admin["headers"]).json()
    assert any("INTERNAL_DRAFT_NOT_APPROVED" in d["draft"] for d in admin_data["drafts"])
    assert all("INTERNAL_DRAFT_NOT_APPROVED" not in m["content"] for m in customer_data["messages"])


def test_approval_version_and_one_time_publish(env):
    customer = make_user("phase5-approval@example.test")
    admin = make_user("phase5-approver@example.test", role="admin")
    tid = create(env, customer, 500, "Refund request", "Please refund me for a broken product.")["id"]
    version = env.get(f"/tickets/{tid}", headers=admin["headers"]).json()["version"]
    url = f"/admin/tickets/{tid}/publish"
    assert env.post(url, json={"response": "Old approval", "version": version + 1}, headers=admin["headers"]).status_code == 409
    assert env.post(url, json={"response": "Your request is under review; no refund was issued.", "version": version}, headers=admin["headers"]).status_code == 200
    assert env.post(url, json={"response": "Duplicate", "version": version}, headers=admin["headers"]).status_code == 409
    data = env.get(f"/tickets/{tid}", headers=customer["headers"]).json()
    assert data["status"] == "ANSWERED"
    assert sum("no refund was issued" in m["content"] for m in data["messages"]) == 1


class FakeSearch:
    def __init__(self, passages):
        self.passages = passages
        self.calls = []

    def search(self, query, k=4):
        self.calls.append((query, k))
        return self.passages[:k]


def test_help_center_blocks_case_specific_requests_without_search():
    search = FakeSearch([{"id": "x", "source": "01_Shipping_and_Delivery_SOP.pdf", "text": "shipping delivery"}])
    result = answer_question("Please refund me for my order", search=search)
    assert result["status"] == "ticket_required"
    assert result["sources"] == []
    assert not search.calls


def test_help_center_no_evidence_fallback():
    result = answer_question("What is the shipping delivery timeframe?", search=FakeSearch([]))
    assert result["status"] == "no_evidence"
    assert result["sources"] == []


def test_help_center_accepts_actual_approved_pdf_evidence():
    """Regression: current help-center mapping may still use obsolete .md names."""
    search = FakeSearch([{
        "id": "approved-shipping-1",
        "source": "01_Shipping_and_Delivery_SOP.pdf",
        "version": "pdf-sop-v1",
        "visibility": "customer",
        "text": "Shipping delivery timeframe: standard delivery normally takes five business days.",
    }])
    result = answer_question("What is the shipping delivery timeframe?", search=search)
    assert result["status"] == "answered", (
        "Help Center rejected relevant evidence from the actual indexed PDF filename: " + str(result)
    )
    assert result["sources"][0]["document"] == "01_Shipping_and_Delivery_SOP.pdf"
    assert "five business days" in result["answer"]


def test_help_center_does_not_leak_internal_sop():
    search = FakeSearch([{
        "id": "internal-1",
        "source": "06_Escalations_and_Soft_Skills_SOP.pdf",
        "version": "pdf-sop-v1",
        "text": "Internal escalation and customer handling instructions are confidential.",
    }])
    result = answer_question("What are the internal escalation handling instructions?", search=search)
    assert result["status"] != "answered"
    assert result["sources"] == []
    assert "confidential" not in result["answer"].lower()
