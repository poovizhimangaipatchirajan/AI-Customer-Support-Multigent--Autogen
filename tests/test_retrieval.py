"""Consolidated retrieval tests.

Merged without behaviour changes from:
- test_hybrid.py: regression tests
- test_semantic_rag_generic.py: Offline, model-independent tests for generic semantic retrieval and QueryAgent tool wiring.
- test_semantic_rank_multi_evidence.py: Multi-intent candidate selection in rank_approved. Offline; no embeddings API.
- test_phase8c_rag_evaluation.py: regression tests
"""
import pytest
from backend.rag.hybrid import chunks, tokenize
import ast
from pathlib import Path
import numpy as np
from backend.rag.customer_policy import approved_passages
from backend.rag.semantic_rank import rank_approved
from backend.rag.semantic_rank import MIN_SIMILARITY, meaningful_tokens
from eval.evaluate import CASES, INTERNAL, evaluate, score_case


# ======================================================================
# From test_hybrid.py
# ======================================================================

def test_policies():
    policy_chunks = chunks()

    assert len(policy_chunks) >= 4

    for chunk in policy_chunks:
        assert chunk.get("id")
        assert chunk.get("source")
        assert chunk.get("text")
        assert chunk.get("version")

    sources = {chunk["source"] for chunk in policy_chunks}

    assert len(sources) >= 4
def test_tokens():assert tokenize('Refund and RETURN')==['refund','and','return']


# ======================================================================
# From test_semantic_rag_generic.py
# ======================================================================

def test_paraphrases_use_same_approved_shipping_evidence():
    docs = approved_passages()
    shipping = next(i for i, d in enumerate(docs) if d['source'].startswith('01_'))
    vectors = np.eye(len(docs), dtype='float32')
    # Different wording, same semantic embedding neighborhood.
    for question in ('My parcel says delivered but I did not receive it',
                     'The courier marked my package delivered incorrectly',
                     'Tracking says delivered but nothing arrived'):
        query_vector = vectors[shipping] * 0.95 + vectors[(shipping + 1) % len(docs)] * 0.05
        matches = rank_approved(question, docs, query_vector, vectors)
        assert matches and matches[0]['id'] == docs[shipping]['id']


def test_semantically_unrelated_question_abstains():
    docs = approved_passages()
    vectors = np.eye(len(docs), dtype='float32')
    query_vector = np.ones(len(docs), dtype='float32')
    assert rank_approved('Do you sell helicopter rides?', docs, query_vector, vectors) == []


def test_unapproved_passage_rejected():
    docs = approved_passages()
    forged = dict(docs[0], text='Guaranteed refund for everyone')
    with pytest.raises(ValueError, match='Unapproved'):
        rank_approved('refund', [forged], [1, 0], [[1, 0]])


def test_query_agent_has_rag_tool_and_knowledge_retains_policy_search():
    path = Path(__file__).resolve().parents[1] / 'backend' / 'agents' / 'team.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'make_team')
    calls = {n.args[0].value: n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == 'AssistantAgent' and n.args and isinstance(n.args[0], ast.Constant)}
    def tools(name):
        call = calls[name]
        return [e.id for k in call.keywords if k.arg == 'tools'
                for e in k.value.elts if isinstance(e, ast.Name)]
    assert 'query_rag_context' in tools('QueryAgent')
    assert 'policy_search' in tools('KnowledgeAgent')

@pytest.mark.parametrize('question,target,score,runner', [
    ('A parcel is marked delivered but was never received', '01_', .53, .26),
    ('The courier reports delivery incorrectly', '01_', .465, .274),
    ('My shipment has gone missing', '01_', .44, .29),
    ('What is covered by the standard warranty?', '03_', .486, .411),
])
def test_generic_calibration_accepts_distinct_semantic_winners(question, target, score, runner):
    docs = approved_passages()
    winner = next(i for i, d in enumerate(docs) if d['source'].startswith(target)
                  and (target != '03_' or '30-day platform' in d['text']))
    other = next(i for i in range(len(docs)) if i != winner)
    vectors = np.eye(len(docs), dtype='float32')
    query = vectors[winner] * score + vectors[other] * runner
    # Input vector is normalized by rank_approved; preserve target cosine
    # ordering, not the absolute calibrated values in this synthetic fixture.
    matches = rank_approved(question, docs, query, vectors)
    assert matches and matches[0]['id'] == docs[winner]['id']


def test_generic_calibration_abstains_on_ambiguous_near_tie():
    docs = approved_passages()
    vectors = np.eye(len(docs), dtype='float32')
    query = vectors[0] * .51 + vectors[1] * .49
    assert rank_approved('Please explain the policy', docs, query, vectors) == []


# ======================================================================
# From test_semantic_rank_multi_evidence.py
# ======================================================================

DOCS = approved_passages()
N = len(DOCS)
EYE = np.eye(N, dtype='float32')


def _blend(weights):
    q = np.zeros(N, dtype='float32')
    for i, w in weights.items():
        q[i] = w
    return q


def _exclusive_tokens(a, b, limit=6):
    toks = sorted(meaningful_tokens(DOCS[a]['text']) - meaningful_tokens(DOCS[b]['text']))
    assert len(toks) >= limit
    return toks[:limit]


def _query_text(a, b):
    # Lexical support for both passages.
    return ' '.join(_exclusive_tokens(a, b) + _exclusive_tokens(b, a))


def test_secondary_passage_with_lexical_support_is_retained():
    a, b = 1, 5   # two returns SOP passages
    query = _blend({a: .70, b: .66, 9: .10})
    ids = [m['id'] for m in rank_approved(_query_text(a, b), DOCS, query, EYE)]
    assert DOCS[a]['id'] in ids and DOCS[b]['id'] in ids


def test_secondary_passage_without_lexical_support_is_excluded():
    a, b = 1, 5
    query = _blend({a: .70, b: .66, 9: .10})
    text = ' '.join(_exclusive_tokens(a, b))   # nothing from b
    ids = [m['id'] for m in rank_approved(text, DOCS, query, EYE)]
    assert ids and ids[0] == DOCS[a]['id']
    assert DOCS[b]['id'] not in ids


def test_secondary_passage_beyond_band_is_excluded_even_with_lexical_support():
    a, b = 1, 5
    query = _blend({a: .80, b: .40, 9: .10})   # cosine gap well above 0.10
    ids = [m['id'] for m in rank_approved(_query_text(a, b), DOCS, query, EYE)]
    assert DOCS[b]['id'] not in ids


def test_absolute_similarity_floor_still_applies_to_secondary():
    a, b = 1, 5
    query = _blend({a: .95, b: .20, 9: .05})
    ids = [m['id'] for m in rank_approved(_query_text(a, b), DOCS, query, EYE)]
    assert DOCS[b]['id'] not in ids
    for m in rank_approved(_query_text(a, b), DOCS, query, EYE):
        assert m['semantic_score'] >= MIN_SIMILARITY


def test_k_limit_respected():
    query = _blend({1: .70, 2: .69, 3: .68, 4: .67, 5: .66})
    text = ' '.join(sorted(set().union(*(meaningful_tokens(DOCS[i]['text']) for i in (1, 2, 3, 4, 5)))))
    assert len(rank_approved(text, DOCS, query, EYE, k=2)) <= 2


def test_ambiguous_top_without_lexical_support_still_abstains():
    query = _blend({0: .51, 1: .49})
    assert rank_approved('Please explain the policy', DOCS, query, EYE) == []


def test_unrelated_query_still_abstains():
    assert rank_approved('Do you sell helicopter rides?', DOCS,
                         np.ones(N, dtype='float32'), EYE) == []


def test_unapproved_passage_still_rejected():
    forged = dict(DOCS[0], text='Guaranteed refund for everyone')
    with pytest.raises(ValueError, match='Unapproved'):
        rank_approved('refund', [forged], [1, 0], [[1, 0]])


# ======================================================================
# From test_phase8c_rag_evaluation.py
# ======================================================================

def doc(source, text='', visibility='customer'):
    return {'source': source, 'text': text, 'visibility': visibility}

def test_golden_dataset_is_unique_and_nonempty():
    assert len(CASES) >= 10
    assert len({c['id'] for c in CASES}) == len(CASES)
    assert all(c['expected_source'] != INTERNAL and c['evidence_phrase'] for c in CASES)

def test_first_rank_scores_full_credit():
    c = CASES[0]
    r = score_case(c, [doc(c['expected_source'], c['evidence_phrase'])])
    assert r['rank'] == 1 and r['reciprocal_rank'] == 1 and r['ndcg'] == 1
    assert r['evidence_hit'] and not r['internal_leak']

def test_wrong_source_is_not_relevant():
    c = CASES[0]
    r = score_case(c, [doc('02_Returns_and_Refunds_SOP.pdf', c['evidence_phrase'])])
    assert not r['source_hit'] and not r['evidence_hit']

def test_internal_sop_leak_is_detected():
    c = CASES[0]
    assert score_case(c, [doc(INTERNAL)])['internal_leak']

def test_second_rank_has_discounted_score():
    c = CASES[0]
    r = score_case(c, [doc('02_Returns_and_Refunds_SOP.pdf'), doc(c['expected_source'])])
    assert r['rank'] == 2 and 0 < r['ndcg'] < 1

def test_aggregate_metrics_perfect_synthetic_search():
    lookup = {c['question']: c for c in CASES}
    def search(q,k):
        c=lookup[q]
        return [doc(c['expected_source'], c['evidence_phrase'])]
    metrics=evaluate(search)['metrics']
    assert metrics['source_recall_at_k'] == 1
    assert metrics['evidence_phrase_hit_at_k'] == 1
    assert metrics['internal_leaks'] == 0

def test_aggregate_metrics_empty_results():
    metrics=evaluate(lambda q,k: [])['metrics']
    assert metrics['source_recall_at_k'] == 0
    assert metrics['mrr_at_k'] == 0
