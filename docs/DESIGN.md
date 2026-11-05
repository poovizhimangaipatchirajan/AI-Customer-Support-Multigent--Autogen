# Design and safety details

[← Back to README](../README.md)

## Refund flow

| Request | Automatic checks | Outcome |
|---|---|---|
| Damaged/defective item, **under ₹500** | Clear amount, no risk flags, linked order owned by the customer and **delivered**, amount ≤ order total, a genuine photo uploaded, order never auto-refunded, **≤ 1 automatic acceptance per customer per 365 days** | All pass → **queued for refund processing** without a human review step. Any fail → asked for details or human review |
| Any refund **₹500 or more** (including exactly ₹500) | Same order and evidence checks | **Always human review**: admin assessment and APPROVED/REJECTED decision |
| Fraud, legal threat, payment dispute | — | **Always human review**, any amount |

Passing the automatic checks is eligibility triage, not a payment and not an approval. The customer is told explicitly that no money has been refunded yet. Photos are confirmed as genuine uploaded images, not inspected for content; the per-customer annual limit is the fraud control. Every acceptance and decision is audited with `execution_status: NOT_CONFIGURED`. A production deployment would add the payment provider's refund API (idempotent, with transaction reference) and sampling audits of automatic acceptances.

## Grounding and citation controls

- Customer citations come only from the **approved customer index** (15 curated passages: 14 from the SOP PDFs in `policies/`, plus one `NovaCart_App_Help` passage describing this app's password-reset screen, which must be kept in step with the UI); the internal `06_Escalations_and_Soft_Skills_SOP.pdf` is never indexed. Index validation failure never falls back to another index.
- Retrieval: FAISS dense + BM25 lexical, fused with Reciprocal Rank Fusion, then a conservative ranker (cosine floor 0.35, margin and tie bands) that abstains rather than guess.
- Sentences promising that a team will contact the customer, or directing them to an app screen or option no cited passage names, are removed before validation; if nothing substantive remains the reply fails closed to human review.
- The **grounding guard** rejects return-window start dates the passage does not state, one category's window applied to another, unsupported numbers, and invented citation IDs. Claims that an item belongs to a category are rewritten to say the category is confirmed during verification.
- Every substantively answered policy domain needs its own cited evidence; missing or invalid structured handoffs fail closed to human review.
- Retrieved documents and customer text are treated as data, never instructions (prompt-injection tests in `tests/`).
- MCP tools are read-only demo tools; web search is restricted to fixed public topics and never receives customer data. Neither is NovaCart policy.

## Human-in-the-loop

When an administrator approves an escalated reply, the worker runs a short AutoGen conversation: `SupportReviewer` (a `UserProxyAgent`) states the human decision and LogAgent records it with the `record_human_review` tool. LogAgent cannot change the decision; a stuck run is retried once, then marked failed and audited. The UI shows it as a fifth workflow step after the four agents.

## Tamil and Tanglish

The approved policy passages are English, so every check runs in English:

1. The worker detects the language of the latest customer message (Tamil script, or romanised Tamil = Tanglish). English tickets make no extra call.
2. A Tamil or Tanglish message is translated to English; retrieval, the four agents and the grounding guard all work on that English text. Risk screening runs on the original words at ticket creation and again on the translation; a flag found only in the translation is added (it can only make the ticket stricter).
3. The English reply is validated exactly as for an English ticket, then translated. The customer sees the translation with the verified English reply below it.
4. A translation is rejected if it drops, changes or adds any number (amount, day count, order ID); the English reply is then published unchanged. Both steps are audited (`REPLY_TRANSLATED` / `REPLY_TRANSLATION_SKIPPED`).

QueryAgent reports the language; the complaint log shows it.

Fixed messages the backend sends itself (the refund-amount question, the automatic-acceptance notice) have reviewed Tamil and Tanglish versions in `backend/services/fixed_messages.py`, chosen by the language of the customer's message; they never go through a model, and tests check every version keeps the same numbers and placeholders. The Help Center screens the original words for risk, answers from the English translation of the question, and translates the answer with the same number check, keeping the English below. Screen labels and buttons stay English.

## Complaint log

Admin **Audit logs** shows the log as a table with CSV download (`/admin/rubric/complaints.csv`). Columns: ticket_id, timestamp, customer_name / order_id, complaint, category, urgency, sentiment (QueryAgent's emotion: Angry, Frustrated, Worried, Confused, Happy, Sad or Neutral), language (English, Tamil or Tanglish), kb_sources, response, status, outcome, escalation_reason. Category, urgency and emotion come from QueryAgent's classification; keyword rules only fill gaps, and backend risk flags always force High urgency. Emotion affects tone and reporting only, never escalation.

## Known limitations

- **Late or undelivered orders** are covered by one curated passage; there is no live tracking, so replies never state an order's actual status.
- **Product-to-category mapping is not in the approved policy**, so replies never claim which category an item belongs to.
- **Short wrong-size/colour exchange questions (TC08) can retrieve no passage** above the similarity floor; the case escalates honestly. Thresholds are deliberately not lowered.
- Tamil replies are machine translations of the verified English reply; numbers are checked, wording is not, which is why the English original is always shown with them.
- Photos are confirmed as genuine images but never inspected for content; no payment provider is connected.
- SQLite and a single worker suit a demo; production would use PostgreSQL and several workers.

## Repository hygiene

- `.gitignore` ignores `.env`, databases, `docker_data/`, `private_evidence/`, index backups, `*.bak*`, regression logs and complaint-log CSV/XLSX exports; it allows `evidence/screenshots/*.png`.
- `private_evidence/` is private runtime storage for customer uploads and is never committed. Any deployment needs private persistent storage, authorization, retention and backup controls.
- Screenshots use fictional accounts and orders only.
