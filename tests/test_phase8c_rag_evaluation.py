from eval.evaluate import CASES, INTERNAL, evaluate, score_case

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
