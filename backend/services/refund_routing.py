"""Backend-owned, non-LLM mandatory refund review guard."""
import math
import re

_REFUND = re.compile(r"\b(?:refund|reimburse(?:ment)?|money\s+back)\b", re.I)


def mandatory_refund_review(ticket):
    """Return True for a >= INR 500 refund request or an existing mandatory flag.

    This is a status gate, not permission to execute any financial transaction.
    Do not treat an order value as a requested refund unless refund intent exists.
    """
    flags = set(filter(None, str(ticket.get("risk") or "").split(",")))
    if "REFUND_GE_500" in flags:
        return True
    amount = ticket.get("claimed_amount")
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return False
    if not math.isfinite(amount) or amount < 500:
        return False
    text = " ".join(str(ticket.get(k) or "") for k in ("subject", "description"))
    return bool(_REFUND.search(text))
