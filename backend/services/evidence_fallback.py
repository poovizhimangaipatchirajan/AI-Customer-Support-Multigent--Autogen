"""Conservative extractive fallback for unsupported model wording.

No model call, category lookup, or domain-specific rewrite. A source passage is
quoted verbatim, never reinterpreted. Caller must rerun every publication guard.
"""
import re


def extractive_policy_fallback(customer_message, passages):
    if not isinstance(customer_message, str) or not customer_message.strip():
        return None
    if not isinstance(passages, (list, tuple)):
        return None
    # Match content words, not a predefined product or category taxonomy.
    words = lambda s: set(re.findall(r"[a-z]{4,}", s.lower()))
    query_words = words(customer_message)
    ranked = []
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        source_text = passage.get("text")
        if not isinstance(source_text, str) or not source_text.strip():
            continue
        # Exclude oversized or structurally suspicious evidence rather than
        # truncate an obligation or remove a condition from the policy.
        source_text = source_text.strip()
        if len(source_text) > 1800 or "\x00" in source_text:
            continue
        overlap = len(query_words & words(source_text))
        if overlap:
            ranked.append((overlap, source_text))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    # Exact policy wording avoids invented anchors, promises, or omissions.
    return "The relevant customer-facing policy states: " + ranked[0][1]
