"""Phase 2E.3: offline/read-only customer-index migration preflight.

Does NOT call an embedding API, create vectors, or modify local_index.
Run from the FixFlow AI repository root.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'backend/rag/customer_policy.py'
POLICIES = ROOT / 'policies'
MANIFEST = ROOT / 'local_index/manifest.json'


def check(root=ROOT):
    issues, notes = [], []
    policy_file = root / 'backend/rag/customer_policy.py'
    if not policy_file.is_file():
        return {'status': 'BLOCKED', 'issues': ['Missing customer_policy.py'], 'notes': []}
    spec = importlib.util.spec_from_file_location('fixflow_policy_preflight', policy_file)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    docs = policy.approved_passages()
    if len(docs) != 12:
        issues.append(f'Expected 12 reviewed excerpts, found {len(docs)}; manual review required')
    if len({d['id'] for d in docs}) != len(docs):
        issues.append('Duplicate excerpt IDs')
    for d in docs:
        if not policy.is_approved_customer_passage(d):
            issues.append(f'Invalid approval: {d.get("id")}')
        if d.get('visibility') != 'customer' or d.get('approval') != 'curated-v1':
            issues.append(f'Not customer-approved: {d.get("id")}')
        if not isinstance(d.get('page'), int) or d['page'] < 1:
            issues.append(f'Invalid PDF page: {d.get("id")}')
    sources = sorted({d['source'] for d in docs})
    pdf_available = {}
    try:
        from pypdf import PdfReader
    except ImportError:
        issues.append('pypdf is not installed: cannot verify source page bounds')
        PdfReader = None
    for source in sources:
        path = root / 'policies' / source
        pdf_available[source] = path.is_file()
        if not path.is_file():
            issues.append(f'Missing source PDF: policies/{source}')
            continue
        if PdfReader is not None:
            try:
                count = len(PdfReader(str(path)).pages)
                for d in docs:
                    if d['source'] == source and d['page'] > count:
                        issues.append(f'Page out of range: {source} p.{d["page"]} (total {count})')
            except Exception as exc:
                issues.append(f'Unreadable PDF {source}: {exc}')
    # This validates page EXISTENCE, not semantic equivalence or disclosure authorization.
    legacy = None
    manifest_path = root / 'local_index/manifest.json'
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
            chunks = manifest['chunks']
            digest = hashlib.sha256(json.dumps(chunks, sort_keys=True).encode()).hexdigest()
            if digest != manifest.get('digest'):
                issues.append('Existing index manifest digest mismatch')
            legacy = {'chunks': len(chunks), 'approved': sum(policy.is_approved_customer_passage(d) for d in chunks)}
            if not (root / 'local_index/dense.faiss').is_file():
                issues.append('Existing index missing dense.faiss')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(f'Existing index invalid: {exc}')
    else:
        notes.append('No existing index manifest; migration still requires explicit rebuild authorization')
    notes.extend([
        'Only 12 approved customer excerpts should enter the customer index; do NOT embed raw internal SOP chunks.',
        'A source PDF page reference does not prove that a paraphrase is accurate or authorized for disclosure.',
        'Manual PDF-text and disclosure review is required before any embedding call.',
        'Do not replace local_index with a manifest alone: FAISS vectors must match its document order.',
        'A customer-only index will require an authorized, separately tested build path; current hybrid.build() embeds internal chunks.',
        'Stop API and worker before any eventual two-file index swap; keep a rollback backup.',
    ])
    return {'status': 'BLOCKED' if issues else 'READY_FOR_MANUAL_REVIEW', 'issues': issues, 'notes': notes,
            'approved_excerpt_count': len(docs), 'source_pdf_count': len(sources),
            'source_pdf_available': pdf_available, 'current_index': legacy,
            'proposed_customer_index': {'documents': len(docs), 'raw_internal_sop_documents': 0,
                                        'requires_paid_embedding_approval': True},
            'approved_excerpt_refs': [{'id': d['id'], 'source': d['source'], 'page': d['page']} for d in docs]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='phase2e3_migration_preflight.json')
    args = parser.parse_args()
    report = check()
    output = ROOT / args.output
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Phase 2E.3 preflight: {report["status"]}')
    print(f'Approved customer excerpts: {report.get("approved_excerpt_count", 0)}')
    print(f'Proposed customer index documents: {report.get("proposed_customer_index", {}).get("documents", 0)}')
    print(f'Current index: {report.get("current_index")}')
    for issue in report['issues']:
        print('BLOCKER:', issue)
    for note in report['notes']:
        print('NOTE:', note)
    print(f'Report: {output}')
    print('No embeddings, API calls, or index modifications performed.')
    return 2 if report['issues'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
