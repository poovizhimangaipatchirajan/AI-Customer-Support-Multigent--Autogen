"""Read-only refund-review context for follow-up messages."""
import re

TIMING = re.compile(r"\b(?:how long|how many days|when|timeframe|timeline|processing time|take generally|take to process)\b", re.I)
REFUND = re.compile(r"\b(?:refund|money back|reimbursement)\b", re.I)

def refund_timing_question(text):
    return bool(TIMING.search(text or "") and REFUND.search(text or ""))

def review_context(c, ticket_id):
    """Verified decision state; never treat review approval as payment execution."""
    try:
        row = c.execute("SELECT status, execution_status FROM refund_reviews WHERE ticket_id=?", (ticket_id,)).fetchone()
    except Exception:
        return "Refund review status unavailable. Do not infer a decision."
    if not row:
        return "No refund review decision recorded."
    state = row['status']
    execution = row['execution_status']
    return f"Refund review decision: {state}. Payment execution status: {execution}. Review approval is not payment execution."

def safe_timing_fallback(message, context):
    if not refund_timing_question(message):
        return None
    return ("I cannot confirm a general refund-processing timeframe from verified customer-facing policy evidence. "
            + context + " Our support team can provide an update when verified processing information is available.")
