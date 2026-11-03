"""Fully offline Phase 2E.4 tests; no OpenAI calls or active-index mutations."""
import json
import numpy as np
import pytest

from backend.rag.customer_index import (
    build_staging, verify_index, activate, rollback, CustomerHybridRetriever,
    validated_documents,
)


def fake_embed(texts):
    return np.asarray([[float(i + 1), 1., 0., 0.] for i, _ in enumerate(texts)])


def test_all_twelve_are_approved():
    assert len(validated_documents()) == 12


def test_offline_customer_only_index(tmp_path):
    stage = build_staging(fake_embed, staging_dir=tmp_path / 'stage')
    manifest, idx = verify_index(stage)
    assert idx.ntotal == 12
    assert {d['visibility'] for d in manifest['chunks']} == {'customer'}
    assert {d['approval'] for d in manifest['chunks']} == {'curated-v1'}
    retriever = CustomerHybridRetriever(index_dir=stage, embed_query=lambda _: [1., 1., 0., 0.])
    results = retriever.search('refund', k=4)
    assert 1 <= len(results) <= 4
    from backend.rag.customer_policy import is_approved_customer_passage
    assert all(is_approved_customer_passage(doc) for doc in results)
    with pytest.raises(PermissionError):
        retriever.search('internal', include_internal=True)


def test_tampering_fails_closed(tmp_path):
    stage = build_staging(fake_embed, staging_dir=tmp_path / 'stage')
    path = stage / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['chunks'][0]['text'] = 'INTERNAL SOP OVERRIDE'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_index(stage)


def test_activation_requires_approval_and_preserves_backup(tmp_path):
    active = tmp_path / 'active'
    first = build_staging(fake_embed, staging_dir=tmp_path / 'first')
    with pytest.raises(PermissionError):
        activate(first, active_dir=active)
    activate(first, active_dir=active, approved=True)
    second = build_staging(fake_embed, staging_dir=tmp_path / 'second')
    result = activate(second, active_dir=active, approved=True)
    assert result['backup']
    verify_index(result['backup'])
    rollback(active_dir=active, backup_dir=result['backup'], approved=True)
    verify_index(active)


def test_duplicate_identity_rejected(tmp_path, monkeypatch):
    import backend.rag.customer_index as module
    docs = validated_documents()
    monkeypatch.setattr(module, 'approved_passages', lambda: docs + [docs[0]])
    with pytest.raises(ValueError):
        module.validated_documents()

@pytest.fixture(autouse=True)
def mock_missing_optional_native_dependencies(monkeypatch):
    """Exercise orchestration offline even on machines without native FAISS wheels.

    On the user's Windows venv, the actual installed FAISS/BM25 will be used
    by the normal application; these test doubles deliberately mock both.
    """
    import sys
    import types
    import numpy as np
    faiss = types.ModuleType('faiss')
    class FlatIP:
        def __init__(self, dimension):
            self.d = dimension
            self.vectors = np.zeros((0, dimension), dtype='float32')
        @property
        def ntotal(self): return len(self.vectors)
        def add(self, vectors): self.vectors = np.asarray(vectors).copy()
        def search(self, q, k):
            scores = np.asarray(q) @ self.vectors.T
            order = np.argsort(-scores, axis=1)[:, :k]
            return np.take_along_axis(scores, order, axis=1), order
    def write_index(index, path):
        with open(path, 'wb') as f: np.save(f, index.vectors)
    def read_index(path):
        with open(path, 'rb') as f: vectors = np.load(f)
        index = FlatIP(vectors.shape[1]); index.add(vectors); return index
    def normalize_L2(a):
        a /= np.linalg.norm(a, axis=1, keepdims=True)
    faiss.IndexFlatIP = FlatIP
    faiss.write_index = write_index
    faiss.read_index = read_index
    faiss.normalize_L2 = normalize_L2
    bm = types.ModuleType('rank_bm25')
    class BM25:
        def __init__(self, docs): self.docs = docs
        def get_scores(self, query):
            return np.array([sum(d.count(term) for term in query) for d in self.docs])
    bm.BM25Okapi = BM25
    monkeypatch.setitem(sys.modules, 'faiss', faiss)
    monkeypatch.setitem(sys.modules, 'rank_bm25', bm)
