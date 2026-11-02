"""Phase 8C retrieval benchmark. Run: python -m eval.evaluate --mode live --k 4"""
import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((Path(__file__).parent / 'golden_cases.json').read_text(encoding='utf-8'))
INTERNAL = '06_Escalations_and_Soft_Skills_SOP.pdf'


def score_case(case, docs, k=4):
    docs = docs[:k]
    sources = [d.get('source') for d in docs]
    hits = [i for i, src in enumerate(sources, 1) if src == case['expected_source']]
    rank = hits[0] if hits else None
    evidence = any(d.get('source') == case['expected_source'] and case['evidence_phrase'].casefold() in d.get('text', '').casefold() for d in docs)
    leak = any(d.get('source') == INTERNAL or d.get('visibility') == 'internal' for d in docs)
    return {'id': case['id'], 'rank': rank, 'source_hit': bool(rank), 'reciprocal_rank': 1/rank if rank else 0.0, 'ndcg': 1/math.log2(rank+1) if rank else 0.0, 'evidence_hit': evidence, 'internal_leak': leak}


def evaluate(search, k=4):
    rows = [score_case(c, search(c['question'], k=k), k=k) for c in CASES]
    n = len(rows)
    metrics = {
        'cases': n, 'k': k,
        'source_recall_at_k': sum(r['source_hit'] for r in rows)/n,
        'mrr_at_k': sum(r['reciprocal_rank'] for r in rows)/n,
        'source_ndcg_at_k': sum(r['ndcg'] for r in rows)/n,
        'evidence_phrase_hit_at_k': sum(r['evidence_hit'] for r in rows)/n,
        'internal_leaks': sum(r['internal_leak'] for r in rows),
    }
    return {'metrics': metrics, 'cases': rows, 'notes': [
        'Source-level relevance is a proxy, not passage-level precision.',
        'Evidence phrase matching is an automated retrieval check, not answer faithfulness.',
        'No LLM answer correctness or claim-level citation assessment is performed.',
        'Review false negatives: PDF table extraction can split words or phrases.'
    ]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['live', 'offline'], default='offline')
    p.add_argument('--k', type=int, default=4)
    p.add_argument('--out', default='eval/phase8c_report.json')
    args = p.parse_args()
    if args.k < 1: p.error('--k must be positive')
    if args.mode == 'live':
        from backend.rag.hybrid import HybridRetriever
        retriever = HybridRetriever()
        search = lambda q, k: retriever.search(q, k=k, include_internal=False)
    else:
        from backend.rag.hybrid import chunks, tokenize
        from rank_bm25 import BM25Okapi
        docs = [d for d in chunks() if d.get('visibility') == 'customer']
        bm = BM25Okapi([tokenize(d['text']) for d in docs])
        def search(q, k):
            scores = bm.get_scores(tokenize(q))
            ranking = sorted(range(len(docs)), key=lambda i: (-float(scores[i]), i))
            return [docs[i] for i in ranking[:k]]
    report = evaluate(search, k=args.k)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report['metrics'], indent=2))
    print('Report:', out)
    if report['metrics']['internal_leaks']:
        raise SystemExit('FAIL: internal-only SOP leaked into retrieval results')

if __name__ == '__main__':
    main()
