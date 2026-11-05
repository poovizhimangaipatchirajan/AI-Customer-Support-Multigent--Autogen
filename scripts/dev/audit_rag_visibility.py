"""FixFlow Phase 2E.2: read-only, offline RAG visibility/index audit.

Run from the repository root: python scripts/audit_rag_visibility.py
No OpenAI calls, index modifications, or network access.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_MODULE = ROOT / 'backend' / 'rag' / 'customer_policy.py'
INDEX_MANIFEST = ROOT / 'local_index' / 'manifest.json'
INDEX_DENSE = ROOT / 'local_index' / 'dense.faiss'
SOP_DIR = ROOT / 'policies'
EXPECTED_SOPS = (
    '01_Shipping_and_Delivery_SOP.pdf',
    '02_Returns_and_Refunds_SOP.pdf',
    '03_Product_and_Warranty_SOP.pdf',
    '04_Account_and_Login_SOP.pdf',
    '05_Payments_and_Billing_SOP.pdf',
    '06_Escalations_and_Soft_Skills_SOP.pdf',
)


def main():
    problems = []
    notes = []
    print('FixFlow AI | Phase 2E.2 offline RAG visibility audit')
    if not POLICY_MODULE.is_file():
        print('FAIL: backend/rag/customer_policy.py missing; Phase 2E not installed')
        return 2
    spec = importlib.util.spec_from_file_location('fixflow_customer_policy_audit', POLICY_MODULE)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    approved = policy.approved_passages()
    print(f'Approved customer excerpts: {len(approved)}')
    if not approved:
        problems.append('No approved excerpts')
    ids = [d['id'] for d in approved]
    if len(ids) != len(set(ids)):
        problems.append('Duplicate approved excerpt IDs')
    for d in approved:
        if not policy.is_approved_customer_passage(d):
            problems.append(f'Approved excerpt failed its own validator: {d.get("id")}')
        if d.get('source') not in EXPECTED_SOPS or not isinstance(d.get('page'), int) or d['page'] < 1:
            problems.append(f'Invalid source/page metadata: {d.get("id")}')
    for filename in EXPECTED_SOPS:
        if not (SOP_DIR / filename).is_file():
            notes.append(f'Source PDF missing locally: policies/{filename}')
    if INDEX_MANIFEST.is_file():
        try:
            manifest = json.loads(INDEX_MANIFEST.read_text(encoding='utf-8-sig'))
            docs = manifest['chunks']
            digest = hashlib.sha256(json.dumps(docs, sort_keys=True).encode('utf-8')).hexdigest()
            if digest != manifest.get('digest'):
                problems.append('Manifest digest mismatch')
            if not isinstance(docs, list):
                raise ValueError('chunks is not a list')
            eligible = [d for d in docs if policy.is_approved_customer_passage(d)]
            labeled = [d for d in docs if isinstance(d, dict) and d.get('visibility') == 'customer']
            rejected_labeled = len(labeled) - len(eligible)
            print(f'Index documents: {len(docs)}')
            print(f'Index customer-labeled: {len(labeled)}')
            print(f'Index actually approved: {len(eligible)}')
            print(f'Customer-labeled but rejected: {rejected_labeled}')
            if rejected_labeled:
                notes.append('Legacy or unapproved customer labels are rejected (expected for an old index)')
            if not eligible:
                notes.append('No approved excerpts in current index: customer RAG has no approved evidence; rebuild requires separate authorization')
            if not INDEX_DENSE.is_file():
                problems.append('Manifest exists but dense.faiss is missing')
        except (ValueError, KeyError, TypeError, OSError) as exc:
            problems.append(f'Cannot validate index manifest: {exc}')
    else:
        notes.append('No local_index/manifest.json found: no index available for customer retrieval')
        if INDEX_DENSE.is_file():
            problems.append('dense.faiss exists but manifest.json is missing')
    print('\nFindings:')
    for note in notes:
        print('  NOTE:', note)
    for issue in problems:
        print('  FAIL:', issue)
    if problems:
        print('\nRESULT: FAIL — resolve integrity/configuration issues before proceeding')
        return 2
    print('\nRESULT: PASS — read-only checks completed; this is NOT a production security sign-off')
    return 0


if __name__ == '__main__':
    sys.exit(main())
