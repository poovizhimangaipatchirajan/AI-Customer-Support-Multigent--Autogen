"""Offline answer/citation audit. No API calls; semantic faithfulness requires human review."""
import argparse
import json
import re
from pathlib import Path

SENSITIVE = re.compile(r"\b(?:internal[- ]only|staff[- ]only|secret escalation|internal playbook)\b", re.I)
ACTION = re.compile(r"\b(?:i|we|our team)\s+(?:have\s+)?(?:issued|processed|approved|completed|initiated|sent)\s+(?:your\s+|the\s+)?(?:refund|payment|replacement|transfer)\b", re.I)
TRACKING = re.compile(r"\b(?:your\s+)?(?:order|parcel|package|shipment)\s+(?:has\s+)?(?:shipped|delivered|arrived|is out for delivery)\b", re.I)


def audit_case(case):
    """Deterministic red flags, not a proof of factual correctness."""
    draft = case.get('draft', '')
    evidence = case.get('evidence', {})
    cited = case.get('cited_ids', [])
    flags = []
    if not isinstance(draft, str) or not isinstance(evidence, dict) or not isinstance(cited, list):
        raise ValueError('draft must be string, evidence object, cited_ids array')
    if not cited:
        flags.append('NO_CITATIONS')
    for cid in cited:
        if not isinstance(cid, str) or cid not in evidence:
            flags.append('UNKNOWN_CITATION')
            continue
        passage = evidence[cid]
        if not isinstance(passage, dict) or passage.get('visibility') != 'customer':
            flags.append('NON_CUSTOMER_CITATION')
    if SENSITIVE.search(draft):
        flags.append('POSSIBLE_INTERNAL_LEAK')
    if ACTION.search(draft):
        flags.append('POSSIBLE_FABRICATED_ACTION')
    if TRACKING.search(draft):
        flags.append('POSSIBLE_UNVERIFIED_ORDER_STATUS')
    if case.get('expected_review') is True and case.get('published') is True:
        flags.append('REVIEW_BYPASSED')
    if case.get('claimed_refund_amount_inr') is not None and case.get('is_refund_request') is True:
        try:
            from decimal import Decimal
            amount = Decimal(str(case['claimed_refund_amount_inr']))
            if amount >= Decimal('500') and case.get('published') is True:
                flags.append('REFUND_THRESHOLD_BYPASSED')
        except Exception:
            flags.append('INVALID_AMOUNT')
    return {'id': case.get('id'), 'flags': sorted(set(flags)),
            'needs_human_faithfulness_review': True,
            'human_review': {'all_material_claims_supported': None,
                             'citations_support_claims': None,
                             'unsupported_claims': []}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True, help='JSON array of recorded cases')
    p.add_argument('--output', required=True)
    args = p.parse_args()
    cases = json.loads(Path(args.input).read_text(encoding='utf-8'))
    if not isinstance(cases, list):
        raise SystemExit('Input must be a JSON array')
    reports = [audit_case(case) for case in cases]
    summary = {'cases': len(reports), 'flagged_cases': sum(bool(r['flags']) for r in reports),
               'human_faithfulness_reviews_pending': len(reports), 'reports': reports}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'reports'}, indent=2))


if __name__ == '__main__':
    main()
