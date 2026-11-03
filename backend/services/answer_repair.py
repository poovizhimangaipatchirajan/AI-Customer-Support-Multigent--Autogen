"""Conservative, deterministic pre-persistence repair of isolated timing claims.

This is NOT semantic claim verification. A repaired answer must still pass the
backend grounding, citation, visibility and risk checks before publication.
"""
import re
from backend.services.grounding_guard import check_response_grounding, check_customer_visibility

_REPAIRABLE = frozenset({'UNSUPPORTED_DELIVERY_TIME'})
_SPLIT = re.compile(r'(?<=[.!?])\s+|\n+')


def repair_isolated_time_claim(draft, passages):
    """Return (candidate, reason) or (original, None) if safe repair is impossible.

    Remove only a complete, standalone unsupported timing sentence. Never rewrite
    values, invent facts, remove internal details, or bypass other rejection types.
    """
    if not isinstance(draft, str) or not draft.strip():
        return draft, None
    if check_customer_visibility(draft):
        return draft, None
    reason = check_response_grounding(draft, passages)
    if reason not in _REPAIRABLE:
        return draft, None
    sentences = [s.strip() for s in _SPLIT.split(draft) if s.strip()]
    if len(sentences) < 2:
        return draft, None
    retained = []
    removed = 0
    for sentence in sentences:
        failure = check_response_grounding(sentence, passages)
        if failure == reason:
            removed += 1
        elif failure is None:
            retained.append(sentence)
        else:
            return draft, None
    if removed != 1 or not retained:
        return draft, None
    candidate = ' '.join(retained)
    # Do not publish a sentence fragment, or a reply with a dangling reference
    # to the removed assertion. No semantic inference is made here.
    if re.search(r'\b(?:as (?:mentioned|stated|noted) above|this (?:means|guarantees)|therefore|thus)\b', candidate, re.I):
        return draft, None
    if check_response_grounding(candidate, passages) is not None:
        return draft, None
    if check_customer_visibility(candidate) is not None:
        return draft, None
    return candidate, reason
