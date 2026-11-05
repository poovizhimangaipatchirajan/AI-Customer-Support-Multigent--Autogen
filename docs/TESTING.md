# Testing and validation

[← Back to README](../README.md)

## Test layout

`tests/` holds **27 feature-grouped files** (974 offline tests) plus `tests/conftest.py`. The conftest makes a plain `python -m pytest` **offline and side-effect free**: unless a live flag is set, it replaces the OpenAI/Serper keys with dummies, points OpenAI at an unroutable address and disables SMTP, so tests can never make paid calls or send email.

| File | Covers |
| --- | --- |
| `test_auth_accounts.py` | Registration, verification, login, sessions, evidence-upload security |
| `test_risk_screening.py` | ₹500 inclusive threshold, fraud/legal/payment-dispute flags, multilingual risk, amount attribution |
| `test_refund_workflow.py` | Refund clarification, follow-ups, approval prerequisites, review decisions, case closure |
| `test_auto_refund.py` | Under-₹500 automatic eligibility checks and the one-per-customer-per-year limit |
| `test_grounding_return_windows.py`, `test_grounding_guard.py`, `test_quantitative_grounding.py` | Return-window anchors, category windows, category-membership repair, numeric claims |
| `test_evidence_quality.py`, `test_customer_evidence_domains.py` | Citation sufficiency per policy domain, cited-text support, evidence fallbacks |
| `test_retrieval.py`, `test_customer_index_visibility.py` | Hybrid FAISS + BM25 + RRF ranking, approved-index validation, internal-SOP isolation |
| `test_approved_only_lane.py` | Approved-only index runtime (run in its own process, see below) |
| `test_agents_prompts.py` | Four-agent prompts, structured citation handoff |
| `test_human_review.py` | Human-in-the-loop: `UserProxyAgent` records the specialist's decision; LogAgent cannot alter it; retries and failure are audited |
| `test_worker.py` | Leases, recovery, retry exhaustion, fail-closed publication |
| `test_load_resilience.py` | Stuck model calls, cancellation, busy database and Help Center 503s |
| `test_api_journeys.py` | End-to-end customer/admin API journeys, customer isolation |
| `test_help_center.py`, `test_mcp_web_search.py` | Help Center answers, MCP tools, restricted web search |
| `test_language.py` | Tamil/Tanglish detection, English retrieval and validation, number-preserving translation, translated risk flags |
| `test_support_claims.py` | Staff-contact promises and unsupported app directions removed, Tamil/Tanglish lawyer and repeated-charge wording, app-help reset passage |
| `test_logging_ui.py` | Complaint-log columns and CSV, ticket classification, UI error handling |
| `test_scenarios.py` | Offline buildathon and realistic customer scenarios |
| `test_buildathon_live_six_scenarios.py` and 3 other live files | **Opt-in paid** live runs (skipped by default) |
| `tests/e2e/test_screenshot_journeys.py` | **Opt-in** Playwright browser journeys against the running Docker stack |

## Offline regression (PowerShell, project root)

```powershell
python -m pytest -q
$env:FIXFLOW_CUSTOMER_INDEX_MODE = "approved_only"; python -m pytest -q tests/test_approved_only_lane.py; Remove-Item Env:FIXFLOW_CUSTOMER_INDEX_MODE
```

| Batch (run 2026-11-05) | Result |
| --- | --- |
| Full offline suite | **974 passed, 36 skipped** (skipped = opt-in live tests, not passes) |
| Approved-only lane | **35 passed** |

## Live acceptance (opt-in, uses OpenAI)

```powershell
$env:FIXFLOW_RUN_BUILDATHON_LIVE = "1"; $env:FIXFLOW_CUSTOMER_INDEX_MODE = "approved_only"; python -m pytest -q -s tests/test_buildathon_live_six_scenarios.py
```

| Scenario (live, approved-only index, 2026-11-05) | Outcome |
| --- | --- |
| Where is my order? | ANSWERED, cites the in-transit and delivered-not-received shipping passages; no invented ETA or tracking |
| Return shoes bought last week | ANSWERED (occasionally routed to review when the agent flags a case decision); 14-day fashion window, conditions, no invented start date |
| Forgot password | ANSWERED, identity verification required |
| Broken blender, replacement | ESCALATED to human review, with policy guidance |
| Charged three times, bank and lawyer | ESCALATED (`LEGAL`, `PAYMENT_DISPUTE`) |
| Gift cards for 500 employees | ESCALATED, no citations (no matching policy) |
| TC02 fashion return / TC07 damaged cable ₹299 / TC08 wrong-size headphones | ANSWERED / ESCALATED / ESCALATED |

**Why some "Resolved" cases are escalated on purpose.** The assignment expects the broken blender to be resolved. FixFlow answers it with the grounded guidance it can support (Dead-on-Arrival assessment, technician diagnosis, required box and accessories) and routes the **replacement decision** to a human, because the approved policy does not say which category a blender belongs to and a replacement is a consequential remedy. TC07 (damaged cable, no order or photo supplied) escalates because verification is missing; with a linked delivered order and a photo, the under-₹500 automatic path accepts it (screenshot 9). ANSWERED is never proof that a refund or replacement happened.

## Retrieval evaluation

```powershell
python -m eval.evaluate --mode live --k 4
```

30 labelled questions in `eval/golden_cases.json`, scored on the approved-only index the app uses (report: `eval/retrieval_report_approved_only.json`).

| Question group | Result |
| --- | --- |
| 20 specific questions answerable from approved customer passages | **20/20 correct source in top 4** |
| 4 questions on internal or excluded topics | Correctly **no customer passage returned** |
| 6 vague template questions ("What does the policy say about refund?") | Abstained: several passages tie, so the conservative ranker returns none rather than guess |
| Internal-SOP leaks | **0** |

Overall source recall is 67% because the golden set was written against the full SOP PDFs; the offline keyword baseline over all customer chunks (`--mode offline`) reaches 90% recall without the approval and abstention safeguards.

## Browser screenshots (Playwright, opt-in, uses OpenAI through the worker)

```powershell
docker compose up -d
$env:FIXFLOW_RUN_E2E = "1"; python -m pytest tests/e2e -q
```

Each run creates fresh fictional accounts directly in the Docker database (no registration email) and writes `01_…` to `20_…` to `evidence/screenshots/`.

## Load and timeout handling

| Situation | Behaviour |
| --- | --- |
| Many tickets at once | Ticket creation only queues work; one background worker processes tickets in order; the UI refreshes progress every 2 s |
| Slow or stuck AI call | Each model call limited to 45 s (2 retries), each agent run to 150 s; a timed-out run is cancelled, retried once from a clean state, then escalated |
| Worker crash | 180 s lease with heartbeat; the job is requeued once, then escalated; never processed twice |
| Help Center provider slow | 10 s embedding limit; HTTP 503 "The Help Center is busy right now…" with Retry-After |
| Database busy (SQLite lock) | HTTP 503 "The service is busy…" with Retry-After |
| Frontend waiting on the API | 30 s limit and a clear timeout or busy message, never a crash |

Scaling beyond a demo would mean PostgreSQL and several worker processes; the single worker keeps ordering and recovery simple.
