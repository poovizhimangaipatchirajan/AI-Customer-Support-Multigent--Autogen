"""Phase 2E.4: isolated, approved-only customer hybrid index.

Staging and activation are deliberately separate operations. Neither is invoked
by application startup. The embedding provider is injected for offline testing.
"""
import hashlib
import json
import os
import shutil
import re
import uuid
from pathlib import Path

from backend.rag.customer_policy import approved_passages, is_approved_customer_passage

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = ROOT / 'local_customer_index'
MODEL = 'text-embedding-3-small'
RRF_K = 60

def tokenize(value):
    return re.findall(r"\w+", value.lower(), flags=re.UNICODE)


def _digest(documents):
    return hashlib.sha256(json.dumps(documents, sort_keys=True).encode('utf-8')).hexdigest()


def validated_documents():
    docs = approved_passages()
    if len(docs) != len(approved_passages()) or len({d['id'] for d in docs}) != len(docs):
        raise ValueError('Approved customer passage count/identity mismatch')
    if not all(is_approved_customer_passage(d) for d in docs):
        raise ValueError('Unapproved customer passage')
    return docs


def _validate_vectors(vectors, count):
    import numpy as np
    arr = np.asarray(vectors, dtype='float32')
    if arr.ndim != 2 or arr.shape[0] != count or arr.shape[1] < 1:
        raise ValueError('Embedding shape mismatch')
    if not np.isfinite(arr).all() or (np.linalg.norm(arr, axis=1) == 0).any():
        raise ValueError('Invalid embedding vectors')
    return arr


def build_staging(embed_documents, *, staging_dir, model=MODEL):
    """Create isolated staged artifacts. embed_documents(texts) is injected.

    NEVER call this with a paid embedding implementation without user approval.
    """
    import faiss
    docs = validated_documents()
    stage = Path(staging_dir)
    if stage.exists():
        raise FileExistsError(f'Staging directory already exists: {stage}')
    stage.mkdir(parents=True)
    try:
        vectors = _validate_vectors(embed_documents([d['text'] for d in docs]), len(docs))
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        manifest = {'kind': 'approved-customer-only-v1', 'model': model,
                    'dimension': int(vectors.shape[1]), 'chunks': docs,
                    'digest': _digest(docs)}
        faiss.write_index(index, str(stage / 'dense.faiss'))
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        verify_index(stage)
        return stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def verify_index(directory):
    """Fail closed on unapproved content, malformed metadata or vector mismatch."""
    import faiss
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    docs = manifest['chunks']
    if (manifest.get('kind') != 'approved-customer-only-v1' or
        manifest.get('digest') != _digest(docs) or
        len(docs) != len(approved_passages()) or len({d.get('id') for d in docs}) != len(docs) or
        {d['id'] for d in docs} != {d['id'] for d in validated_documents()} or
        not all(is_approved_customer_passage(d) for d in docs)):
        raise ValueError('Customer index approval/integrity validation failed')
    idx = faiss.read_index(str(directory / 'dense.faiss'))
    if idx.ntotal != len(docs) or idx.d != manifest['dimension']:
        raise ValueError('Customer index vector/manifest mismatch')
    return manifest, idx


def activate(staging_dir, *, active_dir=DEFAULT_INDEX, backup_dir=None, approved=False):
    """Explicit, offline atomic directory activation; stop API/workers first.

    Caller must separately verify approval and service shutdown. A rollback
    backup is retained; never silently replace an existing backup.
    """
    if approved is not True:
        raise PermissionError('Explicit human approval required for index activation')
    stage, active = Path(staging_dir), Path(active_dir)
    verify_index(stage)
    if not stage.is_dir() or stage.resolve() == active.resolve():
        raise ValueError('Invalid staging directory')
    backup = Path(backup_dir) if backup_dir else active.with_name(active.name + '.backup-' + uuid.uuid4().hex[:12])
    if backup.exists():
        raise FileExistsError('Backup destination already exists')
    active.parent.mkdir(parents=True, exist_ok=True)
    if stage.stat().st_dev != active.parent.stat().st_dev:
        raise ValueError('Staging and active directory must share a filesystem')
    had_active = active.exists()
    if had_active:
        # Preserve old index before swapping; old index may use legacy format.
        os.replace(active, backup)
    try:
        os.replace(stage, active)
        verify_index(active)
    except BaseException:
        if active.exists():
            failed = active.with_name(active.name + '.failed-' + uuid.uuid4().hex[:12])
            os.replace(active, failed)
        if had_active:
            os.replace(backup, active)
        raise
    return {'active': str(active), 'backup': str(backup) if had_active else None}


def rollback(*, active_dir=DEFAULT_INDEX, backup_dir, approved=False):
    """Explicit rollback. Stop API/workers before calling."""
    if approved is not True:
        raise PermissionError('Explicit human approval required for rollback')
    active, backup = Path(active_dir), Path(backup_dir)
    if not backup.is_dir():
        raise FileNotFoundError('Rollback backup not found')
    # Do not delete the current index; retain it for investigation.
    displaced = active.with_name(active.name + '.rolled-back-' + uuid.uuid4().hex[:12])
    if active.exists():
        os.replace(active, displaced)
    try:
        os.replace(backup, active)
    except BaseException:
        if displaced.exists():
            os.replace(displaced, active)
        raise
    return {'active': str(active), 'displaced': str(displaced) if displaced.exists() else None}


class CustomerHybridRetriever:
    """FAISS + BM25 with RRF, using only verified customer-approved documents."""
    def __init__(self, *, index_dir=DEFAULT_INDEX, embed_query=None):
        from rank_bm25 import BM25Okapi
        self.manifest, self.index = verify_index(index_dir)
        self.docs = self.manifest['chunks']
        self.bm = BM25Okapi([tokenize(d['text']) for d in self.docs])
        self.embed_query = embed_query

    def search(self, query, k=4, include_internal=False):
        if include_internal:
            raise PermissionError('Customer retrieval cannot expose internal SOPs')
        if not isinstance(query, str) or not query.strip() or k <= 0:
            return []
        from backend.rag.customer_policy import approved_mfa_guidance
        mfa_evidence = approved_mfa_guidance(query)
        if mfa_evidence is not None:
            return mfa_evidence[:k]

        # Conservative pre-embedding relevance check. An offline embedding
        # callback may return a valid policy vector for *any* input, so dense
        # similarity alone cannot reject clearly out-of-domain questions.
        # Use the approved catalog only; never consult internal SOP content.
        from backend.rag.semantic_rank import meaningful_tokens

        def normalize_relevance_terms(terms):
            aliases = {
                "covered": "cover",
                "covering": "cover",
                "covers": "cover",
                "coverage": "cover",
            }
            return {aliases.get(term, term) for term in terms}

        query_terms = normalize_relevance_terms(meaningful_tokens(query))

        approved_terms = set().union(
            *(
                normalize_relevance_terms(meaningful_tokens(d["text"]))
                for d in self.docs
            )
        )
        # No lexical evidence at all: abstain instead of making a paid API call.
        # This is deliberately conservative; synonym-only questions may require
        # a future reviewed vocabulary/semantic-intent fallback.
        if not query_terms or not (query_terms & approved_terms):
            return []

        if self.embed_query is None:
            raise RuntimeError('Query embedding provider not configured')
        from backend.rag.semantic_rank import rank_approved
        vector = self.embed_query(query)
        # The approved-only index already contains exactly the validated catalog.
        # Reconstruct approved vectors for shared hybrid fusion and semantic
        # evidence validation. No question-specific routing or lexical gate.
        document_vectors = [self.index.reconstruct(i) for i in range(len(self.docs))]
        return rank_approved(query, self.docs, vector, document_vectors, k=k)
