"""FixFlow AI local-only, opt-in integration tests.

Copy to tests/e2e/test_customer_complaint_journey.py.
Run with FIXFLOW_RUN_MUTATING_E2E=1 after starting FastAPI and Streamlit.
Tests create fictional tickets in the configured local database.
"""
import json
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect

from fixflow_test_endpoints import API_URL, UI_URL

pytestmark = pytest.mark.skipif(
    os.getenv("FIXFLOW_RUN_MUTATING_E2E") != "1",
    reason="Set FIXFLOW_RUN_MUTATING_E2E=1 to allow fictional ticket creation in the local database",
)


def credentials(role):
    email = os.getenv(f"FIXFLOW_TEST_{role}_EMAIL")
    password = os.getenv(f"FIXFLOW_TEST_{role}_PASSWORD")
    if not email or not password:
        pytest.skip(f"Configure FIXFLOW_TEST_{role}_EMAIL and FIXFLOW_TEST_{role}_PASSWORD")
    return email, password


def request(method, path, token=None, payload=None):
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(API_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            body = {"detail": raw.decode("utf-8", errors="replace")}
        return exc.code, body
    except URLError as exc:
        pytest.fail(f"FastAPI is unavailable at {API_URL}: {exc}")


def login_api(role):
    email, password = credentials(role)
    status, body = request("POST", "/auth/login", payload={"email": email, "password": password})
    assert status == 200, f"{role} login failed: HTTP {status}; verify test credentials"
    assert body["role"] == role.lower(), f"Expected {role.lower()} role"
    return body["access_token"]


def new_subject(label):
    return f"E2E {label} {uuid.uuid4().hex[:10]}"


def create_ticket(token, subject, description, amount=None):
    status, body = request("POST", "/tickets", token, {
        "subject": subject, "description": description, "claimed_amount": amount,
    })
    assert status == 201, f"Ticket creation failed: HTTP {status} {body}"
    return body


def sidebar(page, name):
    return page.get_by_test_id("stSidebarUserContent").get_by_role("button", name=name)


def login_ui(page, role="CUSTOMER"):
    email, password = credentials(role)
    page.goto(UI_URL, wait_until="domcontentloaded")
    expect(page.get_by_text("Welcome to FixFlow AI")).to_be_visible(timeout=20000)
    page.get_by_label("Email", exact=True).first.fill(email)
    page.get_by_label("Password", exact=True).first.fill(password)
    page.get_by_role("button", name="Login", exact=True).click()
    expect(sidebar(page, "Log out")).to_be_visible(timeout=20000)


def test_customer_submits_complaint_through_streamlit(page: Page):
    """Exercise the actual two-step Streamlit complaint form and confirmation."""
    login_ui(page)
    sidebar(page, "New complaint").click()
    expect(page.get_by_text("Raise a new complaint")).to_be_visible(timeout=15000)
    subject = new_subject("shipping complaint")
    expect(page.get_by_role("combobox", name="Category")).to_be_visible()
    page.get_by_label("Subject").fill(subject)
    page.get_by_label("Description").fill(
        "My fictional NovaCart order has not arrived; please advise the delivery status."
    )
    page.get_by_role("button", name="Next: review complaint").click()
    expect(page.get_by_text(subject)).to_be_visible(timeout=15000)
    page.get_by_role("button", name="Submit complaint", exact=True).click()
    expect(page.get_by_text("Your complaint was submitted successfully.")).to_be_visible(timeout=20000)
    page.get_by_role("button", name="View ticket").click()
    expect(page.get_by_text(subject)).to_be_visible(timeout=15000)
    sidebar(page, "My tickets").click()
    expect(page.get_by_text(subject)).to_be_visible(timeout=15000)


def test_refund_499_vs_500_and_admin_publication():
    """Check initial backend routing and admin publication, not refund execution."""
    customer = login_api("CUSTOMER")
    admin = login_api("ADMIN")
    under = create_ticket(
        customer, new_subject("refund under threshold"),
        "I request a refund of INR 499 for my fictional damaged order.", 499,
    )
    assert under["status"] == "QUEUED", (
        "A case-specific INR 499 refund with no other risks should initially queue; "
        f"got {under}"
    )
    at = create_ticket(
        customer, new_subject("refund at threshold"),
        "I request a refund of INR 500 for my fictional damaged order.", 500,
    )
    assert at["status"] == "ESCALATED", "INR 500 must require human review"
    status, detail = request("GET", f"/tickets/{at['id']}", customer)
    assert status == 200 and detail["status"] == "ESCALATED"
    assert all(message["role"] != "admin" for message in detail["messages"])
    assert "drafts" not in detail, "Customer must not receive internal AI drafts"
    status, queue = request("GET", "/admin/queue", admin)
    assert status == 200 and any(t["id"] == at["id"] for t in queue)
    status, forbidden = request("POST", f"/admin/tickets/{at['id']}/publish", customer, {
        "response": "Unauthorized publication", "version": detail["version"],
    })
    assert status == 403, f"Customer must not publish admin responses: {forbidden}"
    response_text = "Our support team has reviewed your fictional refund request. No refund has been processed."
    status, published = request("POST", f"/admin/tickets/{at['id']}/publish", admin, {
        "response": response_text, "version": detail["version"],
    })
    assert status == 200 and published["status"] == "ANSWERED", (status, published)
    status, after = request("GET", f"/tickets/{at['id']}", customer)
    assert status == 200 and after["status"] == "ANSWERED"
    assert any(m["role"] == "admin" and m["content"] == response_text for m in after["messages"])
    status, stale = request("POST", f"/admin/tickets/{at['id']}/publish", admin, {
        "response": "Duplicate publication", "version": detail["version"],
    })
    assert status == 409, f"Duplicate/stale publication should be rejected: {stale}"


def test_customer_followup_and_sticky_escalation():
    customer = login_api("CUSTOMER")
    ticket = create_ticket(
        customer, new_subject("ordinary followup"),
        "Please provide an update on the shipping date of my fictional order.", None,
    )
    assert ticket["status"] == "QUEUED", ticket
    status, reply = request("POST", f"/tickets/{ticket['id']}/reply", customer, {
        "content": "Can you provide the expected delivery date for this fictional order?"
    })
    assert status == 200 and reply["status"] == "QUEUED", (status, reply)
    status, detail = request("GET", f"/tickets/{ticket['id']}", customer)
    assert status == 200
    assert len([m for m in detail["messages"] if m["role"] == "customer"]) == 2
    status, risk_reply = request("POST", f"/tickets/{ticket['id']}/reply", customer, {
        "content": "I now dispute an unauthorized payment on my account. Please escalate this."
    })
    assert status == 200 and risk_reply["status"] == "ESCALATED", (status, risk_reply)
    status, safe_reply = request("POST", f"/tickets/{ticket['id']}/reply", customer, {
        "content": "Thank you for reviewing my fictional order."
    })
    assert status == 200 and safe_reply["status"] == "ESCALATED", (
        "Human-review risk must remain sticky", status, safe_reply
    )


def test_customer_cannot_access_admin_queue():
    customer = login_api("CUSTOMER")
    status, _ = request("GET", "/admin/queue", customer)
    assert status == 403
