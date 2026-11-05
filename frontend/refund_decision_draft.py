"""Editable customer-message drafts for *recorded* human refund decisions.

This is presentation-only. It does not authorize refunds, change ticket status,
or execute payments. The administrator must review and explicitly publish.
"""


def decision_response_draft(review: dict) -> str:
    status = str(review.get("status") or "").upper()
    reason = str(review.get("reason") or "").strip()
    if status not in ("APPROVED", "REJECTED"):
        raise ValueError("A recorded APPROVED or REJECTED decision is required")
    if not reason:
        raise ValueError("A recorded decision reason is required")
    if status == "APPROVED":
        return (
            "Our support team has reviewed your refund request and approved "
            "the refund review for further processing.\n\n"
            f"Review decision reason: {reason}\n\n"
            "This is a review decision only. No refund or payment has been "
            "issued, and any remaining processing steps must be completed "
            "before funds can be returned."
        )
    return (
        "Our support team has reviewed your refund request and has not "
        "approved it.\n\n"
        f"Review decision reason: {reason}\n\n"
        "If you have additional relevant information or supporting evidence, "
        "please reply to this ticket so our team can review it. "
        "No refund or payment has been issued."
    )
