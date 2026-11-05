"""Generic, approval-bound semantic ranking for customer-facing policy evidence.

Cosine similarity is a relevance signal, not proof of policy entailment.
Do not use this module to authorize case-specific actions.
"""
import re
import numpy as np
from backend.rag.customer_policy import is_approved_customer_passage

MIN_SIMILARITY = 0.35
MIN_MARGIN = 0.055
# Near-tied passages are always retained. A multi-intent query embeds as a
# blend, so a genuinely relevant secondary passage can sit further below the
# top. Such passages need BOTH the absolute similarity floor and lexical
# support within SECONDARY_BAND of the top. Calibrate SECONDARY_BAND against
# real approved-index scores before relying on it end to end.
TIE_BAND = 0.025
SECONDARY_BAND = 0.10
RRF_K = 60
STOPWORDS = frozenset(('the a an is are was were my your our i me you to for of and or in on at what where when how does do can could would please tell about policy novacart'.split()))


def meaningful_tokens(text):
    return {t for t in re.findall(r'[a-z0-9]+', (text or '').lower())
            if len(t) >= 3 and t not in STOPWORDS}


def normalize_vectors(vectors, count):
    array = np.asarray(vectors, dtype='float32')
    if (array.ndim != 2 or array.shape[0] != count or array.shape[1] < 1
            or not np.isfinite(array).all()):
        raise ValueError('Invalid semantic embedding matrix')
    lengths = np.linalg.norm(array, axis=1)
    if (lengths == 0).any():
        raise ValueError('Zero-length semantic embedding')
    return array / lengths[:, None]


def rank_approved(query, documents, query_vector, document_vectors, *, k=4,
                  min_similarity=MIN_SIMILARITY, min_margin=MIN_MARGIN,
                  tie_band=TIE_BAND, secondary_band=SECONDARY_BAND):
    """Hybrid dense/BM25/RRF candidate ranking, followed by conservative
    semantic evidence selection. Thresholds are corpus-wide, never query rules.

    The dense margin is used to reject ambiguous evidence: a high-scoring
    passage is not automatically an answer. Callers must still verify claims.
    """
    if not isinstance(query, str) or not query.strip() or k <= 0 or not documents:
        return []
    if not all(is_approved_customer_passage(d) for d in documents):
        raise ValueError('Unapproved semantic retrieval document')
    matrix = normalize_vectors(document_vectors, len(documents))
    vector = normalize_vectors([query_vector], 1)
    if vector.shape[1] != matrix.shape[1]:
        raise ValueError('Embedding dimension mismatch')
    scores = matrix @ vector[0]
    # BM25 scores computed locally to keep this small ranking module usable
    # in offline tests; no separate dependency or network call is needed.
    import math
    from collections import Counter
    corpus = [list(meaningful_tokens(d['text'])) for d in documents]
    tokens = list(meaningful_tokens(query))
    lengths = [len(doc) for doc in corpus]
    avg_len = sum(lengths) / max(1, len(lengths))
    df = Counter(token for doc in corpus for token in set(doc))
    lexical = np.zeros(len(documents), dtype='float32')
    for i, doc in enumerate(corpus):
        freq = Counter(doc)
        for token in tokens:
            if token not in freq:
                continue
            idf = math.log(1 + (len(corpus) - df[token] + 0.5) / (df[token] + 0.5))
            tf = freq[token]
            lexical[i] += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * lengths[i] / max(avg_len, 1)))
    dense_order = sorted(range(len(documents)), key=lambda i: (-float(scores[i]), i))
    lexical_order = sorted(range(len(documents)), key=lambda i: (-float(lexical[i]), i))
    # Combine rankings only when lexical evidence exists; empty BM25 results
    # must not arbitrarily bias RRF toward the first catalog passage.
    has_lexical = bool(np.max(lexical) > 0)
    dense_rank = {i: r for r, i in enumerate(dense_order, 1)}
    lexical_rank = {i: r for r, i in enumerate(lexical_order, 1)}
    rrf = {i: 1 / (RRF_K + dense_rank[i]) +
           (1 / (RRF_K + lexical_rank[i]) if has_lexical and lexical[i] > 0 else 0)
           for i in range(len(documents))}
    # A semantic relevance gate remains independent of fusion: BM25 alone
    # cannot turn an irrelevant excerpt into customer-facing policy evidence.
    top = dense_order[0]
    runner_up = float(scores[dense_order[1]]) if len(documents) > 1 else -1.0
    if float(scores[top]) < min_similarity:
        return []
    # Ambiguous dense-only matches are not sufficient evidence.  Retain
    # near-tied candidates when the top passage also has lexical support,
    # allowing closely related approved passages to be retrieved together.
    if (len(documents) > 1 and float(scores[top]) - runner_up < min_margin
            and float(lexical[top]) <= 0):
        return []
    # The absolute floor applies to every candidate. Outside the tight tie band
    # a candidate must also have lexical support and stay within the wider
    # secondary band, so BM25 alone never admits a passage and a purely dense
    # straggler is not treated as evidence. Approval is validated above.
    eligible = [i for i in dense_order if float(scores[i]) >= min_similarity and (
                float(scores[top]) - float(scores[i]) <= tie_band or
                (float(scores[top]) - float(scores[i]) <= secondary_band
                 and float(lexical[i]) > 0))]
    eligible.sort(key=lambda i: (-rrf[i], -float(scores[i]), i))
    return [dict(documents[i], semantic_score=round(float(scores[i]), 5),
                 hybrid_score=round(float(rrf[i]), 7)) for i in eligible[:k]]
