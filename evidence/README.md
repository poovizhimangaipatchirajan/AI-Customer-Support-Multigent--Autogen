# FixFlow AI — Buildathon Evaluation Evidence

## Project Overview

FixFlow AI is a NovaCart multi-agent customer support application.

Architecture:
- FastAPI backend
- Streamlit frontend
- Microsoft AutoGen
- Hybrid RAG: FAISS + BM25 + RRF
- SQLite persistence
- Human-review escalation

## 1. Agent Design

Four AutoGen agents:
- QueryAgent: Classifies customer requests and risks.
- KnowledgeAgent: Retrieves approved policy evidence.
- ResolutionAgent: Produces grounded response drafts.
- LogAgent: Persists outcomes.

Evidence: logs/agent_execution.txt

Two completed tickets recorded all four agents.

## 2. Knowledge Grounding

Evidence: logs/knowledge_grounding.txt

Ticket: 90a4a79f-bd3c-4d20-89af-e87ad3f70909

Result: 4/4 citation IDs matched approved policy excerpts.

Source: 02_Returns_and_Refunds_SOP.pdf, page 1.

The response covers return windows, eligibility, damaged-product
photographs, return initiation and order verification.

One valid electronics citation was unnecessary for the response.

## 3. Workflow and Escalation

Evidence: logs/worker_execution.txt

Ticket: 9795c2c6-c35c-497c-a01e-4a4ca58916f8

Verified:
- Missing citations detected.
- Unsupported answer rejected.
- Ticket status: ESCALATED.
- Human review required.
- Safe fallback persisted.
- Citations cleared.
- LogAgent acknowledged completion.

The live run exercised missing-citation protection, not the
invoice-specific citation-relevance guard.

## 4. Logging

The application persists tickets, agent events, AI results,
citations, review decisions and ticket statuses.

A complaint CSV report is also maintained.

## API Integration Evidence

Evidence: logs/api_execution_evidence.txt

Verified HTTP activity:
- Customer ticket creation: 201 Created
- Ticket retrieval: 200 OK
- Authentication/profile endpoints: 200 OK
- Admin complaint endpoint: 200 OK

These records demonstrate successful API requests during local testing.
## 5. Regression Tests

Evidence: test-results/invoice_grounding_tests.txt

Result: 4 passed.

The tests cover unsupported invoice guidance, supported synthetic
invoice guidance, neutral billing guidance and return-policy guidance.

## 6. Streamlit UI

Screenshots to capture:
- Customer ticket submission.
- Grounded return-policy response.
- Escalated invoice ticket.
- Administrator review interface.

## 7. Reproduction

Run from the repository root:

    python -m pytest tests/test_invoice_grounding.py -q

See the root README.md for application startup instructions.
## Streamlit UI Evidence

| Screenshot | Demonstration |
|---|---|
| screenshots/01_customer_portal.png | Customer dashboard and ticket submission |
| screenshots/02_grounded_response.png | AI response grounded in approved policy documents |
| screenshots/03_invoice_escalation.png | Safe escalation when citations are unavailable |
| screenshots/04_admin_review.png | Human-review interface for escalated tickets |
## Important Notes

- Evidence reflects actual local test runs.
- Rubric marks are provisional.
- No automatic refund execution is authorized.
- Case-specific refund requests of INR 500 or more require human review.
- Do not commit secrets or confidential customer information.
