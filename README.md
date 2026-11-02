# FixFlow AI — NovaCart Multi-Agent Customer Support

FixFlow AI is a local-first customer-support application built with a **FastAPI backend**, **Streamlit frontend**, **AutoGen multi-agent workflow**, and **hybrid retrieval-augmented generation (RAG)**. It helps process customer complaints, retrieve policy evidence, draft grounded responses, and route sensitive cases to human administrators.

> RC1 has passed its agreed local validation gates. This does **not** certify production security, live payment processing, or readiness to handle real customer data.

## Features

- Customer registration, verification, sign-in, ticket submission, and ticket follow-ups.
- Customer-specific ticket access and administrative review workflows.
- Four-agent AutoGen support workflow for query handling, knowledge retrieval, resolution, and outcome logging.
- Hybrid **FAISS + BM25** retrieval combined with **Reciprocal Rank Fusion (RRF)** and evidence/citation controls.
- Read-only MCP integration and restricted public web research for appropriate current-information questions.
- SQLite-backed tickets, audit records, AI results, and durable refund-review decisions.
- Worker leases, heartbeats, retry/recovery handling, stale-result protection, and idempotent processing safeguards.
- Administrative queue, draft review/publishing, audit inspection, and CSV export.

## Safety and refund rules

- **Refund requests of ₹500 or more, including exactly ₹500, require backend-enforced human review.** This includes refund requests introduced in follow-up messages.
- Fraud, legal, and payment-dispute concerns are escalated independently of the amount.
- The application **does not execute real refunds or payment transactions**.
- AI answers should be grounded in approved evidence and must not invent order status, refund status, payment outcomes, or delivery estimates.
- Internal-only policy material must not be disclosed to customers; customer records must remain isolated by account.
- Public web search must not disclose private customer information.

## System architecture

FixFlow AI separates the customer-facing interface, authenticated API, background processing, and evidence-backed AI workflow. The diagram below is a conceptual map of the documented components; it is not a claim that every integration is invoked for every ticket.

```mermaid
flowchart TB
    C[Customer] --> UI[Streamlit customer portal]
    A[Support administrator] --> AD[Streamlit admin dashboard]
    UI --> API[FastAPI backend]
    AD --> API
    API --> AUTH[Authentication and authorization]
    API --> DB[(SQLite: users, tickets, review state, audit records)]
    API --> GUARD[Backend policy and escalation checks]
    API --> WORKER[Background worker]
    WORKER --> TEAM[AutoGen support workflow]
    TEAM --> QA[QueryAgent]
    QA --> KA[KnowledgeAgent]
    KA --> RA[ResolutionAgent]
    RA --> LA[LogAgent]
    KA --> RAG[Hybrid retrieval: FAISS + BM25 + RRF]
    RAG --> DOCS[Approved policy and FAQ documents]
    TEAM -. permitted read-only tools .-> MCP[MCP integration]
    TEAM -. current public information only .-> WEB[Restricted web search]
    LA --> DB
    GUARD --> REVIEW[Human review and approval]
    REVIEW --> DB
    API --> UI
    API --> AD
```

### Four-agent workflow

The AutoGen team is organized around four distinct responsibilities:

| Agent | Responsibility | Typical output |
| --- | --- | --- |
| **QueryAgent** | Understand the complaint and classify category, urgency, and sentiment. | Structured issue summary and classification |
| **KnowledgeAgent** | Retrieve relevant passages from approved documents using hybrid RAG and preserve source references. | Evidence passages and citations |
| **ResolutionAgent** | Draft a customer-facing response grounded in the retrieved evidence; avoid inventing policies or transaction outcomes. | Evidence-backed response draft |
| **LogAgent** | Record the ticket outcome and support the escalation/audit workflow. | Logged result and escalation information |

```mermaid
flowchart TD
    IN[Customer complaint or follow-up] --> T[FastAPI ticket intake and authorization]
    T --> Q[1. QueryAgent: classify and summarize]
    Q --> K[2. KnowledgeAgent: retrieve policy evidence]
    K --> R[3. ResolutionAgent: draft grounded reply]
    R --> L[4. LogAgent: record outcome]
    L --> POLICY{Escalation or human review required?}
    POLICY -->|Yes| H[Human review queue]
    POLICY -->|No| SAFE[Eligible support response]
    H --> ADMIN[Administrator reviews and publishes authorized response]
    SAFE --> OUT[Customer ticket view]
    ADMIN --> OUT
    OUT --> AUDIT[(Ticket history and audit trail)]
```

**Important:** The flowchart shows the logical support process, not a guarantee that the AI can approve or execute financial transactions. Backend checks and human review are authoritative. Refund requests of **₹500 or more** (including ₹500), fraud/legal/payment disputes, missing order verification, and missing required evidence must be handled under the applicable safeguards. **No refund is automatically executed.**

### Hybrid RAG and supporting services

- **FAISS** supplies vector-similarity retrieval; **BM25** supplies lexical retrieval; **Reciprocal Rank Fusion (RRF)** combines their ranked results.
- Retrieved policy/FAQ passages support citations and grounding checks; insufficient evidence must not become an invented policy answer.
- **MCP** is described as read-only, and public web search is restricted to appropriate current-information needs. Neither should expose private customer records.
- **SQLite** stores ticket and review state, while the separate worker handles AI processing and recovery. The admin interface supports review, publication, and audit inspection.
- The **frontend, API, and worker run as separate local processes**. Do not use the isolated test database for normal application work.

## Local setup — Windows CMD

Requirements: a compatible Python environment, dependencies from `requirements.txt`, and the configured local environment variables. Use the project's existing virtual environment if it is already working; do not recreate it unnecessarily.

```cmd
cd C:\Users\Admin\Desktop\nexora\fixflow-ai
venv\Scripts\activate
python -m pip install -r requirements.txt
```

Keep API keys and credentials in local environment configuration. **Never commit `.env`, SQLite databases, local mailbox contents, or credentials.** Refer to the project's environment template when available; do not copy real values into documentation.

### Start the API

In terminal 1:

```cmd
cd C:\Users\Admin\Desktop\nexora\fixflow-ai
venv\Scripts\activate
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### Start the frontend

In terminal 2:

```cmd
cd C:\Users\Admin\Desktop\nexora\fixflow-ai
venv\Scripts\activate
set FIXFLOW_API_URL=http://127.0.0.1:8000
python -m streamlit run frontend/app.py --server.address 127.0.0.1 --server.port 8501
```

Open **http://127.0.0.1:8501**.

### Start the background worker

In terminal 3, activate the same virtual environment and ensure the required model, retrieval, and integration settings are available:

```cmd
cd C:\Users\Admin\Desktop\nexora\fixflow-ai
venv\Scripts\activate
python -m backend.worker.main
```

For a fresh installation, build the policy index using the project's existing configured ingestion procedure before expecting RAG answers. Do not rebuild or overwrite an already validated local index unnecessarily.

## Local test validation

The following results were reported from the RC1 local validation cycle:

| Validation | Result |
| --- | --- |
| Final non-browser regression | **247 passed, 4 warnings, 0 failures** (76.44 s) |
| Browser end-to-end tests | **8 passed** (earlier validated run) |
| Lease-hardening targeted tests | **12 passed** |
| Final security-gate tests | **6 passed** |
| Live AI/RAG, MCP, and Serper integration checks | Previously exercised successfully in targeted runs |

The test counts **overlap** and should not be added together. The four warnings in the final regression were deprecation warnings.

To rerun the final non-browser suite from the project root:

```cmd
python -m pytest -q -rs -rw -p no:playwright --ignore=tests/e2e/test_portal_ui.py --ignore=tests/e2e/test_customer_complaint_journey.py
```

For isolated browser E2E runs, use the separate test services and database (API port **8001**, UI port **8502**, database `fixflow_e2e_test.sqlite3`) rather than the normal local app. Set the corresponding test URLs before running browser tests.

## RC1 validation scope

The final security-gate checks covered:

1. **Cross-customer isolation:** unauthorized customers cannot access another customer's ticket data.
2. **Refund follow-up enforcement:** refund requests at the ₹500 boundary are routed for human approval, including follow-ups.
3. **Process-restart durability:** persisted review state and worker recovery were exercised across separate Python processes.

### Latest local test log (reported 2026-11-02)

- `python scripts/run_local_validation.py --e2e`: **327 backend/offline passed** and **8 browser E2E passed**; deprecation warnings only.
- `python -m pytest -q tests/test_phase10_consolidated.py --tb=short`: **7 passed**, after `FIXFLOW_LOCAL_DEMO=0` was set in the command session.
- `python -m pytest -q tests/test_resolved_evidence_upload.py --tb=short`: **1 passed**.
- The test sets may overlap; these are user-supplied Windows logs, not tests rerun in this documentation update.

### Six mandatory demo cases

| Scenario | Reported outcome | Evidence |
| --- | --- | --- |
| Late delivery | ANSWERED, 2 citations | `tests/test_buildathon_live_six_scenarios.py` |
| Refund-policy request | ANSWERED, 3 citations | same |
| Forgotten password | ANSWERED, 1 citation | same |
| Broken blender | ANSWERED, 1 citation | same |
| Triple charge / legal threat | ESCALATED | same |
| Unsupported custom gift cards | ESCALATED, no citations | same |

These reported outcomes describe the support response; **ANSWERED is not proof that a refund or replacement transaction occurred**. No real payment execution is implemented.

### Two additional safety cases

1. **Refund without verified order ID:** the app must request verification or human review, and never execute a refund.
2. **Damaged product without required photo evidence:** the app must request evidence or route to human review; never execute a refund.

Capture screenshots of these cases and the three mandatory views using the instructions in `screenshots/README.md`. Add image references only after genuine captures exist.

### Private upload handling

`private_evidence/` is private runtime storage for customer-uploaded damage evidence. The feature must remain functional; **its contents must not be committed or included in the submission archive**. Keep the runtime directory and database records on the local machine. Any future deployment requires private persistent storage, authorization, retention and backup controls.
