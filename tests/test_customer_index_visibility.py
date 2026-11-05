"""Consolidated customer index visibility tests.

Merged without behaviour changes from:
- test_phase2e4_customer_index.py: Fully offline Phase 2E.4 tests; no OpenAI calls or active-index mutations.
- test_customer_safe_passage_projection.py: Dependency-free tests for the customer-facing evidence projection.
- test_phase2e_visibility.py: Offline security tests; no FAISS, OpenAI key, or network required.
- test_phase2d3_citation_visibility.py: regression tests
"""
import json
import re
import numpy as np
import pytest
from backend.rag.customer_index import build_staging, verify_index, activate, rollback, CustomerHybridRetriever, validated_documents
import importlib.util
from pathlib import Path
from backend.services.evidence_quality import missing_citation_domains
from backend.services.grounding_guard import customer_safe_passages


# ======================================================================
# From test_phase2e4_customer_index.py
# ======================================================================

def fake_embed(texts):
    return np.asarray([[float(i + 1), 1., 0., 0.] for i, _ in enumerate(texts)])


@pytest.mark.usefixtures('mock_missing_optional_native_dependencies')
def test_all_curated_are_approved():
    assert len(validated_documents()) == len(__import__("backend.rag.customer_policy", fromlist=["approved_passages"]).approved_passages())


@pytest.mark.usefixtures('mock_missing_optional_native_dependencies')
def test_offline_customer_only_index(tmp_path):
    stage = build_staging(fake_embed, staging_dir=tmp_path / 'stage')
    manifest, idx = verify_index(stage)
    assert idx.ntotal == len(validated_documents())
    assert {d['visibility'] for d in manifest['chunks']} == {'customer'}
    assert {d['approval'] for d in manifest['chunks']} == {'curated-v1'}
    retriever = CustomerHybridRetriever(index_dir=stage, embed_query=lambda _: [1., 1., 0., 0.])
    results = retriever.search('refund', k=4)
    assert 0 <= len(results) <= 4
    from backend.rag.customer_policy import is_approved_customer_passage
    assert all(is_approved_customer_passage(doc) for doc in results)
    with pytest.raises(PermissionError):
        retriever.search('internal', include_internal=True)


@pytest.mark.usefixtures('mock_missing_optional_native_dependencies')
def test_tampering_fails_closed(tmp_path):
    stage = build_staging(fake_embed, staging_dir=tmp_path / 'stage')
    path = stage / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['chunks'][0]['text'] = 'INTERNAL SOP OVERRIDE'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_index(stage)


@pytest.mark.usefixtures('mock_missing_optional_native_dependencies')
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


@pytest.mark.usefixtures('mock_missing_optional_native_dependencies')
def test_duplicate_identity_rejected(tmp_path, monkeypatch):
    import backend.rag.customer_index as module
    docs = validated_documents()
    monkeypatch.setattr(module, 'approved_passages', lambda: docs + [docs[0]])
    with pytest.raises(ValueError):
        module.validated_documents()

@pytest.fixture
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
        def reconstruct(self, i): return self.vectors[i].copy()
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


# ======================================================================
# From test_customer_safe_passage_projection.py
# ======================================================================

path = Path(__file__).resolve().parents[1] / 'backend/services/grounding_guard.py'
spec = importlib.util.spec_from_file_location('customer_guard_under_test', path)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def passage(text, visibility='customer', source='01_Shipping_and_Delivery_SOP.pdf'):
    return {'id': 'shipping-1', 'source': source, 'visibility': visibility, 'text': text}


def test_sensitive_sentence_removed_without_losing_safe_sentence():
    original = passage('Customers can contact support about delayed delivery. We conduct driver GPS verification for investigations.')
    result = guard.customer_safe_passages([original])
    assert len(result) == 1
    assert result[0]['id'] == 'shipping-1'
    assert result[0]['text'] == 'Customers can contact support about delayed delivery.'
    assert 'GPS' in original['text']  # no mutation of cached RAG passage


def test_only_sensitive_text_is_not_exposed():
    assert guard.customer_safe_passages([passage('We conduct geofence audits.')]) == []


def test_internal_source_excluded_even_when_visibility_is_wrong():
    assert guard.customer_safe_passages([passage('Internal escalation instructions.', source='06_Escalations_and_Soft_Skills_SOP.pdf')]) == []


def test_non_customer_evidence_excluded():
    assert guard.customer_safe_passages([passage('Internal account procedure.', visibility='internal')]) == []


def test_safe_text_keeps_citation_id():
    result = guard.customer_safe_passages([passage('Customers may contact support for delivery questions.')])
    assert result[0]['id'] == 'shipping-1'
    assert guard.check_customer_visibility(result[0]['text']) is None


def test_rejects_invalid_retriever_data():
    import pytest
    with pytest.raises(ValueError):
        guard.customer_safe_passages([{'text': 'Missing ID'}])


# ======================================================================
# From test_phase2e_visibility.py
# ======================================================================

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "customer_policy_under_test", ROOT / "backend/rag/customer_policy.py"
)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def test_curated_excerpt_count():
    assert len(policy.approved_passages()) >= 10


def test_approved_excerpts_have_real_provenance():
    for passage in policy.approved_passages():
        # SOP PDFs, plus the one documented in-app help source for this UI.
        assert passage["source"].endswith(".pdf") or passage["source"] == "NovaCart_App_Help"
        assert passage["page"] >= 1
        assert policy.is_approved_customer_passage(passage)


def test_legacy_customer_label_not_enough():
    legacy = {"id": "legacy", "visibility": "customer", "text": "internal"}
    assert not policy.is_approved_customer_passage(legacy)


def test_forged_id_cannot_override_text():
    forged = policy.approved_passages()[0].copy()
    forged["text"] = "Internal discretionary compensation matrix"
    assert not policy.is_approved_customer_passage(forged)


def test_forged_id_cannot_override_page():
    forged = policy.approved_passages()[0].copy()
    forged["page"] = 4
    assert not policy.is_approved_customer_passage(forged)


def test_forged_id_cannot_override_source():
    forged = policy.approved_passages()[0].copy()
    forged["source"] = "06_Escalations_and_Soft_Skills_SOP.pdf"
    assert not policy.is_approved_customer_passage(forged)


def test_internal_content_not_in_approved_excerpts():
    content = " ".join(p["text"] for p in policy.approved_passages()).lower()
    for secret in ("kill_session_and_lock", "goodwill credit", "fraud score",
                   "geofence audit", "csr override", "trust metric",
                   "we have dispatched", "representment evidence"):
        assert secret not in content


def test_safe_return_policy_available():
    content = " ".join(p["text"] for p in policy.approved_passages()).lower()
    assert "7-day return window" in content
    assert "14-day return window" in content
    assert "photographs" in content


def test_no_raw_shipping_or_escalation_pages_approved():
    passages = policy.approved_passages()
    sources = {p["source"] for p in passages}
    # The approved catalog now includes a deliberately curated shipping
    # excerpt; this does not authorize arbitrary raw shipping SOP pages.
    shipping = [p for p in passages
                if p["source"] == "01_Shipping_and_Delivery_SOP.pdf"]
    assert shipping
    # Two curated excerpts: delivered-not-received (p3) and in-transit (p1,
    # approved 2026-11-05). Any other shipping page would be uncurated.
    assert {p["page"] for p in shipping} == {1, 3}
    assert len(shipping) == 2
    assert all(policy.is_approved_customer_passage(p) for p in shipping)
    assert not any(re.search(r"geofence|gps|manifest|fulfil", p["text"], re.I) for p in shipping)
    assert "06_Escalations_and_Soft_Skills_SOP.pdf" not in sources
    forged = dict(shipping[0], page=4)
    assert not policy.is_approved_customer_passage(forged)


def test_tampered_visibility_is_rejected():
    forged = policy.approved_passages()[0].copy()
    forged["visibility"] = "internal"
    assert not policy.is_approved_customer_passage(forged)


# ======================================================================
# From test_phase2d3_citation_visibility.py
# ======================================================================

def _p(id, source, page, text):
    return {"id": id, "source": source, "page": page, "visibility": "customer", "text": text}


def test_shipping_and_returns_require_both_domains():
    question = "How long does delivery take, and how can I return it?"
    draft = "The SOP does not specify a universal delivery time. Returns depend on category."
    returns = _p("r", "02_Returns_and_Refunds_SOP.pdf", 1, "Returns depend on category")
    shipping = _p("s", "01_Shipping_and_Delivery_SOP.pdf", 1, "Estimated Delivery Date (EDD)")
    assert missing_citation_domains(question, draft, [returns]) == ("shipping",)
    assert missing_citation_domains(question, draft, [returns, shipping]) == ()


def test_known_internal_pages_and_addendum_do_not_reach_agent():
    raw = [
        _p("bad", "01_Shipping_and_Delivery_SOP.pdf", 4, "Internal compensation macro"),
        _p("add", "02_Returns_and_Refunds_SOP.pdf", 5,
           "FIXFLOW AI | IMPLEMENTATION GUIDANCE ADDENDUM | Not a policy amendment"),
        _p("good", "02_Returns_and_Refunds_SOP.pdf", 1, "Fashion 14 Days"),
    ]
    assert [p["id"] for p in customer_safe_passages(raw)] == ["good"]


# ======================================================================
# Fresh-clone index build (scripts/rebuild_customer_index_staging.py)
# ======================================================================
def _fake_embedder(texts):
    rng = np.random.default_rng(7)
    return rng.normal(size=(len(texts), 1536)).astype('float32').tolist()


def _load_rebuild_script():
    from pathlib import Path as _Path
    path = _Path(__file__).resolve().parents[1] / 'scripts' / 'rebuild_customer_index_staging.py'
    spec = importlib.util.spec_from_file_location('rebuild_customer_index_staging', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_clone_index_build_uses_the_whole_catalog(tmp_path):
    from backend.rag.customer_index import verify_index
    from backend.rag.customer_policy import approved_passages
    script = _load_rebuild_script()
    active = tmp_path / 'local_customer_index'
    assert script.build(_fake_embedder, active_dir=active, activate_now=True) == active
    manifest, index = verify_index(active)
    assert index.ntotal == len(manifest['chunks']) == len(approved_passages())
    assert manifest['kind'] == 'approved-customer-only-v1'


def test_rebuild_keeps_a_backup_and_build_only_mode_does_not_activate(tmp_path):
    script = _load_rebuild_script()
    active = tmp_path / 'local_customer_index'
    script.build(_fake_embedder, active_dir=active, activate_now=True)
    staged = script.build(_fake_embedder, active_dir=active, activate_now=False)
    assert staged != active and staged.is_dir()
    script.build(_fake_embedder, active_dir=active, activate_now=True)
    assert any(p.name.startswith('local_customer_index.backup-') for p in tmp_path.iterdir())


def test_rebuild_script_has_no_hardcoded_passage_count():
    from pathlib import Path as _Path
    source = (_Path(__file__).resolve().parents[1] / 'scripts' / 'rebuild_customer_index_staging.py').read_text(encoding='utf-8')
    assert '!= 13' not in source and '!= 14' not in source
