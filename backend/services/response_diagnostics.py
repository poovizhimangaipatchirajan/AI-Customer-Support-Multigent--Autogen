"""Read-only response diagnostics. NOT an authorization or semantic entailment engine.

Use to assess real model outputs before deciding whether to enforce new rules.
No state changes, network calls, or model calls occur here.
"""
from dataclasses import dataclass
import re

from backend.services.grounding_guard import check_response_grounding, check_customer_visibility, customer_safe_passages
from backend.services.evidence_quality import missing_citation_domains

_DOMAINS = {
    'shipping': (r'\b(?:ship(?:ping|ment)?|deliver(?:y|ed)?|parcel|tracking|arriv\w*|late|edd)\b',
                 r'\b(?:ship(?:ping|ment)?|deliver(?:y|ed)?|parcel|tracking|arriv\w*|edd|order.status)\b'),
    'returns': (r'\b(?:return|send it back|exchange|cancel)\b',
                r'\b(?:return|send (?:it )?back|exchange|cancel|eligib\w*)\b'),
    'refunds': (r'\b(?:refund|money back|reimburse|charged twice|charged three times)\b',
                r'\b(?:refund|reimburse|money back|payment|review)\b'),
    'account': (r'\b(?:password|log ?in|sign ?in|account access)\b',
                r'\b(?:password|log ?in|sign ?in|reset|account)\b'),
    'product': (r'\b(?:broken|defect\w*|damaged|replacement|warranty)\b',
                r'\b(?:broken|defect\w*|damaged|replacement|warranty|product)\b'),
}

@dataclass(frozen=True)
class Diagnostic:
    codes: tuple[str, ...]
    requested_domains: tuple[str, ...]
    addressed_domains: tuple[str, ...]


def inspect_response(customer_message, response, cited_ids, evidence):
    """Return conservative diagnostic signals, not an approve/publish decision.

    Evidence must be an ID -> passage mapping. The presence of a relevant ID
    is not proof that a claim is entailed by its text.
    """
    if not isinstance(customer_message, str) or not isinstance(response, str):
        raise TypeError('customer_message and response must be strings')
    if not isinstance(evidence, dict):
        raise TypeError('evidence must be an ID-to-passage mapping')
    if not isinstance(cited_ids, (list, tuple)):
        raise TypeError('cited_ids must be a list or tuple')
    codes = []
    if not response.strip():
        codes.append('EMPTY_RESPONSE')
    if not cited_ids:
        codes.append('MISSING_CITATIONS')
    elif len(set(cited_ids)) != len(cited_ids):
        codes.append('DUPLICATE_CITATIONS')
    valid = []
    for identifier in cited_ids:
        passage = evidence.get(identifier) if isinstance(identifier, str) else None
        if not isinstance(passage, dict):
            codes.append('UNKNOWN_CITATION')
        elif passage.get('visibility') != 'customer' or not isinstance(passage.get('text'), str) or not passage['text'].strip():
            codes.append('UNSAFE_OR_EMPTY_CITATION')
        else:
            sanitized = customer_safe_passages([{**passage, 'id': identifier}])
            if not sanitized:
                codes.append('UNSAFE_OR_EMPTY_CITATION')
            else:
                valid.extend(sanitized)
    if valid:
        missing_domains = missing_citation_domains(customer_message, response, valid)
        if missing_domains:
            codes.append("MISSING_DOMAIN_CITATION")
        reason = check_response_grounding(response, valid, customer_message=customer_message)
        if reason:
            codes.append(reason)
    # Diagnostics are observational, not an authorization gate. A fabricated
    # citation must not suppress detection of a claimed completed transaction.
    # Actual publication remains blocked by the strict citation validator.
    if not valid:
        completed = re.search(
            r"\b(?:we|i|our team)\s+(?:have\s+|has\s+)?(?:already\s+)?"
            r"(?:issued|processed|approved|completed|sent)\s+"
            r"(?:your\s+|the\s+|a\s+)?(?:refund|reimbursement|payment|replacement)\b",
            response, re.I,
        )
        if completed and not re.search(r"\b(?:not|never|haven.t|hasn.t|didn.t)\b", response[:completed.end()], re.I):
            codes.append('UNVERIFIED_COMPLETED_ACTION')
        # With no trusted passage, an affirmative numeric delivery promise is
        # unsupported. This is a diagnostic signal, never a policy decision.
        delivery_time = re.search(
            r"\b(?:deliver(?:y|ed)?|arriv(?:al|e|es)?)\b[^.!?\n]{0,60}"
            r"\b(?:within|in|by|takes?)\s+\d+\s*"
            r"(?:business\s+|working\s+|calendar\s+)?(?:days?|hours?|weeks?)\b",
            response, re.I,
        )
        if delivery_time and not re.search(r"\b(?:not|never|cannot|can.t|unable)\b", response[:delivery_time.end()], re.I):
            codes.append('UNSUPPORTED_DELIVERY_TIME')
    visibility = check_customer_visibility(response)
    if visibility:
        codes.append(visibility)
    requested = tuple(k for k, (request_pattern, _) in _DOMAINS.items() if re.search(request_pattern, customer_message, re.I))
    addressed = tuple(k for k, (_, answer_pattern) in _DOMAINS.items() if re.search(answer_pattern, response, re.I))
    # Only flag omissions for clearly multi-domain requests. A safe abstention
    # can be legitimate and requires human judgment; this is not a blocker.
    if len(requested) >= 2 and not re.search(r'\b(?:cannot verify|unable to verify|human review|specialist review)\b', response, re.I):
        if any(k not in addressed for k in requested):
            codes.append('POSSIBLE_MULTI_INTENT_OMISSION')
    return Diagnostic(tuple(dict.fromkeys(codes)), requested, addressed)
